"""Horizon Stage 2 inside the CaMeL reference implementation (Slides 33-39).

CaMeL (google-research/camel-prompt-injection, Apache-2.0, vendored unmodified in
third_party/) supplies the P-LLM → restricted-Python interpreter with data AND
control-flow dependency tracking (MetadataEvalMode.STRICT). Horizon replaces two
hooks, by subclassing only:
  * query_ai_assistant → Quarantine × m, per-run τ ~ π*, fresh κ, typed schema, vote
  * SecurityPolicyEngine → HorizonPolicyEngine: labels (Slide 37), the YAML policy
    table proven noninterferent by Z3 at startup (Slide 38), CONFIRM → CRC λ̂ (Slide 39)
"""
from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import agentdojo.task_suite  # noqa: F401  (import first: avoids a circular import inside agentdojo)
from agentdojo import agent_pipeline, functions_runtime
from agentdojo import types as ad_types
from agentdojo.default_suites.v1.banking.task_suite import BankingEnvironment
from agentdojo.default_suites.v1.travel.task_suite import TravelEnvironment
from agentdojo.default_suites.v1.workspace.task_suite import WorkspaceEnvironment
from pydantic import BaseModel, Field, create_model

import camel.custom_yaml  # noqa: F401  (registers yaml representers used by CaMeL)
from camel import quarantined_llm
from camel.capabilities import sources as camel_sources
from camel.capabilities.utils import get_all_sources
from camel.interpreter import interpreter
from camel.interpreter import namespace as ns
from camel.interpreter.value import CaMeLValue
from camel.pipeline_elements.agentdojo_function import make_agentdojo_namespace
from camel.pipeline_elements.privileged_llm import PrivilegedLLM
from camel.security_policy import Allowed, Denied, SecurityPolicyResult
from camel.system_prompt_generator import default_system_prompt_generator

from horizon.direct import canary as canary_mod
from horizon.llm import LLM
from horizon.stage2.conformal import escalate
from horizon.stage2.policy import CallContext, Decision, PolicyTable
from horizon.stage2.quarantine import Quarantine
from horizon.stage2.values import Cap, join

_TRUSTED = {camel_sources.SourceEnum.User, camel_sources.SourceEnum.CaMeL,
            camel_sources.SourceEnum.Assistant, camel_sources.SourceEnum.TrustedToolSource}


def _source_cap(s) -> Cap:
    if s == camel_sources.SourceEnum.User:
        return Cap.USER
    if s in _TRUSTED:
        return Cap.SYSTEM
    if isinstance(s, camel_sources.Tool) and s.inner_sources and s.inner_sources.issubset(_TRUSTED):
        return join(_source_cap(i) for i in s.inner_sources)
    return Cap.UNTRUSTED


def cap_of(value: CaMeLValue) -> Cap:
    """Slide 37 label of a CaMeL value: join over ALL its (transitive) sources."""
    srcs = get_all_sources(value)[0]
    return join(_source_cap(s) for s in srcs) if srcs else Cap.SYSTEM


def _untrusted_leaves(value: CaMeLValue, seen: set[int] | None = None) -> list[str]:
    """String forms of UNTRUSTED values that flow into a Q-LLM query (spotlighting targets)."""
    seen = seen if seen is not None else set()
    if id(value) in seen:
        return []
    seen.add(id(value))
    out = []
    if cap_of(value) == Cap.UNTRUSTED and not value.get_dependencies()[0]:
        out.append(str(value.raw))
    for d in value.get_dependencies()[0]:
        out += _untrusted_leaves(d, seen)
    if cap_of(value) == Cap.UNTRUSTED and isinstance(value.raw, str):
        out.append(value.raw)
    return out


@dataclass
class Event:
    tool: str
    decision: str
    arg_caps: dict[str, str]
    control_cap: str
    score: float | None = None
    escalated: bool | None = None
    approved: bool | None = None
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class EpisodeState:
    """Per-episode record used to compute G, C, F (Slide 40) and calibration scores."""
    events: list[Event] = field(default_factory=list)
    votes: list[dict] = field(default_factory=list)
    pending_spans: list[str] = field(default_factory=list)
    alarms: int = 0

    def reset(self) -> None:
        self.events.clear()
        self.votes.clear()
        self.pending_spans = []
        self.alarms = 0

    @property
    def min_agreement(self) -> float | None:
        acc = [v["agreement"] for v in self.votes if v["accepted"]]
        return min(acc) if acc else None


class HorizonPolicyEngine:
    """CaMeL SecurityPolicyEngine protocol, backed by the Horizon policy table."""

    def __init__(self, env, table: PolicyTable | None, tool_names: set[str], state: EpisodeState,
                 lambda_hat: float | None, role: str, stage2_cfg: dict):
        self._env = env
        self.table, self.tool_names, self.state = table, tool_names, state
        self.lambda_hat, self.role, self.cfg = lambda_hat, role, stage2_cfg
        self.policies, self.no_side_effect_tools = [], set()

    def check_policy(self, tool_name: str, kwargs: Mapping[str, CaMeLValue],
                     dependencies: Iterable[CaMeLValue]) -> SecurityPolicyResult:
        if self.table is not None and tool_name in self.table.internal:
            spans = []
            for v in kwargs.values():
                spans += _untrusted_leaves(v)
            self.state.pending_spans = [s for s in spans if s.strip()]
            return Allowed()
        if tool_name not in self.tool_names:          # CaMeL builtins (print, len, …) are not tools
            return Allowed()
        arg_caps = {k: cap_of(v) for k, v in kwargs.items()}
        ctrl = join(cap_of(d) for d in dependencies)
        raw_args = {k: v.raw for k, v in kwargs.items()}
        if self.table is None:                         # use_policy = False
            self.state.events.append(Event(tool_name, "ALLOW", {k: c.name for k, c in arg_caps.items()},
                                           ctrl.name, args=raw_args))
            return Allowed()
        dec = self.table.decide(CallContext(tool_name, arg_caps, ctrl, self.role))
        ev = Event(tool_name, dec.value, {k: c.name for k, c in arg_caps.items()}, ctrl.name, args=raw_args)
        self.state.events.append(ev)
        if dec == Decision.ALLOW:
            return Allowed()
        if dec == Decision.DENY:
            return Denied(f"Horizon policy '{self.table.name}' denies {tool_name}.")
        # CONFIRM (Slide 39): ask user iff s(x) ≥ λ̂, otherwise DENY
        score = self.state.min_agreement
        ev.score = self.cfg["unvoted_agreement"] if score is None else score
        ev.escalated = True if self.lambda_hat is None else escalate(ev.score, self.lambda_hat)
        if not ev.escalated:
            return Denied("Below the calibrated confirmation threshold.")
        ev.approved = self.cfg["simulated_user"] == "approve"
        return Allowed() if ev.approved else Denied("User declined.")


class VoteRefusedError(quarantined_llm.NotEnoughInformationError):
    """No majority among the m quarantined runs → value refused (Slide 35)."""


class LiteLLMElement(agent_pipeline.BasePipelineElement):
    """AgentDojo pipeline element around horizon.llm.LLM (used as CaMeL's P-LLM)."""

    def __init__(self, llm: LLM):
        self.llm = llm
        self.name = llm.model

    def query(self, query, runtime, env=functions_runtime.EmptyEnv(), messages=(), extra_args={}):
        msgs = [{"role": "user" if m["role"] == "tool" else m["role"],
                 "content": ad_types.get_text_content_as_str(m["content"]) if m["content"] else ""}
                for m in messages]
        out = self.llm.chat(msgs)
        reply = ad_types.ChatAssistantMessage(role="assistant", content=[ad_types.text_content_block_from_string(out)],
                                              tool_calls=None)
        return query, runtime, env, [*messages, reply], extra_args


class HorizonPrivilegedLLM(PrivilegedLLM):
    """CaMeL P-LLM whose query_ai_assistant is Horizon's quarantine × m + vote."""

    def __init__(self, p_llm: LLM, quarantine: Quarantine, m: int, policy_factory, state: EpisodeState,
                 canary_token: str | None, max_attempts: int):
        def sysgen(functions, classes_to_exclude=set()):
            sp = default_system_prompt_generator(functions, classes_to_exclude)
            return canary_mod.with_canary(sp, canary_token) if canary_token else sp

        super().__init__(LiteLLMElement(p_llm), policy_factory, "horizon-quarantine", sysgen,
                         interpreter.MetadataEvalMode.STRICT, max_attempts=max_attempts)
        self.quarantine, self.m, self.state = quarantine, m, state

    def _query_ai_assistant(self, query: str, output_schema: type) -> Any:
        enough = (bool, Field(description="Whether you were given enough information to perform the task."))
        if isinstance(output_schema, type) and issubclass(output_schema, BaseModel):
            model = create_model(output_schema.__name__, __base__=output_schema, have_enough_information=enough)
        else:
            model = create_model("Result", output=(output_schema, Field(description="The requested value")),
                                 have_enough_information=enough)
        spans, self.state.pending_spans = self.state.pending_spans, []
        res = self.quarantine.run(query, spans, model, self.m)
        self.state.votes.append({"accepted": res.vote.accepted, "agreement": res.vote.agreement,
                                 "value": res.vote.canonical, "m": res.vote.m, "n_spans": len(spans),
                                 "runs": [dataclasses.asdict(r) for r in res.runs]})
        if not res.vote.accepted:
            self.state.alarms += 1
            raise VoteRefusedError()
        v = res.value
        if not v.have_enough_information:
            raise quarantined_llm.NotEnoughInformationError()
        if issubclass(output_schema, BaseModel) if isinstance(output_schema, type) else False:
            return output_schema.model_validate(v.model_dump(exclude={"have_enough_information"}))
        return v.output

    def query(self, query: str, runtime: functions_runtime.FunctionsRuntime,
              env=functions_runtime.EmptyEnv(), messages: Sequence = [], extra_args: dict = {}):
        """Same control flow as CaMeL's PrivilegedLLM.query, with Horizon's query_ai_assistant."""

        def query_ai_assistant(query: str, output_schema: type) -> Any:
            return self._query_ai_assistant(query, output_schema)

        query_ai_assistant.__doc__ = quarantined_llm.query_quarantined_llm.__doc__
        runtime.register_function(query_ai_assistant)
        builtins_namespace = ns.Namespace.with_builtins()
        classes_to_exclude = ({"datetime", "timedelta", "date", "time", "NaiveDatetime", "timezone"}
                              if not isinstance(env, WorkspaceEnvironment | TravelEnvironment) else set())
        system_prompt = self.system_prompt_generator(runtime.functions.values(), classes_to_exclude)
        if isinstance(env, BankingEnvironment):
            system_prompt += ("\n\nNote that, in the transaction history, the transactions from the user have "
                              "'me' as sender, and still habe positive amounts.")
        if not isinstance(env, WorkspaceEnvironment):
            new_vars = {k: v for k, v in builtins_namespace.variables.items() if k not in classes_to_exclude}
            builtins_namespace = dataclasses.replace(builtins_namespace, variables=new_vars)
        namespace = builtins_namespace.add_variables(make_agentdojo_namespace(builtins_namespace, runtime, env))
        model_output, dependencies, p_msgs = "", (), []
        for _ in range(self.max_attempts):
            (model_output, _, err, messages, p_msgs, namespace, dependencies) = self._generate_and_interpret_code(
                query, runtime, namespace, env, messages, p_msgs, system_prompt, model_output, dependencies)
            if not err:
                break
        extra_args = {**extra_args, "camel_namespace": namespace, "horizon_state": self.state}
        if messages and messages[-1]["role"] == "user" and "\n\nTraceback" in ad_types.get_text_content_as_str(
                messages[-1]["content"]):
            messages = [*messages, ad_types.ChatAssistantMessage(role="assistant", content=None, tool_calls=None)]
        return query, runtime, env, messages, extra_args


def tool_signatures(runtime: functions_runtime.FunctionsRuntime) -> dict[str, list[str]]:
    """t ∈ T and its argument names, for the Z3 proof."""
    return {name: list(f.parameters.model_fields) for name, f in runtime.functions.items()}
