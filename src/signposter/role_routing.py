"""Deterministic routing from Signposter stages to role policies."""

from __future__ import annotations

from dataclasses import dataclass

from signposter.dispatch import DispatchDecision, extract_label_value
from signposter.role_policy import RolePolicy, get_role_policy
from signposter.scan import LabeledItem

CORE_AREAS = {
    "scheduler",
    "planner",
    "runner",
    "merge",
    "review",
    "gate",
    "integration",
    "workflow",
    "safety",
}

DOC_HINTS = ("docs", "readme", "markdown")
TEST_HINTS = ("test", "tests", "pytest")
CORE_PATH_HINTS = (
    "src/signposter/merge",
    "src/signposter/review",
    "src/signposter/gate",
    "src/signposter/integration",
    "src/signposter/planner",
    "src/signposter/runner",
    "src/signposter/scheduler",
    "src/signposter/worktree",
    "src/signposter/openclaw",
)


@dataclass(frozen=True)
class RoleSelection:
    """Selected Signposter role policy for a deterministic stage."""

    policy: RolePolicy
    reason: str
    stage_kind: str
    deterministic: bool = True
    escalation_active: bool = False


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(pattern in lower for pattern in patterns)


def _is_docs_task(item: LabeledItem, area: str | None) -> bool:
    haystack = " ".join([item.title, area or "", " ".join(item.labels)])
    return _contains_any(haystack, DOC_HINTS)


def _is_test_task(item: LabeledItem, area: str | None) -> bool:
    haystack = " ".join([item.title, area or "", " ".join(item.labels)])
    return _contains_any(haystack, TEST_HINTS)


def _is_core_area(area: str | None) -> bool:
    return (area or "").lower() in CORE_AREAS


def _paths_are_docs_or_tests(file_paths: list[str]) -> bool:
    if not file_paths:
        return False
    for path in file_paths:
        lower = path.lower()
        if lower.endswith(".md") or lower.startswith("docs/"):
            continue
        if lower.startswith("tests/") or "/tests/" in lower or lower.endswith("_test.py"):
            continue
        return False
    return True


def _paths_touch_core(file_paths: list[str]) -> bool:
    return any(any(hint in path.lower() for hint in CORE_PATH_HINTS) for path in file_paths)


def select_role_for_issue(
    item: LabeledItem,
    dispatch: DispatchDecision | None = None,
) -> RoleSelection:
    """Select a role policy for an issue/task stage."""
    decision = dispatch
    phase = decision.phase if decision else extract_label_value(item.labels, "phase")
    role = decision.role if decision else extract_label_value(item.labels, "role")
    risk = decision.risk if decision else extract_label_value(item.labels, "risk")
    area = decision.area if decision else extract_label_value(item.labels, "area")

    if phase == "plan" or role == "planner":
        return RoleSelection(
            policy=get_role_policy("PLANNER_MAIN"),
            reason="planning stage requires roadmap-level reasoning rather than worker execution",
            stage_kind="issue",
        )

    if phase == "review" or role == "reviewer":
        return RoleSelection(
            policy=get_role_policy("REVIEWER_CORE"),
            reason="review issues are routed to reviewer-core policy for structured review work",
            stage_kind="issue",
        )

    if _is_docs_task(item, area) or _is_test_task(item, area):
        return RoleSelection(
            policy=get_role_policy("WORKER_LIGHT"),
            reason="docs/test-focused build task can use the cheaper light worker role",
            stage_kind="issue",
        )

    if risk == "high" or _is_core_area(area):
        return RoleSelection(
            policy=get_role_policy("WORKER_CORE"),
            reason="core or high-risk build task needs the stronger worker-core policy",
            stage_kind="issue",
        )

    return RoleSelection(
        policy=get_role_policy("WORKER_CODE"),
        reason="code implementation task is non-trivial but not core-safety critical",
        stage_kind="issue",
    )


def select_role_for_review(
    *,
    risk_level: str,
    size: str,
    file_paths: list[str],
) -> RoleSelection:
    """Select a role policy for PR review."""
    if risk_level == "high" or _paths_touch_core(file_paths):
        return RoleSelection(
            policy=get_role_policy("REVIEWER_CORE"),
            reason="core or high-risk PR review requires reviewer-core policy",
            stage_kind="review",
        )

    if size == "small" or _paths_are_docs_or_tests(file_paths):
        return RoleSelection(
            policy=get_role_policy("REVIEWER_LIGHT"),
            reason=(
                "small or docs/tests-focused PR review can use the cheaper "
                "reviewer-light policy"
            ),
            stage_kind="review",
        )

    return RoleSelection(
        policy=get_role_policy("REVIEWER_CORE"),
        reason="non-trivial code review defaults to reviewer-core for better semantic coverage",
        stage_kind="review",
    )


def select_role_for_reconcile(change_kind: str) -> RoleSelection:
    """Select a role policy for reconcile decisions."""
    normalized = change_kind.strip().lower()
    if normalized in {"next", "side", "stop", "simple"}:
        return RoleSelection(
            policy=get_role_policy("RECONCILE_LIGHT"),
            reason="simple next/side/stop reconcile can use the cheaper reconcile-light policy",
            stage_kind="reconcile",
        )

    return RoleSelection(
        policy=get_role_policy("RECONCILE_CORE"),
        reason="DAG-changing or dependency-changing reconcile needs reconcile-core policy",
        stage_kind="reconcile",
    )
