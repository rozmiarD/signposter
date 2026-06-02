from __future__ import annotations

from types import SimpleNamespace

import pytest

from signposter.bug_ledger import BugLedgerEntry
from signposter.cli import main
from signposter.control_status import (
    build_control_plane_status,
    format_control_plane_status,
)
from signposter.scan import LabeledItem
from signposter.scheduler import SchedulerNext


def test_control_plane_status_formats_empty_read_only_view() -> None:
    result = build_control_plane_status(repo="ExatronOmega/signposter")

    output = format_control_plane_status(result)

    assert result.status == "ready"
    assert "Signposter Control Plane Status" in output
    assert "Agreement:\n  status: not evaluated" in output
    assert "manifest: not provided" in output
    assert "Scheduler:\n  status: not evaluated" in output
    assert "Orchestrator:\n  status: not evaluated" in output
    assert "Bug ledger:\n  recent: none" in output
    assert "No GitHub mutation was performed." in output
    assert "No OpenClaw execution was performed." in output


def test_control_plane_status_formats_active_sources() -> None:
    planner = {
        "planner_status": "active",
        "status_counts": {
            "total": 5,
            "ready": 1,
            "active": 0,
            "merged": 4,
            "blocked": 0,
        },
        "next": {
            "next": {
                "key": "H045E",
                "github_issue": 157,
                "state": "ready",
            }
        },
    }
    issue = LabeledItem(
        number=157,
        title="H045E",
        html_url="https://github.com/ExatronOmega/signposter/issues/157",
        labels=["state:ready"],
        item_type="issue",
    )
    scheduler = SchedulerNext(
        repo="ExatronOmega/signposter",
        status="ready",
        issue=issue,
        reason="selected",
        skipped=[],
        notes=[],
        active_notes=[
            "#156: worktree=missing, prompt=present, summary=missing, "
            "activity_age=stale(3d), category=stale-active, resume=needs inspection"
        ],
        active_counts={"resumable": 1},
    )
    orchestrator = SimpleNamespace(
        status="ready",
        action="claim-issue",
        stop_reason=None,
        takeover_category=None,
        takeover_reason=None,
    )
    bug = BugLedgerEntry(
        key="BUG-0001",
        summary="issue #156: OpenClaw timeout for WORKER_CORE",
        status="runtime-blocker",
        current_issue=156,
    )

    result = build_control_plane_status(
        repo="ExatronOmega/signposter",
        planner_run=planner,
        scheduler_next=scheduler,
        orchestrator_next=orchestrator,
        bugs=(bug,),
    )

    output = format_control_plane_status(result)

    assert result.status == "ready"
    assert "Agreement:" in output
    assert "status: aligned" in output
    assert "planner issue: #157" in output
    assert "scheduler issue: #157" in output
    assert "active issues: none" in output
    assert "counts: total=5 ready=1 active=0 merged=4 blocked=0" in output
    assert "next: H045E (#157, state=ready)" in output
    assert "next: #157 — H045E" in output
    assert "active diagnostics:" in output
    assert "#156: worktree=missing, prompt=present, summary=missing" in output
    assert "action: claim-issue" in output
    assert "BUG-0001 [runtime-blocker] issue=#156" in output


def test_control_plane_status_surfaces_active_issue_stuck_diagnostics() -> None:
    scheduler = SchedulerNext(
        repo="ExatronOmega/signposter",
        status="ready",
        issue=None,
        reason="no ready issue",
        skipped=["#2: state:active"],
        notes=[],
        active_notes=[
            "#2: worktree=missing, prompt=missing, summary=missing, "
            "activity_age=stale(4d), category=stale-active, resume=needs inspection"
        ],
        active_counts={"stale-active": 1},
    )

    result = build_control_plane_status(
        repo="ExatronOmega/signposter",
        scheduler_next=scheduler,
    )

    output = format_control_plane_status(result)

    assert result.status == "ready"
    assert "active issues: #2" in output
    assert "active: stale-active=1" in output
    assert "active diagnostics:" in output
    assert "#2: worktree=missing, prompt=missing, summary=missing" in output
    assert "category=stale-active" in output
    assert "resume=needs inspection" in output


def test_control_plane_status_surfaces_blocked_state() -> None:
    scheduler = SchedulerNext(
        repo="ExatronOmega/signposter",
        status="blocked",
        issue=None,
        reason="dependency blocked",
        skipped=["#157: waiting"],
        notes=[],
    )
    orchestrator = SimpleNamespace(
        status="blocked",
        action="execute-worker",
        stop_reason="OpenClaw execution requires explicit --execute",
        takeover_category="runtime-stall",
        takeover_reason="worker artifact incomplete",
    )

    result = build_control_plane_status(
        repo="ExatronOmega/signposter",
        scheduler_next=scheduler,
        orchestrator_next=orchestrator,
    )

    output = format_control_plane_status(result)

    assert result.status == "blocked"
    assert "Status:\n  blocked" in output
    assert "stop: OpenClaw execution requires explicit --execute" in output
    assert "takeover: runtime-stall — worker artifact incomplete" in output


def test_control_plane_status_blocks_disagreed_targets() -> None:
    planner = {
        "planner_status": "active",
        "next": {
            "next": {
                "key": "H045E",
                "github_issue": 157,
                "state": "ready",
            }
        },
    }
    scheduler = SchedulerNext(
        repo="ExatronOmega/signposter",
        status="ready",
        issue=LabeledItem(
            number=158,
            title="H045F",
            html_url="https://github.com/ExatronOmega/signposter/issues/158",
            labels=["state:ready"],
            item_type="issue",
        ),
        reason="selected",
        skipped=[],
        notes=[],
    )
    orchestrator = SimpleNamespace(
        status="ready",
        action="claim-issue",
        stop_reason=None,
        takeover_category=None,
        takeover_reason=None,
        lifecycle=SimpleNamespace(issue_number=158),
    )

    result = build_control_plane_status(
        repo="ExatronOmega/signposter",
        planner_run=planner,
        scheduler_next=scheduler,
        orchestrator_next=orchestrator,
    )

    output = format_control_plane_status(result)

    assert result.status == "blocked"
    assert "Agreement:" in output
    assert "status: disagreement" in output
    assert "planner issue: #157" in output
    assert "scheduler issue: #158" in output
    assert "orchestrator issue: #158" in output
    assert "evaluated sources point at different issues" in output


def test_control_plane_status_blocks_ready_target_when_active_work_differs() -> None:
    planner = {
        "planner_status": "active",
        "next": {
            "next": {
                "key": "H049-032",
                "github_issue": 239,
                "state": "ready",
            }
        },
        "active_tasks": [
            {
                "key": "H049-029",
                "github_issue": 236,
                "state": "active",
            }
        ],
    }
    scheduler = SchedulerNext(
        repo="ExatronOmega/signposter",
        status="ready",
        issue=LabeledItem(
            number=239,
            title="H049-032",
            html_url="https://github.com/ExatronOmega/signposter/issues/239",
            labels=["state:ready"],
            item_type="issue",
        ),
        reason="selected",
        skipped=[],
        notes=[],
    )
    orchestrator = SimpleNamespace(
        status="ready",
        action="claim-issue",
        stop_reason=None,
        takeover_category=None,
        takeover_reason=None,
        lifecycle=SimpleNamespace(issue_number=239),
    )

    result = build_control_plane_status(
        repo="ExatronOmega/signposter",
        planner_run=planner,
        scheduler_next=scheduler,
        orchestrator_next=orchestrator,
    )

    output = format_control_plane_status(result)

    assert result.status == "blocked"
    assert "status: disagreement" in output
    assert "planner issue: #239" in output
    assert "scheduler issue: #239" in output
    assert "orchestrator issue: #239" in output
    assert "active issues: #236" in output


def test_control_plane_status_cli_combines_sources(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        """
{
  "version": "planner.seed-manifest.v0.1",
  "repo": "ExatronOmega/signposter",
  "status": "applied",
  "issues": [
    {
      "key": "H045E",
      "title": "H045E",
      "labels": [],
      "depends_on": [],
      "github_depends_on": [],
      "dependency_metadata": [],
      "github_issue": 157,
      "github_url": "https://github.com/ExatronOmega/signposter/issues/157"
    }
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    issue = LabeledItem(
        number=157,
        title="H045E",
        html_url="https://github.com/ExatronOmega/signposter/issues/157",
        labels=["state:ready"],
        item_type="issue",
    )
    scheduler = SchedulerNext(
        repo="ExatronOmega/signposter",
        status="ready",
        issue=issue,
        reason="selected",
        skipped=[],
        notes=[],
    )
    orchestrator = SimpleNamespace(
        status="ready",
        action="claim-issue",
        stop_reason=None,
        takeover_category=None,
        takeover_reason=None,
    )
    bug = BugLedgerEntry(
        key="BUG-0001",
        summary="issue #156: runtime blocker",
        status="runtime-blocker",
        current_issue=156,
    )

    monkeypatch.setattr(
        "signposter.cli._fetch_manifest_issue_states",
        lambda repo, manifest: {157: "ready"},
    )
    monkeypatch.setattr("signposter.cli.select_next_issue", lambda repo, limit=50: scheduler)
    monkeypatch.setattr(
        "signposter.cli.plan_orchestrator_next",
        lambda repo, issue=None, pr=None: orchestrator,
    )
    monkeypatch.setattr("signposter.cli.load_bug_ledger", lambda path: (bug,))
    monkeypatch.setattr(
        "sys.argv",
        [
            "signposter",
            "control-plane",
            "status",
            "--repo",
            "ExatronOmega/signposter",
            "--manifest",
            str(manifest),
            "--sync-github",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    output = capsys.readouterr().out
    assert exc_info.value.code in (None, 0)
    assert "Signposter Control Plane Status" in output
    assert "Planner:" in output
    assert "Scheduler:" in output
    assert "Orchestrator:" in output
    assert "Bug ledger:" in output
    assert "No GitHub mutation was performed." in output
