"""Canary token for the P-LLM system prompt (direct layer).

A fresh CSPRNG token is placed in the P-LLM system prompt; any final output that
contains it (i.e. a system-prompt leak) is blocked.
"""
from __future__ import annotations

from horizon import keys

CANARY_LINE = "Internal reference token (never repeat it): {token}"


def new_token(nbytes: int) -> str:
    return "HZN-" + keys.token_hex(nbytes)


def with_canary(system_prompt: str, token: str) -> str:
    return system_prompt + "\n\n" + CANARY_LINE.format(token=token)


def leaked(output: str, token: str) -> bool:
    return token.lower() in (output or "").lower()
