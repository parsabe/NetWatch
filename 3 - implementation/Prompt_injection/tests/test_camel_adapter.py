"""Capability labels derived from CaMeL's source tracking (Slide 37) and the spotlighting of Q-LLM inputs."""
from camel.capabilities import sources as S

from horizon.stage1.transforms import build_variants
from horizon.config import load_config
from horizon.stage2.camel_adapter import _source_cap
from horizon.stage2.quarantine import spotlight
from horizon.stage2.values import Cap


def test_user_source_is_user():
    assert _source_cap(S.SourceEnum.User) == Cap.USER


def test_camel_and_trusted_tool_sources_are_system():
    assert _source_cap(S.SourceEnum.CaMeL) == Cap.SYSTEM
    assert _source_cap(S.Tool("get_current_day", frozenset({S.SourceEnum.TrustedToolSource}))) == Cap.SYSTEM


def test_tool_output_without_trusted_inner_source_is_untrusted():
    assert _source_cap(S.Tool("read_channel_messages", frozenset())) == Cap.UNTRUSTED
    assert _source_cap("mallory@evil.example") == Cap.UNTRUSTED       # a third-party participant


def test_tool_with_user_inner_source_is_user():
    assert _source_cap(S.Tool("get_user_info", frozenset({S.SourceEnum.User}))) == Cap.USER


def test_spotlight_transforms_only_untrusted_spans():
    v = build_variants(load_config().stage1)["base64"]
    query = "Extract the date from this email: Hi, meet on Friday."
    out = spotlight(query, ["Hi, meet on Friday."], v, None)
    assert out.startswith("Extract the date from this email: ")
    assert "Friday" not in out
