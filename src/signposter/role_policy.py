"""Role-aware OpenClaw policy registry.

This module is the single source of truth for the active Signposter
role-to-model policy. Routing and execution surfaces should consume this
registry instead of hard-coding model names in multiple places.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ALLOWED_OPENCLAW_MODELS = frozenset(
    {
        "openai/gpt-5.5",
        "openai/gpt-5.4",
        "openai/gpt-5.4-mini",
        "openai/gpt-5.3-codex",
        "openai/gpt-5.2",
        "xai/grok-build-0.1",
    }
)

ALLOWED_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high"})


@dataclass(frozen=True)
class RolePolicy:
    """Defines the active model policy for one Signposter role."""

    name: str
    openclaw_agent: str
    model: str
    reasoning_effort: str
    use_case: str
    escalation_role: str | None = None
    fallback_role: str | None = None
    fallback_model: str | None = None
    restrictions: tuple[str, ...] = ()
    manual_only: bool = False
    legacy_fallback: bool = False
    codex_cli_agent: str | None = None


@dataclass(frozen=True)
class OpenClawAgentProfile:
    """Configured OpenClaw agent/profile details from local runtime config."""

    name: str
    primary_model: str | None
    fallback_models: tuple[str, ...]

    @property
    def allowed_models(self) -> frozenset[str]:
        values = [self.primary_model, *self.fallback_models]
        return frozenset(value for value in values if value)


ACTIVE_ROLE_POLICIES: dict[str, RolePolicy] = {
    "ROUTER_CLASSIFIER": RolePolicy(
        name="ROUTER_CLASSIFIER",
        openclaw_agent="worker_light",
        codex_cli_agent="codex_router_classifier",
        model="openai/gpt-5.4-mini",
        reasoning_effort="minimal",
        use_case="Cheap routing and classification for recoverable stages.",
        escalation_role="ISSUE_FACTORY",
        restrictions=(
            "No mutation.",
            "No final safety decision.",
            "No GitHub mutation.",
        ),
    ),
    "ARTIFACT_SUMMARIZER": RolePolicy(
        name="ARTIFACT_SUMMARIZER",
        openclaw_agent="worker_light",
        codex_cli_agent="codex_artifact_summarizer",
        model="openai/gpt-5.4-mini",
        reasoning_effort="minimal",
        use_case="Bounded raw-output summarization and evidence extraction.",
        escalation_role="ROUTER_CLASSIFIER",
        restrictions=(
            "No mutation.",
            "No final safety decision.",
        ),
    ),
    "ISSUE_FACTORY": RolePolicy(
        name="ISSUE_FACTORY",
        openclaw_agent="planner_main",
        codex_cli_agent="codex_issue_factory",
        model="openai/gpt-5.4-mini",
        reasoning_effort="low",
        use_case="Issue shaping, labels, acceptance criteria, dependencies.",
        escalation_role="PLANNER_MAIN",
    ),
    "PLANNER_MAIN": RolePolicy(
        name="PLANNER_MAIN",
        openclaw_agent="planner_main",
        codex_cli_agent="codex_planner_main",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        use_case="Freeform goal to roadmap/task DAG planning.",
        escalation_role="CRITICAL_OVERRIDE",
    ),
    "WORKER_LIGHT": RolePolicy(
        name="WORKER_LIGHT",
        openclaw_agent="worker_light",
        codex_cli_agent="codex_worker_light",
        model="xai/grok-build-0.1",
        reasoning_effort="low",
        use_case="Docs, tests, simple patch, low-risk refactor.",
        escalation_role="WORKER_CODE",
        fallback_model="openai/gpt-5.4-mini",
    ),
    "WORKER_CODE": RolePolicy(
        name="WORKER_CODE",
        openclaw_agent="worker_code",
        codex_cli_agent="codex_worker_code",
        model="openai/gpt-5.3-codex",
        reasoning_effort="low",
        use_case="Code-heavy repo and terminal tasks.",
        escalation_role="WORKER_CORE",
    ),
    "WORKER_CORE": RolePolicy(
        name="WORKER_CORE",
        openclaw_agent="worker_core",
        codex_cli_agent="codex_worker_core",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        use_case="Core Signposter semantics, safety, and orchestration changes.",
        escalation_role="CRITICAL_OVERRIDE",
    ),
    "REVIEWER_LIGHT": RolePolicy(
        name="REVIEWER_LIGHT",
        openclaw_agent="reviewer_light",
        codex_cli_agent="codex_reviewer_light",
        model="xai/grok-build-0.1",
        reasoning_effort="low",
        use_case="Docs-only and small low/medium-risk PR review.",
        escalation_role="REVIEWER_CORE",
        fallback_model="openai/gpt-5.4-mini",
    ),
    "REVIEWER_CORE": RolePolicy(
        name="REVIEWER_CORE",
        openclaw_agent="reviewer_core",
        codex_cli_agent="codex_reviewer_core",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        use_case="Lifecycle, gate, merge, policy, and OpenClaw review.",
        escalation_role="CRITICAL_OVERRIDE",
    ),
    "CRITICAL_OVERRIDE": RolePolicy(
        name="CRITICAL_OVERRIDE",
        openclaw_agent="main",
        codex_cli_agent="codex_critical_override",
        model="openai/gpt-5.4",
        reasoning_effort="high",
        use_case="Explicit critical/manual escalation path only.",
        restrictions=(
            "Must not be the default.",
            "High reasoning requires explicit manual escalation.",
        ),
        manual_only=True,
    ),
    "RECONCILE_LIGHT": RolePolicy(
        name="RECONCILE_LIGHT",
        openclaw_agent="planner_main",
        codex_cli_agent="codex_reconcile_light",
        model="openai/gpt-5.4-mini",
        reasoning_effort="low",
        use_case="Simple next/side/stop reconcile decisions.",
        escalation_role="RECONCILE_CORE",
    ),
    "RECONCILE_CORE": RolePolicy(
        name="RECONCILE_CORE",
        openclaw_agent="planner_main",
        codex_cli_agent="codex_reconcile_core",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        use_case="DAG-changing reconcile and dependency conflict handling.",
        escalation_role="CRITICAL_OVERRIDE",
    ),
    "LEGACY_BACKUP": RolePolicy(
        name="LEGACY_BACKUP",
        openclaw_agent="worker_code",
        codex_cli_agent="codex_legacy_backup",
        model="openai/gpt-5.2",
        reasoning_effort="low",
        use_case="Explicit compatibility fallback only.",
        restrictions=(
            "Not for default planner.",
            "Not for core reviewer.",
            "Not for safety-sensitive merge/gate/policy decisions.",
        ),
        legacy_fallback=True,
    ),
}


def get_role_policy(role_name: str) -> RolePolicy:
    """Return the active policy for a role name."""
    return ACTIVE_ROLE_POLICIES[role_name]


def execution_agent_for_backend(policy: RolePolicy, backend: str) -> str:
    """Return the backend-specific execution agent/profile metadata for a role."""
    if backend == "codex-cli":
        return policy.codex_cli_agent or policy.openclaw_agent
    return policy.openclaw_agent


def _openclaw_config_path(env: dict[str, str] | None = None) -> Path:
    source = env or os.environ
    configured = source.get("OPENCLAW_CONFIG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()

    openclaw_home = source.get("OPENCLAW_HOME", "").strip()
    if openclaw_home:
        return Path(openclaw_home).expanduser() / "openclaw.json"

    return Path.home() / ".openclaw" / "openclaw.json"


def load_openclaw_agent_profiles(
    *,
    config_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[dict[str, OpenClawAgentProfile], str | None]:
    """Load configured OpenClaw agents from local runtime config."""
    path = config_path or _openclaw_config_path(env)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, f"OpenClaw config not found: {path}"
    except OSError as exc:
        return {}, f"OpenClaw config could not be read: {exc}"
    except json.JSONDecodeError as exc:
        return {}, f"OpenClaw config is not valid JSON: {exc}"

    agents = payload.get("agents", {})
    if not isinstance(agents, dict):
        return {}, "OpenClaw config missing top-level 'agents' object"

    agent_list = agents.get("list", [])
    if not isinstance(agent_list, list):
        return {}, "OpenClaw config missing 'agents.list' array"

    profiles: dict[str, OpenClawAgentProfile] = {}
    for entry in agent_list:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("id") or entry.get("name") or "").strip()
        if not name:
            continue
        model = entry.get("model", {})
        primary_model = None
        fallback_models: tuple[str, ...] = ()
        if isinstance(model, dict):
            primary_value = str(model.get("primary") or "").strip()
            if primary_value:
                primary_model = primary_value
            raw_fallbacks = model.get("fallbacks", [])
            if isinstance(raw_fallbacks, list):
                fallback_models = tuple(
                    str(item).strip() for item in raw_fallbacks if str(item).strip()
                )
        profiles[name] = OpenClawAgentProfile(
            name=name,
            primary_model=primary_model,
            fallback_models=fallback_models,
        )

    return profiles, None


def validate_role_policy(policy: RolePolicy) -> list[str]:
    """Return validation errors for one role policy."""
    errors: list[str] = []

    if policy.model not in ALLOWED_OPENCLAW_MODELS:
        errors.append(
            f"{policy.name}: model '{policy.model}' is not in the allowed OpenClaw model set"
        )

    if policy.fallback_model and policy.fallback_model not in ALLOWED_OPENCLAW_MODELS:
        errors.append(
            f"{policy.name}: fallback model '{policy.fallback_model}' "
            "is not in the allowed OpenClaw model set"
        )

    if policy.reasoning_effort not in ALLOWED_REASONING_EFFORTS:
        errors.append(
            f"{policy.name}: reasoning effort '{policy.reasoning_effort}' is not allowed"
        )

    if not policy.openclaw_agent.strip():
        errors.append(f"{policy.name}: openclaw_agent must not be empty")

    if policy.codex_cli_agent is not None and not policy.codex_cli_agent.strip():
        errors.append(f"{policy.name}: codex_cli_agent must not be empty")

    if "-pro" in policy.model or policy.model.endswith("-nano"):
        errors.append(f"{policy.name}: forbidden model family '{policy.model}'")

    if policy.fallback_model and (
        "-pro" in policy.fallback_model or policy.fallback_model.endswith("-nano")
    ):
        errors.append(
            f"{policy.name}: forbidden fallback model family '{policy.fallback_model}'"
        )

    if "deep-research" in policy.model or "/o" in policy.model:
        errors.append(f"{policy.name}: unavailable model family '{policy.model}'")

    if policy.fallback_model and (
        "deep-research" in policy.fallback_model or "/o" in policy.fallback_model
    ):
        errors.append(
            f"{policy.name}: unavailable fallback model family '{policy.fallback_model}'"
        )

    if policy.model == "openai/gpt-5.5" and not policy.manual_only:
        errors.append(f"{policy.name}: gpt-5.5 is reserved for critical/manual escalation only")

    if policy.model == "openai/gpt-5.2" and not policy.legacy_fallback:
        errors.append(f"{policy.name}: gpt-5.2 is reserved for explicit legacy fallback only")

    if policy.reasoning_effort == "high" and not policy.manual_only:
        errors.append(f"{policy.name}: high reasoning may only appear on manual escalation roles")

    return errors


def validate_role_agent_profiles(
    registry: dict[str, RolePolicy] | None = None,
    *,
    profiles: dict[str, OpenClawAgentProfile] | None = None,
    runtime_error: str | None = None,
) -> list[str]:
    """Return runtime validation errors for role->agent/profile bindings."""
    active_registry = registry or ACTIVE_ROLE_POLICIES
    if profiles is None and runtime_error:
        return [runtime_error]
    if profiles is None:
        return []

    errors: list[str] = []
    for policy in active_registry.values():
        profile = profiles.get(policy.openclaw_agent)
        if profile is None:
            errors.append(
                f"{policy.name}: configured OpenClaw agent/profile "
                f"'{policy.openclaw_agent}' is missing"
            )
            continue

        if profile.primary_model and profile.primary_model not in ALLOWED_OPENCLAW_MODELS:
            errors.append(
                f"{policy.name}: profile '{profile.name}' primary model "
                f"'{profile.primary_model}' is not allowed"
            )

        forbidden_fallbacks = [
            model for model in profile.fallback_models if model not in ALLOWED_OPENCLAW_MODELS
        ]
        if forbidden_fallbacks:
            errors.append(
                f"{policy.name}: profile '{profile.name}' fallback models are not allowed: "
                + ", ".join(forbidden_fallbacks)
            )

        allowed = profile.allowed_models
        if allowed and policy.model not in allowed:
            errors.append(
                f"{policy.name}: profile '{profile.name}' does not expose policy model "
                f"'{policy.model}' in its primary/fallback models"
            )
        if policy.fallback_model and allowed and policy.fallback_model not in allowed:
            errors.append(
                f"{policy.name}: profile '{profile.name}' does not expose fallback model "
                f"'{policy.fallback_model}'"
            )

    return errors


def validate_role_registry(
    registry: dict[str, RolePolicy] | None = None,
    *,
    profiles: dict[str, OpenClawAgentProfile] | None = None,
    runtime_error: str | None = None,
) -> list[str]:
    """Return all registry validation errors."""
    active_registry = registry or ACTIVE_ROLE_POLICIES
    errors: list[str] = []

    for role_name, policy in active_registry.items():
        if role_name != policy.name:
            errors.append(
                f"{role_name}: registry key does not match embedded policy name '{policy.name}'"
            )

        errors.extend(validate_role_policy(policy))

        for ref_name, ref_value in (
            ("escalation_role", policy.escalation_role),
            ("fallback_role", policy.fallback_role),
        ):
            if ref_value and ref_value not in active_registry:
                errors.append(f"{policy.name}: unknown {ref_name} '{ref_value}'")

    if registry is None:
        loaded_profiles, load_error = load_openclaw_agent_profiles()
        errors.extend(
            validate_role_agent_profiles(
                active_registry,
                profiles=loaded_profiles,
                runtime_error=load_error,
            )
        )
    else:
        errors.extend(
            validate_role_agent_profiles(
                active_registry,
                profiles=profiles,
                runtime_error=runtime_error,
            )
        )

    return errors


def format_role_policy_status(registry: dict[str, RolePolicy] | None = None) -> str:
    """Render the active role policy registry for dry-run/operator inspection."""
    active_registry = registry or ACTIVE_ROLE_POLICIES
    loaded_profiles, load_error = load_openclaw_agent_profiles() if registry is None else ({}, None)
    lines = ["Signposter Role Policy Status", ""]

    for role_name in sorted(active_registry):
        policy = active_registry[role_name]
        lines.append(f"{policy.name}")
        lines.append(f"  openclaw_agent: {policy.openclaw_agent}")
        lines.append(f"  codex_cli_agent: {execution_agent_for_backend(policy, 'codex-cli')}")
        lines.append(f"  agent: {policy.openclaw_agent}")
        lines.append(f"  model: {policy.model}")
        lines.append(f"  reasoning: {policy.reasoning_effort}")
        profile = loaded_profiles.get(policy.openclaw_agent)
        if registry is None:
            if profile is None:
                lines.append("  profile_status: missing")
            else:
                lines.append("  profile_status: present")
                lines.append(f"  profile_primary: {profile.primary_model or 'unknown'}")
                if profile.fallback_models:
                    lines.append(
                        "  profile_fallbacks: " + ", ".join(profile.fallback_models)
                    )
        if policy.escalation_role:
            lines.append(f"  escalation: {policy.escalation_role}")
        if policy.fallback_role:
            lines.append(f"  fallback: {policy.fallback_role}")
        if policy.fallback_model:
            lines.append(f"  fallback_model: {policy.fallback_model}")
        if policy.manual_only:
            lines.append("  manual_only: yes")
        if policy.legacy_fallback:
            lines.append("  legacy_fallback: yes")
        lines.append(f"  use: {policy.use_case}")
        lines.append("")

    errors = validate_role_registry(
        active_registry,
        profiles=loaded_profiles if registry is None else None,
        runtime_error=load_error if registry is None else None,
    )
    lines.append("Validation:")
    lines.append("  status: pass" if not errors else "  status: fail")
    for error in errors:
        lines.append(f"  - {error}")

    return "\n".join(lines).rstrip()
