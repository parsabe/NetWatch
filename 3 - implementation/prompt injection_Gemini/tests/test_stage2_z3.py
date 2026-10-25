"""Unit tests for Z3 Noninterference Verifier.

Cites: Slide 39; Ground Rules §9.
"""
import pytest
from src.stage2.z3_verifier import NoninterferenceViolationError, verify_policy_noninterference


def test_z3_verifies_correct_policy():
    """Verify that a compliant policy passes Z3 verification."""
    compliant_policy = {
        "tools": {
            "send_email": {
                "default_action": "CONFIRM",
                "rules": [
                    {"condition": "control_flow == 'UNTRUSTED'", "action": "DENY"},
                    {"condition": "any_arg == 'UNTRUSTED'", "action": "CONFIRM"},
                ],
            },
            "read_file": {
                "default_action": "ALLOW",
                "rules": [
                    {"condition": "control_flow == 'UNTRUSTED' or any_arg == 'UNTRUSTED'", "action": "CONFIRM"},
                ],
            },
        }
    }
    result = verify_policy_noninterference(compliant_policy)
    assert result["status"] == "VERIFIED"
    assert "send_email" in result["verified_tools"]
    assert "read_file" in result["verified_tools"]


def test_z3_catches_deliberately_bad_policy():
    """Verify Z3 catches a deliberately bad policy (ALLOW on UNTRUSTED-dependent call).

    Cites: Slide 39; §9.
    """
    bad_policy = {
        "tools": {
            "unsafe_tool": {
                "default_action": "ALLOW",  # Dangerous: allows on untrusted by default!
                "rules": [],
            }
        }
    }
    with pytest.raises(NoninterferenceViolationError) as exc_info:
        verify_policy_noninterference(bad_policy)

    assert "unsafe_tool" in str(exc_info.value)
    assert "Violation" in str(exc_info.value)


def test_z3_catches_bad_rule_in_policy():
    """Verify Z3 catches an explicit ALLOW rule on untrusted arguments."""
    bad_rule_policy = {
        "tools": {
            "compromised_tool": {
                "default_action": "DENY",
                "rules": [
                    {"condition": "any_arg == 'UNTRUSTED'", "action": "ALLOW"},  # Blatant violation!
                ],
            }
        }
    }
    with pytest.raises(NoninterferenceViolationError):
        verify_policy_noninterference(bad_rule_policy)
