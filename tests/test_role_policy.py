from __future__ import annotations

from unittest.mock import patch

from signposter.role_policy import (
    ACTIVE_ROLE_POLICIES,
    OpenClawAgentProfile,
    RolePolicy,
    execution_agent_for_backend,
    format_role_policy_status,
    get_role_policy,
    validate_role_agent_profiles,
    validate_role_policy,
    validate_role_registry,
)


def _mock_runtime_profiles():
    return {
        "worker_light": OpenClawAgentProfile(
            name="worker_light",
            primary_model="xai/grok-build-0.1",
            fallback_models=("openai/gpt-5.4-mini",),
        ),
        "worker_code": OpenClawAgentProfile(
            name="worker_code",
            primary_model="openai/gpt-5.3-codex",
            fallback_models=("openai/gpt-5.4", "openai/gpt-5.2"),
        ),
        "worker_core": OpenClawAgentProfile(
            name="worker_core",
            primary_model="openai/gpt-5.4",
            fallback_models=("openai/gpt-5.4-mini",),
        ),
        "reviewer_light": OpenClawAgentProfile(
            name="reviewer_light",
            primary_model="xai/grok-build-0.1",
            fallback_models=("openai/gpt-5.4-mini",),
        ),
        "reviewer_core": OpenClawAgentProfile(
            name="reviewer_core",
            primary_model="openai/gpt-5.4",
            fallback_models=("openai/gpt-5.4-mini",),
        ),
        "planner_main": OpenClawAgentProfile(
            name="planner_main",
            primary_model="openai/gpt-5.4",
            fallback_models=("openai/gpt-5.4-mini",),
        ),
        "main": OpenClawAgentProfile(
            name="main",
            primary_model="openai/gpt-5.4",
            fallback_models=(),
        ),
        "worker": OpenClawAgentProfile(
            name="worker",
            primary_model="openai/gpt-5.2",
            fallback_models=("openai/gpt-5.4",),
        ),
    }


def test_active_registry_uses_only_allowed_models():
    with patch(
        "signposter.role_policy.load_openclaw_agent_profiles",
        return_value=(_mock_runtime_profiles(), None),
    ):
        assert validate_role_registry() == []


def test_get_role_policy_returns_expected_core_roles():
    assert get_role_policy("WORKER_CORE").model == "openai/gpt-5.4"
    assert get_role_policy("WORKER_CORE").reasoning_effort == "medium"
    assert get_role_policy("WORKER_CORE").openclaw_agent == "worker_core"
    assert get_role_policy("WORKER_CORE").codex_cli_agent == "codex_worker_core"
    assert get_role_policy("REVIEWER_LIGHT").model == "xai/grok-build-0.1"
    assert get_role_policy("REVIEWER_LIGHT").openclaw_agent == "reviewer_light"
    assert get_role_policy("REVIEWER_LIGHT").codex_cli_agent == "codex_reviewer_light"
    assert get_role_policy("REVIEWER_LIGHT").fallback_model == "openai/gpt-5.4-mini"
    assert get_role_policy("WORKER_LIGHT").model == "xai/grok-build-0.1"
    assert get_role_policy("WORKER_LIGHT").openclaw_agent == "worker_light"
    assert get_role_policy("WORKER_LIGHT").codex_cli_agent == "codex_worker_light"
    assert get_role_policy("WORKER_LIGHT").fallback_model == "openai/gpt-5.4-mini"
    assert get_role_policy("PLANNER_MAIN").openclaw_agent == "planner_main"
    assert get_role_policy("PLANNER_MAIN").codex_cli_agent == "codex_planner_main"


def test_execution_agent_for_backend_separates_codex_from_openclaw():
    policy = get_role_policy("WORKER_CORE")

    assert execution_agent_for_backend(policy, "codex-cli") == "codex_worker_core"
    assert execution_agent_for_backend(policy, "openclaw") == "worker_core"


def test_critical_override_uses_gpt54_with_manual_high_reasoning():
    policy = ACTIVE_ROLE_POLICIES["CRITICAL_OVERRIDE"]

    assert policy.model == "openai/gpt-5.4"
    assert policy.reasoning_effort == "high"
    assert policy.manual_only is True


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


def test_validate_role_policy_allows_manual_high_reasoning_without_gpt55():
    policy = RolePolicy(
        name="CRITICAL_OVERRIDE",
        openclaw_agent="main",
        model="openai/gpt-5.4",
        reasoning_effort="high",
        use_case="manual escalation",
        manual_only=True,
    )

    assert validate_role_policy(policy) == []


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


def test_validate_role_agent_profiles_rejects_missing_profile():
    registry = {
        "WORKER_CORE": RolePolicy(
            name="WORKER_CORE",
            openclaw_agent="worker_core",
            model="openai/gpt-5.4",
            reasoning_effort="medium",
            use_case="core work",
        )
    }

    errors = validate_role_agent_profiles(registry, profiles={})

    assert any(
        "configured OpenClaw agent/profile 'worker_core' is missing" in error
        for error in errors
    )


def test_validate_role_agent_profiles_rejects_profile_without_policy_model():
    registry = {
        "WORKER_CODE": RolePolicy(
            name="WORKER_CODE",
            openclaw_agent="worker_code",
            model="openai/gpt-5.3-codex",
            reasoning_effort="low",
            use_case="code work",
        )
    }
    profiles = {
        "worker_code": OpenClawAgentProfile(
            name="worker_code",
            primary_model="openai/gpt-5.4",
            fallback_models=("openai/gpt-5.4-mini",),
        )
    }

    errors = validate_role_agent_profiles(registry, profiles=profiles)

    assert any("does not expose policy model 'openai/gpt-5.3-codex'" in error for error in errors)


def test_format_role_policy_status_reports_profile_presence_for_runtime_profiles():
    registry = {
        "WORKER_CORE": RolePolicy(
            name="WORKER_CORE",
            openclaw_agent="worker_core",
            model="openai/gpt-5.4",
            reasoning_effort="medium",
            use_case="core work",
        )
    }

    output = format_role_policy_status(registry)

    assert "WORKER_CORE" in output
    assert "agent: worker_core" in output


def test_format_role_policy_status_reports_pass_for_active_registry():
    with patch(
        "signposter.role_policy.load_openclaw_agent_profiles",
        return_value=(_mock_runtime_profiles(), None),
    ):
        output = format_role_policy_status()

    assert "Signposter Role Policy Status" in output
    assert "WORKER_CODE" in output
    assert "openai/gpt-5.3-codex" in output
    assert "xai/grok-build-0.1" in output
    assert "openclaw_agent: worker_core" in output
    assert "codex_cli_agent: codex_worker_core" in output
    assert "fallback_model: openai/gpt-5.4-mini" in output
    assert "profile_status:" in output
    assert "Validation:" in output
    assert "status:" in output
