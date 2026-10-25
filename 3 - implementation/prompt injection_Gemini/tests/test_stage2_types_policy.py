"""Unit tests for Stage 2 types, capability propagation, and policy evaluation.

Cites: Slide 38, Slide 39.
"""
import pytest
from src.stage2.policy import PolicyDecision, PolicyEngine
from src.stage2.types import Capability, SchemaParser, TypedValue


def test_capability_propagation():
    """Verify most restrictive capability propagation across data and control flow (Slide 38)."""
    val_user = TypedValue("alice@example.com", "email", Capability.USER)
    val_untrusted = TypedValue("malicious payload", "string", Capability.UNTRUSTED)

    assert not val_user.is_untrusted()
    assert val_untrusted.is_untrusted()

    # Derived value from trusted and untrusted inputs must be UNTRUSTED
    derived = val_user.derive("derived data", "string", val_untrusted)
    assert derived.cap == Capability.UNTRUSTED
    assert derived.is_untrusted()


def test_schema_parser_sanitization():
    """Verify SchemaParser accepts valid structured values and rejects injection payloads."""
    # Valid date
    assert SchemaParser.parse_date("2026-10-25") == "2026-10-25"
    # Injection attempting to masquerade as date
    with pytest.raises(ValueError):
        SchemaParser.parse_date("2026-10-25; DROP TABLE users; --")

    # Valid email
    assert SchemaParser.parse_email("test@example.org") == "test@example.org"
    with pytest.raises(ValueError):
        SchemaParser.parse_email("not an email address")

    # Valid int
    assert SchemaParser.parse_int("42") == 42
    with pytest.raises(ValueError):
        SchemaParser.parse_int("42 && cat /etc/passwd")


def test_policy_engine_evaluation():
    """Verify PolicyEngine enforces noninterference and executes rule evaluations."""
    engine = PolicyEngine()

    # Trusted inputs to send_email
    v_recipient = TypedValue("bob@example.com", "email", Capability.USER)
    v_body = TypedValue("Status update", "string", Capability.USER)
    res_trusted = engine.evaluate(
        tool_name="send_email",
        args={"recipient": v_recipient, "body": v_body},
        control_flow=Capability.USER,
        user_role="user",
    )
    # Default is CONFIRM for side-effect tool
    assert res_trusted == PolicyDecision.CONFIRM

    # Untrusted control flow to send_email -> DENY
    res_cf_untrusted = engine.evaluate(
        tool_name="send_email",
        args={"recipient": v_recipient, "body": v_body},
        control_flow=Capability.UNTRUSTED,
    )
    assert res_cf_untrusted == PolicyDecision.DENY

    # Read file on untrusted control flow -> CONFIRM (never ALLOW)
    res_rf_untrusted = engine.evaluate(
        tool_name="read_file",
        args={},
        control_flow=Capability.UNTRUSTED,
    )
    assert res_rf_untrusted != PolicyDecision.ALLOW
