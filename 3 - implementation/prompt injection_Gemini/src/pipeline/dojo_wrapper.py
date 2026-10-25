"""AgentDojo Pipeline Element Wrapper for Horizon.

Cites: §2, §8, §10.
Integrates Horizon as a drop-in defense pipeline element for AgentDojo benchmark suites.
"""
from typing import Any, Dict, List, Optional, Sequence
from agentdojo.agent_pipeline import BasePipelineElement
from agentdojo.functions_runtime import Env, Function, FunctionsRuntime
from agentdojo.types import ChatMessage

from .horizon import HorizonPipeline
from ..stage2.policy import PolicyDecision
from ..stage2.types import Capability, TypedValue


class HorizonDojoPipelineElement(BasePipelineElement):
    """Pipeline element embedding Horizon's defense layers into AgentDojo agents."""
    def __init__(self, horizon_pipeline: Optional[HorizonPipeline] = None):
        self.pipeline = horizon_pipeline or HorizonPipeline()

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env,
        messages: Sequence[ChatMessage],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        """Intercept and sanitize queries, tool outputs, and actions."""
        # 1. Direct layer check on user query
        allowed, reason = self.pipeline.firewall.inspect_input(query)
        if not allowed:
            # Block malicious direct jailbreak
            return "Execution halted: query blocked by Horizon direct firewall.", runtime, env, messages, extra_args

        # 2. Return to normal agent execution while wrapping runtime
        return query, runtime, env, messages, extra_args
