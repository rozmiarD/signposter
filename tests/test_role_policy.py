from __future__ import annotations

from signposter.role_policy import (
    ACTIVE_ROLE_POLICIES,
    RolePolicy,
    format_role_policy_status,
    get_role_policy,
    validate_role_policy,
    validate_role_registry,
)


def test_active_registry_uses_only_allowed_models():
    assert validate_role_registry() == []


def test_get_role_policy_returns_expected_core_roles():
    assert get_role_policy("WORKER_CORE").model == "openai/gpt-5.4"
    assert get_role_policy("WORKER_CORE").reasoning_effort == "medium"
    assert get_role_policy("REVIEWER_LIGHT").model == "openai/gpt-5.4-mini"
    assert get_role_policy("PLANNER_MAIN").openclaw_agent == "planner"


def test_gpt_55_is_reserved_for_manual_critical_override():
    roles = [
        policy.name
        for policy in ACTIVE_ROLE_POLICIES.values()
        if policy.model == "openai/gpt-5.5"
    ]

    assert roles == ["CRITICAL_OVERRIDE"]
    assert ACTIVE_ROLE_POLICIES["CRITICAL_OVERRIDE"].manual_only is True


def test_gpt_52_is_reserved_for_explicit_legacy_backup():
    roles = [
        policy.name
        for policy in ACTIVE_ROLE_POLICIES.values()
        if policy.model == "openai/gpt-5.2"
    ]

    assert roles == ["LEGACY_BACKUP"]
    assert ACTIVE_ROLE_POLICIES["LEGACY_BACKUP"].legacy_fallback is True


def test_validate_role_policy_rejects_forbidden_and_unavailable_models():
    forbidden = RolePolicy(
        name="BROKEN",
        openclaw_agent="worker",
        model="openai/gpt-5.5-pro",
        reasoning_effort="low",
        use_case="broken",
    )
    unavailable = RolePolicy(
        name="ALSO_BROKEN",
        openclaw_agent="worker",
        model="openai/deep-research-1",
        reasoning_effort="low",
        use_case="broken",
    )

    forbidden_errors = validate_role_policy(forbidden)
    unavailable_errors = validate_role_policy(unavailable)

    assert any("not in the allowed OpenClaw model set" in error for error in forbidden_errors)
    assert any("forbidden model family" in error for error in forbidden_errors)
    assert any("not in the allowed OpenClaw model set" in error for error in unavailable_errors)
    assert any("unavailable model family" in error for error in unavailable_errors)


def test_validate_role_policy_rejects_non_manual_gpt55_and_high_reasoning():
    policy = RolePolicy(
        name="TOO_EXPENSIVE",
        openclaw_agent="worker",
        model="openai/gpt-5.5",
        reasoning_effort="high",
        use_case="broken",
    )

    errors = validate_role_policy(policy)

    assert any("critical/manual escalation only" in error for error in errors)
    assert any(
        "high reasoning may only appear on manual escalation roles" in error
        for error in errors
    )


def test_validate_role_registry_rejects_unknown_references():
    registry = {
        "WORKER_LIGHT": RolePolicy(
            name="WORKER_LIGHT",
            openclaw_agent="worker",
            model="openai/gpt-5.4-mini",
            reasoning_effort="low",
            use_case="test",
            escalation_role="MISSING_ROLE",
        )
    }

    errors = validate_role_registry(registry)

    assert any("unknown escalation_role 'MISSING_ROLE'" in error for error in errors)


def test_format_role_policy_status_reports_pass_for_active_registry():
    output = format_role_policy_status()

    assert "Signposter Role Policy Status" in output
    assert "WORKER_CODE" in output
    assert "openai/gpt-5.3-codex" in output
    assert "Validation:" in output
    assert "status: pass" in output
