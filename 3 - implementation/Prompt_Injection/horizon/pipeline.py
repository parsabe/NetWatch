"""End-to-end Horizon pipeline as an AgentDojo pipeline (§4), with every ablation flag.

user prompt u
 → [direct]   LlamaFirewall / Prompt Guard 2 on u                 (use_firewall)
 → [Stage 2]  CaMeL P-LLM sees only u → program → interpreter     (use_stage2)
              Q-LLM × m, per-run τ ~ π*, fresh κ [Stage 1]          (use_stage1, m, use_vote, use_typing)
              policy table, Z3-verified at startup                  (use_policy)
              CONFIRM → CRC threshold λ̂                             (use_conformal)
 → [direct]   Llama Guard + canary on the final output             (use_output_guard, use_canary)

With use_stage2 = False the agent is a plain tool-calling loop; if use_stage1 is on,
every tool output is spotlighted with τ ~ π* and a fresh κ (Stage-1-only ablation).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import openai
from agentdojo import agent_pipeline, functions_runtime
from agentdojo import types as ad_types
from agentdojo.agent_pipeline.agent_pipeline import load_system_message
from agentdojo.agent_pipeline.errors import AbortAgentError

from horizon.config import ROOT, Config
from horizon.direct import canary as canary_mod
from horizon.direct.guards import LlamaGuardOutput, PromptGuardInput
from horizon.llm import LLM
from horizon.stage1.transforms import build_variants
from horizon.stage2.camel_adapter import EpisodeState, HorizonPolicyEngine, HorizonPrivilegedLLM, tool_signatures
from horizon.stage2.policy import PolicyTable
from horizon.stage2.quarantine import Mix, Quarantine
from horizon.stage2.z3_check import assert_noninterference

PI_STAR = ROOT / "results" / "stage1" / "pi_star.json"
LAMBDA_HAT = ROOT / "results" / "stage2" / "lambda_hat.json"


def resolve_mix(cfg: Config) -> Mix | None:
    """π used by the quarantine: None (no Stage 1), a single variant, or π* from the Stage 1 fit."""
    f = cfg.flags
    if not f.use_stage1:
        return None
    variants = build_variants(cfg.stage1)
    if f.stage1_variant:
        return Mix(variants, {f.stage1_variant: 1.0})
    path = PI_STAR if f.stage1_mix == "pi_star" else Path(f.stage1_mix)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run the Stage 1 fit (eval/stage1_runner.py --fit) or set "
                                "flags.stage1_variant")
    return Mix(variants, json.loads(path.read_text())["pi_star"])


def resolve_lambda(cfg: Config) -> float | None:
    if not cfg.flags.use_conformal:
        return None
    if not LAMBDA_HAT.exists():
        raise FileNotFoundError(f"{LAMBDA_HAT} not found: calibrate first (eval/agentdojo_runner.py --calibrate) "
                                "or run with flags.use_conformal=false")
    return float(json.loads(LAMBDA_HAT.read_text())["lambda_hat"])


class InputGuard(agent_pipeline.BasePipelineElement):
    name = "input_guard"

    def __init__(self, guard: PromptGuardInput, log: list):
        self.guard, self.log = guard, log

    def query(self, query, runtime, env=functions_runtime.EmptyEnv(), messages=(), extra_args={}):
        v = self.guard.check(query)
        self.log.append({"layer": "firewall", "blocked": v.blocked, "score": v.score, "reason": v.reason})
        if v.blocked:
            raise AbortAgentError(f"Blocked by {v.detector}", list(messages), env)
        return query, runtime, env, messages, extra_args


class OutputGuard(agent_pipeline.BasePipelineElement):
    name = "output_guard"
    BLOCKED = "[Horizon: response withheld by the output guard]"

    def __init__(self, guard: LlamaGuardOutput | None, token: str | None, log: list):
        self.guard, self.token, self.log = guard, token, log

    def query(self, query, runtime, env=functions_runtime.EmptyEnv(), messages=(), extra_args={}):
        if not messages or messages[-1]["role"] != "assistant" or not messages[-1]["content"]:
            return query, runtime, env, messages, extra_args
        text = ad_types.get_text_content_as_str(messages[-1]["content"])
        blocked, why = False, ""
        if self.token and canary_mod.leaked(text, self.token):
            blocked, why = True, "canary"
        elif self.guard is not None:
            v = self.guard.check(query, text)
            blocked, why = v.blocked, v.reason
        self.log.append({"layer": "output_guard", "blocked": blocked, "reason": why})
        if blocked:
            msg = ad_types.ChatAssistantMessage(role="assistant", tool_calls=None,
                                                content=[ad_types.text_content_block_from_string(self.BLOCKED)])
            messages = [*messages[:-1], msg]
        return query, runtime, env, messages, extra_args


def _spotlight_formatter(mix: Mix):
    """Stage-1-only ablation: transform each tool output with τ ~ π*, fresh κ; the key note is app-authored."""
    from agentdojo.agent_pipeline.tool_execution import tool_result_to_str

    def fmt(result) -> str:
        variant, key = mix.draw()
        body = variant.transform(tool_result_to_str(result), key)
        return f"[Tool output from a third party. {variant.system_note(key)}]\n{body}"
    return fmt


@dataclass
class Built:
    pipeline: agent_pipeline.AgentPipeline
    state: EpisodeState
    guard_log: list
    canary: str | None


def build_pipeline(cfg: Config, suite, role: str | None = None) -> Built:
    """Assemble the pipeline for one AgentDojo suite and run the Z3 proof (refuses to start on failure)."""
    f, s2 = cfg.flags, cfg.stage2
    runtime = functions_runtime.FunctionsRuntime(suite.tools)
    policy = PolicyTable.load(s2["policy"])
    if f.use_policy:
        assert_noninterference(policy, tool_signatures(runtime))
    state, log = EpisodeState(), []
    token = canary_mod.new_token(cfg.direct["canary_hex_bytes"]) if f.use_canary else None
    elements: list = [agent_pipeline.InitQuery()]
    if f.use_firewall:
        elements.insert(0, InputGuard(PromptGuardInput(cfg.direct), log))
    mix = resolve_mix(cfg)
    if f.use_stage2:
        q = Quarantine(LLM(cfg.models["q_llm"], cfg.models), mix, s2["parser"], s2,
                       use_typing=f.use_typing, use_vote=f.use_vote)
        lam = resolve_lambda(cfg)
        tool_names = set(tool_signatures(runtime))
        role = role or policy.default_role
        factory = lambda env: HorizonPolicyEngine(env, policy if f.use_policy else None, tool_names, state,  # noqa: E731
                                                  lam, role, s2)
        elements.append(HorizonPrivilegedLLM(LLM(cfg.models["p_llm"], cfg.models), q, f.m, factory, state, token,
                                             s2["p_llm_max_attempts"]))
    else:
        client = openai.OpenAI(base_url=f"http://127.0.0.1:{cfg.models['proxy_port']}/v1", api_key="local")
        llm = agent_pipeline.OpenAILLM(client, cfg.models["p_llm"].split("/", 1)[1], temperature=cfg.models["temperature"])
        sysmsg = load_system_message(None)
        if token:
            sysmsg = canary_mod.with_canary(sysmsg, token)
        executor = (agent_pipeline.ToolsExecutor(_spotlight_formatter(mix)) if mix
                    else agent_pipeline.ToolsExecutor())
        elements = [agent_pipeline.SystemMessage(sysmsg), *elements, llm,
                    agent_pipeline.ToolsExecutionLoop([executor, llm])]
    if f.use_output_guard or f.use_canary:
        elements.append(OutputGuard(LlamaGuardOutput(cfg.direct) if f.use_output_guard else None, token, log))
    pipe = agent_pipeline.AgentPipeline(elements)
    pipe.name = ablation_name(cfg)
    return Built(pipe, state, log, token)


def ablation_name(cfg: Config) -> str:
    f = cfg.flags
    parts = ["local-horizon"]       # "local" ⇒ AgentDojo attacks address the model as "Local model"
    parts += ["fw"] if f.use_firewall else []
    parts += [f"s1-{f.stage1_variant or f.stage1_mix}"] if f.use_stage1 else []
    parts += [f"s2-m{f.m}" + ("" if f.use_vote else "-novote") + ("" if f.use_typing else "-notype")
              + ("" if f.use_policy else "-nopol") + ("" if f.use_conformal else "-nocrc")] if f.use_stage2 else []
    parts += ["og"] if f.use_output_guard else []
    parts += ["can"] if f.use_canary else []
    return "+".join(parts)
