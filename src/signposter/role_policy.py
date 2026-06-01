"""Role-aware OpenClaw policy registry.

This module is the single source of truth for the active Signposter
role-to-model policy. Routing and execution surfaces should consume this
registry instead of hard-coding model names in multiple places.
"""

from __future__ import annotations

from dataclasses import dataclass

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


ACTIVE_ROLE_POLICIES: dict[str, RolePolicy] = {
    "ROUTER_CLASSIFIER": RolePolicy(
        name="ROUTER_CLASSIFIER",
        openclaw_agent="worker",
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
        openclaw_agent="worker",
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
        openclaw_agent="planner",
        model="openai/gpt-5.4-mini",
        reasoning_effort="low",
        use_case="Issue shaping, labels, acceptance criteria, dependencies.",
        escalation_role="PLANNER_MAIN",
    ),
    "PLANNER_MAIN": RolePolicy(
        name="PLANNER_MAIN",
        openclaw_agent="planner",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        use_case="Freeform goal to roadmap/task DAG planning.",
        escalation_role="CRITICAL_OVERRIDE",
    ),
    "WORKER_LIGHT": RolePolicy(
        name="WORKER_LIGHT",
        openclaw_agent="worker",
        model="xai/grok-build-0.1",
        reasoning_effort="low",
        use_case="Docs, tests, simple patch, low-risk refactor.",
        escalation_role="WORKER_CODE",
        fallback_model="openai/gpt-5.4-mini",
    ),
    "WORKER_CODE": RolePolicy(
        name="WORKER_CODE",
        openclaw_agent="worker",
        model="openai/gpt-5.3-codex",
        reasoning_effort="low",
        use_case="Code-heavy repo and terminal tasks.",
        escalation_role="WORKER_CORE",
    ),
    "WORKER_CORE": RolePolicy(
        name="WORKER_CORE",
        openclaw_agent="worker",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        use_case="Core Signposter semantics, safety, and orchestration changes.",
        escalation_role="CRITICAL_OVERRIDE",
    ),
    "REVIEWER_LIGHT": RolePolicy(
        name="REVIEWER_LIGHT",
        openclaw_agent="reviewer",
        model="xai/grok-build-0.1",
        reasoning_effort="low",
        use_case="Docs-only and small low/medium-risk PR review.",
        escalation_role="REVIEWER_CORE",
        fallback_model="openai/gpt-5.4-mini",
    ),
    "REVIEWER_CORE": RolePolicy(
        name="REVIEWER_CORE",
        openclaw_agent="reviewer",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        use_case="Lifecycle, gate, merge, policy, and OpenClaw review.",
        escalation_role="CRITICAL_OVERRIDE",
    ),
    "CRITICAL_OVERRIDE": RolePolicy(
        name="CRITICAL_OVERRIDE",
        openclaw_agent="main",
        model="openai/gpt-5.5",
        reasoning_effort="medium",
        use_case="Explicit critical/manual escalation path only.",
        restrictions=(
            "Must not be the default.",
            "High reasoning requires explicit manual escalation.",
        ),
        manual_only=True,
    ),
    "RECONCILE_LIGHT": RolePolicy(
        name="RECONCILE_LIGHT",
        openclaw_agent="planner",
        model="openai/gpt-5.4-mini",
        reasoning_effort="low",
        use_case="Simple next/side/stop reconcile decisions.",
        escalation_role="RECONCILE_CORE",
    ),
    "RECONCILE_CORE": RolePolicy(
        name="RECONCILE_CORE",
        openclaw_agent="planner",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        use_case="DAG-changing reconcile and dependency conflict handling.",
        escalation_role="CRITICAL_OVERRIDE",
    ),
    "LEGACY_BACKUP": RolePolicy(
        name="LEGACY_BACKUP",
        openclaw_agent="worker",
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


def validate_role_registry(registry: dict[str, RolePolicy] | None = None) -> list[str]:
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

    return errors


def format_role_policy_status(registry: dict[str, RolePolicy] | None = None) -> str:
    """Render the active role policy registry for dry-run/operator inspection."""
    active_registry = registry or ACTIVE_ROLE_POLICIES
    lines = ["Signposter Role Policy Status", ""]

    for role_name in sorted(active_registry):
        policy = active_registry[role_name]
        lines.append(f"{policy.name}")
        lines.append(f"  agent: {policy.openclaw_agent}")
        lines.append(f"  model: {policy.model}")
        lines.append(f"  reasoning: {policy.reasoning_effort}")
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

    errors = validate_role_registry(active_registry)
    lines.append("Validation:")
    lines.append("  status: pass" if not errors else "  status: fail")
    for error in errors:
        lines.append(f"  - {error}")

    return "\n".join(lines).rstrip()
