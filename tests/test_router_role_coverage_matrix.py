"""Coverage matrix for role policy and routing role families."""

from __future__ import annotations

from signposter.dispatch import classify_candidate
from signposter.role_policy import ACTIVE_ROLE_POLICIES
from signposter.role_routing import (
    select_role_for_issue,
    select_role_for_reconcile,
    select_role_for_review,
)
from signposter.scan import LabeledItem


def _item(labels: list[str], title: str) -> LabeledItem:
    return LabeledItem(
        number=1,
        title=title,
        html_url="https://github.com/example/repo/issues/1",
        labels=labels,
        item_type="issue",
    )


def _issue_selection(labels: list[str], title: str) -> str:
    item = _item(labels, title)
    return select_role_for_issue(item, classify_candidate(item)).policy.name


def test_cheap_automation_roles_are_low_cost_and_non_mutating() -> None:
    router = ACTIVE_ROLE_POLICIES["ROUTER_CLASSIFIER"]
    summarizer = ACTIVE_ROLE_POLICIES["ARTIFACT_SUMMARIZER"]
    issue_factory = ACTIVE_ROLE_POLICIES["ISSUE_FACTORY"]

    assert router.model == "openai/gpt-5.4-mini"
    assert router.reasoning_effort == "minimal"
    assert any("No mutation" in item for item in router.restrictions)
    assert summarizer.model == "openai/gpt-5.4-mini"
    assert summarizer.reasoning_effort == "minimal"
    assert issue_factory.model == "openai/gpt-5.4-mini"
    assert issue_factory.reasoning_effort == "low"


def test_worker_role_family_models_and_routing_are_covered() -> None:
    assert ACTIVE_ROLE_POLICIES["WORKER_LIGHT"].model == "xai/grok-build-0.1"
    assert ACTIVE_ROLE_POLICIES["WORKER_LIGHT"].fallback_model == "openai/gpt-5.4-mini"
    assert ACTIVE_ROLE_POLICIES["WORKER_CODE"].model == "openai/gpt-5.3-codex"
    assert ACTIVE_ROLE_POLICIES["WORKER_CORE"].model == "openai/gpt-5.4"

    assert (
        _issue_selection(
            ["state:ready", "phase:build", "role:worker", "risk:low", "area:docs"],
            "docs: update readme",
        )
        == "WORKER_LIGHT"
    )
    assert (
        _issue_selection(
            ["state:ready", "phase:build", "role:worker", "risk:medium", "area:cli"],
            "implement CLI output",
        )
        == "WORKER_CODE"
    )
    assert (
        _issue_selection(
            ["state:ready", "phase:build", "role:worker", "risk:medium", "area:core"],
            "audit router policy",
        )
        == "WORKER_CORE"
    )


def test_reviewer_role_family_models_and_routing_are_covered() -> None:
    assert ACTIVE_ROLE_POLICIES["REVIEWER_LIGHT"].model == "xai/grok-build-0.1"
    assert ACTIVE_ROLE_POLICIES["REVIEWER_LIGHT"].fallback_model == "openai/gpt-5.4-mini"
    assert ACTIVE_ROLE_POLICIES["REVIEWER_CORE"].model == "openai/gpt-5.4"

    assert (
        select_role_for_review(
            risk_level="low",
            size="small",
            file_paths=["README.md"],
        ).policy.name
        == "REVIEWER_LIGHT"
    )
    assert (
        select_role_for_review(
            risk_level="medium",
            size="medium",
            file_paths=["src/signposter/role_routing.py"],
        ).policy.name
        == "REVIEWER_CORE"
    )


def test_reconcile_role_family_models_and_routing_are_covered() -> None:
    assert ACTIVE_ROLE_POLICIES["RECONCILE_LIGHT"].model == "openai/gpt-5.4-mini"
    assert ACTIVE_ROLE_POLICIES["RECONCILE_LIGHT"].reasoning_effort == "low"
    assert ACTIVE_ROLE_POLICIES["RECONCILE_CORE"].model == "openai/gpt-5.4"
    assert ACTIVE_ROLE_POLICIES["RECONCILE_CORE"].reasoning_effort == "medium"

    assert select_role_for_reconcile("next").policy.name == "RECONCILE_LIGHT"
    assert select_role_for_reconcile("side").policy.name == "RECONCILE_LIGHT"
    assert select_role_for_reconcile("dag-change").policy.name == "RECONCILE_CORE"


def test_critical_and_legacy_roles_are_explicit_boundaries_only() -> None:
    critical = ACTIVE_ROLE_POLICIES["CRITICAL_OVERRIDE"]
    legacy = ACTIVE_ROLE_POLICIES["LEGACY_BACKUP"]

    assert critical.model == "openai/gpt-5.4"
    assert critical.reasoning_effort == "high"
    assert critical.manual_only is True
    assert legacy.model == "openai/gpt-5.2"
    assert legacy.legacy_fallback is True

    normal_routes = {
        _issue_selection(
            ["state:ready", "phase:build", "role:worker", "risk:high", "area:core"],
            "audit safety routing",
        ),
        select_role_for_review(
            risk_level="high",
            size="medium",
            file_paths=["src/signposter/gate.py"],
        ).policy.name,
        select_role_for_reconcile("dependency-change").policy.name,
    }

    assert critical.name not in normal_routes
    assert legacy.name not in normal_routes
