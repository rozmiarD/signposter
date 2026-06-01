from __future__ import annotations

import json
from pathlib import Path

from signposter.bug_ledger import (
    apply_bug_ledger_plan,
    format_bug_ledger_plan,
    load_bug_ledger,
    plan_record_bug,
    plan_show_bugs,
    plan_update_bug,
)


def test_record_bug_plan_writes_new_entry(tmp_path: Path) -> None:
    ledger = tmp_path / "bug-ledger.json"

    plan = plan_record_bug(
        summary="Reviewer stale finding referenced deleted diff context.",
        source_pr=148,
        current_issue=151,
        ledger_path=ledger,
    )

    assert plan.status == "ready"
    assert plan.entry is not None
    assert plan.entry.key == "BUG-0001"

    wrote = apply_bug_ledger_plan(plan, apply=True)

    assert wrote is True
    saved = json.loads(ledger.read_text(encoding="utf-8"))
    assert saved["entries"][0]["summary"].startswith("Reviewer stale finding")


def test_update_bug_plan_marks_deferred_follow_up(tmp_path: Path) -> None:
    ledger = tmp_path / "bug-ledger.json"
    created = plan_record_bug(
        summary="Planner branch lookup mismatched expected source branch.",
        source_issue=132,
        current_issue=151,
        ledger_path=ledger,
    )
    apply_bug_ledger_plan(created, apply=True)

    updated = plan_update_bug(
        key="BUG-0001",
        status="deferred-to-issue",
        follow_up_issue=166,
        notes="Needs planner branch/source detection fix.",
        ledger_path=ledger,
    )

    assert updated.status == "ready"
    assert updated.entry is not None
    assert updated.entry.follow_up_issue == 166

    apply_bug_ledger_plan(updated, apply=True)
    entry = load_bug_ledger(ledger)[0]
    assert entry.status == "deferred-to-issue"
    assert entry.follow_up_issue == 166


def test_update_bug_blocks_unknown_key(tmp_path: Path) -> None:
    result = plan_update_bug(
        key="BUG-9999",
        status="runtime-blocker",
        ledger_path=tmp_path / "bug-ledger.json",
    )

    assert result.status == "blocked"
    assert result.errors == ("unknown bug key: BUG-9999",)


def test_deferred_bug_requires_follow_up_issue(tmp_path: Path) -> None:
    result = plan_record_bug(
        summary="Runtime auth cooldown blocked review submit.",
        status="deferred-to-issue",
        ledger_path=tmp_path / "bug-ledger.json",
    )

    assert result.status == "blocked"
    assert "deferred-to-issue requires follow_up_issue" in result.errors


def test_show_bug_plan_is_bounded(tmp_path: Path) -> None:
    ledger = tmp_path / "bug-ledger.json"
    for index in range(10):
        plan = plan_record_bug(
            summary=f"Bug {index}",
            ledger_path=ledger,
        )
        apply_bug_ledger_plan(plan, apply=True)

    shown = plan_show_bugs(ledger_path=ledger, limit=3)
    out = format_bug_ledger_plan(shown, limit=3)

    assert len(shown.entries) == 3
    assert "BUG-0010" in out
    assert "BUG-0007" not in out
