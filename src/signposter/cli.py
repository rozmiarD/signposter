"""Signposter Command Line Interface.

Currently in bootstrap phase. Only the `doctor` command is implemented.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from signposter.claim import cli_main as claim_cli_main
from signposter.dispatch import cli_main as dispatch_cli_main
from signposter.doctor import main as doctor_main
from signposter.gate import evaluate_gate_for_complete, format_gate_report, run_gate_dry_run
from signposter.handoff import format_handoff_plan, plan_handoff_for_issue
from signposter.integration import (
    apply_integration,
    format_integration_apply_dry_run,
    format_integration_plan,
    plan_integration_for_pr,
)
from signposter.merge import (
    apply_merge,
    format_merge_apply_dry_run,
    format_merge_plan,
    plan_merge_for_pr,
)
from signposter.planner import (
    build_planner_next,
    build_planner_seed_plan,
    format_planner_draft,
    format_planner_mark_result,
    format_planner_next,
    format_planner_roadmap,
    format_planner_seed_plan,
    format_planner_validation,
    load_planner_plan,
    mark_planner_task,
    validate_planner_plan,
    write_planner_draft,
)
from signposter.pr import format_pr_plan, plan_pr_for_issue
from signposter.report import report_main
from signposter.review import (
    evaluate_review_gate,
    execute_pr_review,
    format_review_gate,
    format_review_plan,
    format_review_submit_plan,
    plan_review_for_pr,
    plan_review_submit,
    submit_review,
    write_review_prompt_artifact,
)
from signposter.runner import cli_main as runner_cli_main
from signposter.scan import cli_main as scan_cli_main
from signposter.sync import (
    apply_sync,
    format_sync_apply_result,
    format_sync_plan,
    plan_sync,
)
from signposter.transitions import (
    format_transition_plan,
    perform_transition_mutation,
    run_transition_dry_run,
)
from signposter.worktree import (
    apply_worktree_plan,
    format_worktree_apply_plan,
    format_worktree_plan,
    plan_worktree_for_issue,
)


def main() -> None:
    """Main entry point for the signposter CLI."""
    parser = argparse.ArgumentParser(
        prog="signposter",
        description="Signposter — Local GitHub/OpenClaw workflow dispatcher (bootstrap phase)",
    )
    subparsers = parser.add_subparsers(dest="command")

    # doctor subcommand
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run environment preflight checks (read-only)",
        description="Perform read-only checks on the local environment and project structure.",
    )
    doctor_parser.set_defaults(func=run_doctor)

    # scan subcommand
    scan_parser = subparsers.add_parser(
        "scan",
        help="Read-only GitHub repository scanner (bootstrap phase)",
        description="Inspect open issues, PRs, and workflow state using neutral labels.",
    )
    scan_parser.add_argument(
        "--repo",
        required=True,
        help="Target repository in owner/repo format (e.g. ExatronOmega/signposter)",
    )
    scan_parser.set_defaults(func=run_scan)

    # dispatch subcommand (dry-run only in bootstrap)
    dispatch_parser = subparsers.add_parser(
        "dispatch",
        help="Dispatch dry-run planner (read-only, bootstrap phase)",
        description="Classify candidates and propose routing without taking any actions.",
    )
    dispatch_parser.add_argument(
        "--repo",
        required=True,
        help="Target repository in owner/repo format",
    )
    dispatch_parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Required: run in dry-run mode only (no mutations)",
    )
    dispatch_parser.set_defaults(func=run_dispatch)

    # claim subcommand
    claim_parser = subparsers.add_parser(
        "claim",
        help="Claim/lease planner (supports --dry-run and --apply)",
        description="Determine which ready items would be claimed. Use --apply to mutate.",
    )
    claim_parser.add_argument(
        "--repo",
        required=True,
        help="Target repository in owner/repo format",
    )
    claim_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in read-only dry-run mode",
    )
    claim_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform label mutations on GitHub (requires explicit use)",
    )
    claim_parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of items to claim this run (default: 1 for safety)",
    )
    claim_parser.set_defaults(func=run_claim)

    # release / complete / fail subcommands
    release_parser = subparsers.add_parser(
        "release",
        help="Release an active item back to ready (dry-run by default, --apply for mutation)",
    )
    release_parser.add_argument("--repo", required=True)
    release_parser.add_argument("--issue", type=int, required=True)
    release_parser.add_argument(
        "--dry-run", action="store_true", help="Run in read-only mode (default)"
    )
    release_parser.add_argument(
        "--apply", action="store_true", help="Actually perform the label mutation"
    )
    release_parser.set_defaults(func=run_release)

    complete_parser = subparsers.add_parser(
        "complete",
        help="Mark active item as done (dry-run by default, --apply for mutation)",
    )
    complete_parser.add_argument("--repo", required=True)
    complete_parser.add_argument("--issue", type=int, required=True)
    complete_parser.add_argument(
        "--dry-run", action="store_true", help="Run in read-only mode (default)"
    )
    complete_parser.add_argument(
        "--apply", action="store_true", help="Actually perform the label mutation"
    )
    complete_parser.set_defaults(func=run_complete)

    fail_parser = subparsers.add_parser(
        "fail",
        help="Mark an active item as failed (dry-run by default, --apply for mutation)",
    )
    fail_parser.add_argument("--repo", required=True)
    fail_parser.add_argument("--issue", type=int, required=True)
    fail_parser.add_argument(
        "--dry-run", action="store_true", help="Run in read-only mode (default)"
    )
    fail_parser.add_argument(
        "--apply", action="store_true", help="Actually perform the label mutation"
    )
    fail_parser.set_defaults(func=run_fail)

    # run subcommand
    run_parser = subparsers.add_parser(
        "run",
        help="Runner planner (supports --claim, --write-prompt, --execute)",
        description="Plan execution and optionally execute OpenClaw locally.",
    )
    run_parser.add_argument("--repo", required=True)
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in read-only planning mode",
    )
    run_parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of items to plan execution for (default: 1)",
    )
    run_parser.add_argument(
        "--write-prompt",
        action="store_true",
        help="Generate and write the local prompt artifact (does not run OpenClaw)",
    )
    run_parser.add_argument(
        "--claim",
        action="store_true",
        help="Actually claim the selected item on GitHub (explicit mutation)",
    )
    run_parser.add_argument(
        "--execute",
        action="store_true",
        help="Run OpenClaw agent locally for already-active item (explicit)",
    )
    run_parser.add_argument(
        "--issue",
        type=int,
        help="Target a specific issue number explicitly (bypasses claim planner)",
    )
    run_parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow execution even if the working tree has uncommitted changes (use with caution)",
    )
    run_parser.add_argument(
        "--worktree",
        action="store_true",
        help=(
            "Execute from the isolated worktree for the given --issue "
            "(requires --issue + --execute, worker profile only)"
        ),
    )
    run_parser.set_defaults(func=run_runner)

    # planner subcommand group — HARDENING-029A
    planner_parser = subparsers.add_parser(
        "planner",
        help="Planner surfaces (local draft only in H029A)",
        description="Create local deterministic planner drafts without GitHub or OpenClaw.",
    )
    planner_subparsers = planner_parser.add_subparsers(dest="planner_command")

    planner_draft_parser = planner_subparsers.add_parser(
        "draft",
        help="Create a local planner draft JSON file",
    )
    planner_draft_parser.add_argument(
        "--goal",
        required=True,
        help="High-level goal to decompose into a small deterministic roadmap",
    )
    planner_draft_parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output path for the local planner JSON draft",
    )
    planner_draft_parser.set_defaults(func=run_planner_draft)

    planner_validate_parser = planner_subparsers.add_parser(
        "validate",
        help="Validate a local planner JSON file",
    )
    planner_validate_parser.add_argument(
        "--plan",
        required=True,
        type=Path,
        help="Path to the local planner JSON draft",
    )
    planner_validate_parser.set_defaults(func=run_planner_validate)

    planner_seed_parser = planner_subparsers.add_parser(
        "seed",
        help="Plan GitHub issue creation from a local planner JSON file",
    )
    planner_seed_parser.add_argument(
        "--plan",
        required=True,
        type=Path,
        help="Path to the local planner JSON draft",
    )
    planner_seed_parser.set_defaults(func=run_planner_seed)

    planner_next_parser = planner_subparsers.add_parser(
        "next",
        help="Choose the next dependency-ready issue from a local planner JSON file",
    )
    planner_next_parser.add_argument(
        "--plan",
        required=True,
        type=Path,
        help="Path to the local planner JSON draft",
    )
    planner_next_parser.set_defaults(func=run_planner_next)

    planner_mark_parser = planner_subparsers.add_parser(
        "mark",
        help="Update a task status inside a local planner JSON file",
    )
    planner_mark_parser.add_argument(
        "--plan",
        required=True,
        type=Path,
        help="Path to the local planner JSON draft",
    )
    planner_mark_parser.add_argument(
        "--task",
        required=True,
        help="Planner task key, e.g. WATCH-001",
    )
    planner_mark_parser.add_argument(
        "--status",
        required=True,
        choices=["pending", "active", "done", "blocked", "failed"],
        help="Local task status to write into the planner JSON file",
    )
    planner_mark_parser.add_argument(
        "--reason",
        default=None,
        help="Optional reason stored with the task status",
    )
    planner_mark_parser.set_defaults(func=run_planner_mark)

    planner_roadmap_parser = planner_subparsers.add_parser(
        "roadmap",
        help="Render a generic roadmap document from a local planner JSON file",
    )
    planner_roadmap_parser.add_argument(
        "--plan",
        required=True,
        type=Path,
        help="Path to the local planner JSON draft",
    )
    planner_roadmap_parser.add_argument(
        "--out",
        default=None,
        type=Path,
        help="Optional output path for the rendered roadmap Markdown",
    )
    planner_roadmap_parser.set_defaults(func=run_planner_roadmap)

    # worktree subcommand group (planning only — HARDENING-007)
    worktree_parser = subparsers.add_parser(
        "worktree",
        help="Isolated worktree and branch planning (dry-run only)",
        description="Plan safe isolated execution using git worktrees and branches.",
    )
    worktree_subparsers = worktree_parser.add_subparsers(dest="worktree_command")

    worktree_plan_parser = worktree_subparsers.add_parser(
        "plan",
        help="Produce a dry-run plan for isolated execution of an issue",
    )
    worktree_plan_parser.add_argument("--repo", required=True)
    worktree_plan_parser.add_argument("--issue", type=int, required=True)
    worktree_plan_parser.set_defaults(func=run_worktree_plan)

    # worktree apply subcommand (guarded creation — HARDENING-008)
    worktree_apply_parser = worktree_subparsers.add_parser(
        "apply",
        help="Create the planned branch and worktree (dry-run by default, --apply to execute)",
    )
    worktree_apply_parser.add_argument("--repo", required=True)
    worktree_apply_parser.add_argument("--issue", type=int, required=True)
    worktree_apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done (default behavior)",
    )
    worktree_apply_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create the branch and worktree (requires explicit use)",
    )
    worktree_apply_parser.set_defaults(func=run_worktree_apply)

    # handoff subcommand group (planning only — HARDENING-012)
    handoff_parser = subparsers.add_parser(
        "handoff",
        help="Branch handoff / commit / PR planning (dry-run only)",
        description="Plan commit, push, and handoff of work done inside an isolated worktree.",
    )
    handoff_subparsers = handoff_parser.add_subparsers(dest="handoff_command")

    handoff_plan_parser = handoff_subparsers.add_parser(
        "plan",
        help="Produce a dry-run handoff plan for an issue's worktree",
    )
    handoff_plan_parser.add_argument("--repo", required=True)
    handoff_plan_parser.add_argument("--issue", type=int, required=True)
    handoff_plan_parser.set_defaults(func=run_handoff_plan)

    # pr subcommand group (planning only — HARDENING-013)
    pr_parser = subparsers.add_parser(
        "pr",
        help="Pull request planning for isolated worker branches (dry-run only)",
        description="Plan PR metadata and gh commands without creating a PR.",
    )
    pr_subparsers = pr_parser.add_subparsers(dest="pr_command")

    pr_plan_parser = pr_subparsers.add_parser(
        "plan",
        help="Produce a dry-run PR plan for an issue's worker branch",
    )
    pr_plan_parser.add_argument("--repo", required=True)
    pr_plan_parser.add_argument("--issue", type=int, required=True)
    pr_plan_parser.add_argument(
        "--base",
        default="main",
        help="Base branch for the pull request (default: main)",
    )
    pr_plan_parser.set_defaults(func=run_pr_plan)

    # review subcommand group (HARDENING-014 — reviewer-agent PR review planning)
    review_parser = subparsers.add_parser(
        "review",
        help="Reviewer-agent PR review planning (dry-run only)",
        description="Plan OpenClaw reviewer inspection of a pull request.",
    )
    review_subparsers = review_parser.add_subparsers(dest="review_command")

    review_plan_parser = review_subparsers.add_parser(
        "plan",
        help="Produce a dry-run review plan for a PR",
    )
    review_plan_parser.add_argument("--repo", required=True)
    review_plan_parser.add_argument("--pr", type=int, required=True)
    review_plan_parser.set_defaults(func=run_review_plan)

    # write-prompt subcommand (HARDENING-015)
    write_prompt_parser = review_subparsers.add_parser(
        "write-prompt",
        help="Write the reviewer prompt artifact for a PR (dry-run planning only)",
    )
    write_prompt_parser.add_argument("--repo", required=True)
    write_prompt_parser.add_argument("--pr", type=int, required=True)
    write_prompt_parser.set_defaults(func=run_review_write_prompt)

    # execute subcommand (HARDENING-016)
    execute_parser = review_subparsers.add_parser(
        "execute",
        help=(
            "Execute the reviewer agent locally against a PR review prompt "
            "(dry-run local execution only)"
        ),
    )
    execute_parser.add_argument("--repo", required=True)
    execute_parser.add_argument("--pr", type=int, required=True)
    execute_parser.set_defaults(func=run_review_execute)

    # gate subcommand (HARDENING-017)
    gate_parser = review_subparsers.add_parser(
        "gate",
        help="Evaluate the review gate for a PR using the reviewer opinion (dry-run only)",
    )
    gate_parser.add_argument("--repo", required=True)
    gate_parser.add_argument("--pr", type=int, required=True)
    gate_parser.set_defaults(func=run_review_gate)

    # submit subcommand (HARDENING-018 — GitHub review submission)
    submit_parser = review_subparsers.add_parser(
        "submit",
        help="Plan or apply a GitHub PR review based on reviewer gate (dry-run by default)",
    )
    submit_parser.add_argument("--repo", required=True)
    submit_parser.add_argument("--pr", type=int, required=True)
    submit_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually submit the review to GitHub (default is dry-run)",
    )
    submit_parser.set_defaults(func=run_review_submit)

    # merge subcommand group (HARDENING-019 — dry-run merge planning only)
    merge_parser = subparsers.add_parser(
        "merge",
        help="PR merge planning (dry-run only)",
        description="Evaluate whether a PR is eligible for a guarded merge.",
    )
    merge_subparsers = merge_parser.add_subparsers(dest="merge_command")

    merge_plan_parser = merge_subparsers.add_parser(
        "plan",
        help="Produce a dry-run merge eligibility plan for a PR",
    )
    merge_plan_parser.add_argument("--repo", required=True)
    merge_plan_parser.add_argument("--pr", type=int, required=True)
    merge_plan_parser.set_defaults(func=run_merge_plan)

    # apply subcommand (HARDENING-020)
    merge_apply_parser = merge_subparsers.add_parser(
        "apply",
        help="Guarded PR merge (dry-run by default; use --apply to execute)",
    )
    merge_apply_parser.add_argument("--repo", required=True)
    merge_apply_parser.add_argument("--pr", type=int, required=True)
    merge_apply_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the merge (default is dry-run)",
    )
    merge_apply_parser.set_defaults(func=run_merge_apply)

    # integration subcommand group (HARDENING-021A — post-merge integration planning, dry-run only)
    integration_parser = subparsers.add_parser(
        "integration",
        help="Post-merge integration planning (dry-run only)",
        description="Verify merged PR and plan issue integration / close.",
    )
    integration_subparsers = integration_parser.add_subparsers(dest="integration_command")

    integration_plan_parser = integration_subparsers.add_parser(
        "plan",
        help="Produce a dry-run post-merge integration plan for a PR",
    )
    integration_plan_parser.add_argument("--repo", required=True)
    integration_plan_parser.add_argument("--pr", type=int, required=True)
    integration_plan_parser.set_defaults(func=run_integration_plan)

    # apply subcommand (HARDENING-021B)
    integration_apply_parser = integration_subparsers.add_parser(
        "apply",
        help="Apply post-merge issue integration (dry-run by default; --apply to execute)",
    )
    integration_apply_parser.add_argument("--repo", required=True)
    integration_apply_parser.add_argument("--pr", type=int, required=True)
    integration_apply_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform label transition, comment, and issue close",
    )
    integration_apply_parser.set_defaults(func=run_integration_apply)

    # report subcommand (for posting runner summaries back to GitHub)
    report_parser = subparsers.add_parser(
        "report",
        help="Post a runner execution summary to a GitHub issue (dry-run by default)",
        description="Read a local runner summary artifact and optionally post it as a comment.",
    )
    report_parser.add_argument("--repo", required=True)
    report_parser.add_argument("--issue", type=int, required=True)
    report_parser.add_argument(
        "--summary",
        default=None,
        help=(
            "Path to the local summary artifact to post. "
            "If omitted, the newest issue-specific summary is used."
        ),
    )
    report_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be posted (default)",
    )
    report_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually post the comment to GitHub (explicit mutation)",
    )
    report_parser.set_defaults(func=run_report)

    # gate subcommand (dry-run gate decision)
    gate_parser = subparsers.add_parser(
        "gate",
        help="Evaluate review gate for an issue (dry-run by default)",
        description="Read local runner artifacts + GitHub state and propose next gate action.",
    )
    gate_parser.add_argument("--repo", required=True)
    gate_parser.add_argument("--issue", type=int, required=True)
    gate_parser.add_argument(
        "--summary",
        default=None,
        help=(
            "Path to runner summary artifact. "
            "If omitted, the newest issue-specific summary is used."
        ),
    )
    gate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show recommendation without taking action (default and only supported mode for now)",
    )
    gate_parser.set_defaults(func=run_gate)

    # HARDENING-021C: local cleanup (plan + guarded --apply)
    _register_cleanup_subcommands(subparsers)

    # HARDENING-022A: lifecycle status (read-only)
    _register_lifecycle_subcommands(subparsers)

    # HARDENING-024E: guarded local repository sync/rebase
    _register_sync_subcommands(subparsers)

    # HARDENING-023A: repository label preflight (read-only)
    _register_labels_subcommands(subparsers)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if hasattr(args, "func"):
        exit_code = args.func(args)
        sys.exit(exit_code)
    else:
        parser.print_help()
        sys.exit(1)


def _find_latest_summary_for_issue(issue: int) -> str | None:
    """Find the newest runner summary artifact for a specific issue.

    Never falls back to another issue. This prevents report/gate commands from
    accidentally using stale bootstrap artifacts such as issue-2-reviewer.
    """
    runs_dir = Path("artifacts/runs")
    if not runs_dir.exists():
        return None

    candidates = list(runs_dir.glob(f"issue-{issue}-*.summary.md"))
    if not candidates:
        return None

    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(newest)


def run_doctor(_args: argparse.Namespace) -> int:
    """Execute the doctor command."""
    return doctor_main()


def run_scan(args: argparse.Namespace) -> int:
    """Execute the scan command."""
    repo = getattr(args, "repo", None)
    if not repo:
        print("Error: --repo is required for scan command", file=sys.stderr)
        return 1
    return scan_cli_main(repo)


def run_dispatch(args: argparse.Namespace) -> int:
    """Execute the dispatch dry-run command."""
    repo = getattr(args, "repo", None)
    dry_run = getattr(args, "dry_run", False)
    if not repo:
        print("Error: --repo is required for dispatch command", file=sys.stderr)
        return 1
    if not dry_run:
        print("Error: --dry-run is currently required (bootstrap phase)", file=sys.stderr)
        return 1
    return dispatch_cli_main(repo)


def run_claim(args: argparse.Namespace) -> int:
    """Execute the claim command (dry-run or apply)."""
    repo = getattr(args, "repo", None)
    apply = getattr(args, "apply", False)
    limit = getattr(args, "limit", 1)
    if not repo:
        print("Error: --repo is required for claim command", file=sys.stderr)
        return 1
    return claim_cli_main(repo, limit=limit, apply=apply)


def run_release(args: argparse.Namespace) -> int:
    """Execute release (dry-run by default, --apply for mutation)."""
    repo = getattr(args, "repo", None)
    issue = getattr(args, "issue", None)
    apply = getattr(args, "apply", False)
    if not repo or issue is None:
        print("Error: --repo and --issue are required", file=sys.stderr)
        return 1

    plan = run_transition_dry_run(repo, issue, "release")
    print(format_transition_plan(plan, dry_run=not apply))

    if apply and plan.valid:
        try:
            commands = perform_transition_mutation(plan, repo, dry_run=False)
            print("\n=== APPLYING RELEASE MUTATION ===\n")
            for cmd in commands:
                print(f"  Executed: {cmd}")
            print("\nRelease mutation complete.")
        except RuntimeError as e:
            print(f"\nStatus: blocked — {e}")
            print("\nNotes:")
            print("  No labels were changed.")
            print("  No GitHub mutation was performed.")
            print("  No issue was closed.")
            return 1
    elif apply:
        print("Cannot apply invalid plan.")

    return 0 if plan.valid else 1


def run_complete(args: argparse.Namespace) -> int:
    """Execute complete (dry-run by default, --apply for mutation)."""
    repo = getattr(args, "repo", None)
    issue = getattr(args, "issue", None)
    apply = getattr(args, "apply", False)
    if not repo or issue is None:
        print("Error: --repo and --issue are required", file=sys.stderr)
        return 1

    plan = run_transition_dry_run(repo, issue, "complete")
    print(format_transition_plan(plan, dry_run=not apply))

    if apply and plan.valid:
        # H024F: Gate preflight before complete mutation
        try:
            gate_ok, decision, reason, gate_type = evaluate_gate_for_complete(repo, issue)
        except Exception as e:
            print(f"\nStatus: blocked — gate evaluation error: {e}")
            print("\nNotes:")
            print("  No labels were changed.")
            print("  No GitHub mutation was performed.")
            print("  No issue was closed.")
            return 1

        if not gate_ok:
            print(f"\nSignposter Complete — Issue #{issue}")
            print("\nGate:")
            print("  status: blocked")
            print(f"  decision: {decision.upper()}")
            print(f"  reason: {reason}")
            print("\nStatus:")
            print("  blocked — gate did not pass")
            print("\nNotes:")
            print("  No labels were changed.")
            print("  No GitHub mutation was performed.")
            print("  Issue was not marked done.")
            return 1

        # Gate passed — proceed with existing mutation (label preflight still runs inside)
        try:
            commands = perform_transition_mutation(plan, repo, dry_run=False)
            print("\n=== APPLYING COMPLETE MUTATION ===\n")
            for cmd in commands:
                print(f"  Executed: {cmd}")
            print("\nComplete mutation complete.")
        except RuntimeError as e:
            print(f"\nStatus: blocked — {e}")
            print("\nNotes:")
            print("  No labels were changed.")
            print("  No GitHub mutation was performed.")
            print("  No issue was closed.")
            return 1
    elif apply:
        print("Cannot apply invalid plan.")

    return 0 if plan.valid else 1


def run_fail(args: argparse.Namespace) -> int:
    """Execute fail (dry-run by default, --apply for mutation)."""
    repo = getattr(args, "repo", None)
    issue = getattr(args, "issue", None)
    apply = getattr(args, "apply", False)
    if not repo or issue is None:
        print("Error: --repo and --issue are required", file=sys.stderr)
        return 1

    plan = run_transition_dry_run(repo, issue, "fail")
    print(format_transition_plan(plan, dry_run=not apply))

    if apply and plan.valid:
        print("\n=== APPLYING FAIL MUTATION ===\n")
        commands = perform_transition_mutation(plan, repo, dry_run=False)
        for cmd in commands:
            print(f"  Executed: {cmd}")
        print("\nFail mutation complete.")
    elif apply:
        print("Cannot apply invalid plan.")

    return 0 if plan.valid else 1


def run_runner(args: argparse.Namespace) -> int:
    """Execute the runner planner command."""
    repo = getattr(args, "repo", None)
    limit = getattr(args, "limit", 1)
    write_prompt = getattr(args, "write_prompt", False)
    claim = getattr(args, "claim", False)
    execute = getattr(args, "execute", False)
    if not repo:
        print("Error: --repo is required for run command", file=sys.stderr)
        return 1
    issue = getattr(args, "issue", None)
    allow_dirty = getattr(args, "allow_dirty", False)
    use_worktree = getattr(args, "worktree", False)
    return runner_cli_main(
        repo,
        limit=limit,
        write_prompt=write_prompt,
        claim=claim,
        execute=execute,
        issue=issue,
        allow_dirty=allow_dirty,
        worktree=use_worktree,
    )  # noqa: E501


def run_report(args: argparse.Namespace) -> int:
    """Run report command."""
    repo = args.repo
    issue = args.issue
    summary = getattr(args, "summary", None)
    apply = getattr(args, "apply", False)

    if not summary:
        summary = _find_latest_summary_for_issue(issue)
        if not summary:
            print(
                f"Error: no summary artifact found for issue #{issue} "
                f"(expected artifacts/runs/issue-{issue}-*.summary.md)",
                file=sys.stderr,
            )
            return 2

    return report_main(repo, issue, summary, apply=apply)


def run_gate(args: argparse.Namespace) -> int:
    """Run gate command."""
    repo = args.repo
    issue = args.issue
    summary = getattr(args, "summary", None)

    if not summary:
        summary = _find_latest_summary_for_issue(issue)
        if not summary:
            print(
                f"Error: no summary artifact found for issue #{issue} "
                f"(expected artifacts/runs/issue-{issue}-*.summary.md)",
                file=sys.stderr,
            )
            return 2

    try:
        result = run_gate_dry_run(repo, issue, summary_path=summary)
    except Exception as e:
        print(f"Gate failed: {e}", file=sys.stderr)
        return 1

    print(format_gate_report(result))
    return 0


def run_worktree_plan(args: argparse.Namespace) -> int:
    """Handler for `signposter worktree plan --repo ... --issue N`."""
    repo = getattr(args, "repo", None)
    issue = getattr(args, "issue", None)

    if not repo or not issue:
        print("Error: --repo and --issue are required", file=sys.stderr)
        return 1

    try:
        plan = plan_worktree_for_issue(repo, issue)
        print(format_worktree_plan(plan))
        return 0 if plan.status == "ready" else 1
    except Exception as e:
        print(f"Worktree plan failed: {e}", file=sys.stderr)
        return 2


def run_worktree_apply(args: argparse.Namespace) -> int:
    """Handler for `signposter worktree apply --repo ... --issue N [--apply]`."""
    repo = getattr(args, "repo", None)
    issue = getattr(args, "issue", None)
    apply_flag = getattr(args, "apply", False)

    if not repo or not issue:
        print("Error: --repo and --issue are required", file=sys.stderr)
        return 1

    dry_run = not apply_flag

    try:
        plan = plan_worktree_for_issue(repo, issue)
        print(format_worktree_apply_plan(plan, dry_run=dry_run))

        if apply_flag:
            if plan.status == "ready":
                commands = apply_worktree_plan(plan, dry_run=False)
                print("\nCreated:")
                print(f"  branch: {plan.proposed_branch}")
                print(f"  worktree: {plan.proposed_worktree}")
                for cmd in commands:
                    print(f"  Executed: {cmd}")
            else:
                print("\nRefusing to create worktree (plan not ready).")

        return 0 if plan.status == "ready" else 1
    except Exception as e:
        print(f"Worktree apply failed: {e}", file=sys.stderr)
        return 2


def run_handoff_plan(args: argparse.Namespace) -> int:
    """Handler for `signposter handoff plan --repo ... --issue N`."""
    repo = getattr(args, "repo", None)
    issue = getattr(args, "issue", None)

    if not repo or not issue:
        print("Error: --repo and --issue are required", file=sys.stderr)
        return 1

    try:
        plan = plan_handoff_for_issue(repo, issue)
        print(format_handoff_plan(plan))
        return 0 if plan.status == "ready" else 1
    except Exception as e:
        print(f"Handoff plan failed: {e}", file=sys.stderr)
        return 2

def run_pr_plan(args: argparse.Namespace) -> None:
    """Run PR planning for an issue."""
    plan = plan_pr_for_issue(args.repo, args.issue, base_branch=args.base)
    print(format_pr_plan(plan))


def run_review_plan(args: argparse.Namespace) -> int:
    """Handler for `signposter review plan --repo ... --pr N`."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        plan = plan_review_for_pr(repo, pr)
        print(format_review_plan(plan))
        return 0 if plan.status == "ready" else 1
    except Exception as e:
        print(f"Review plan failed: {e}", file=sys.stderr)
        return 2


def run_review_write_prompt(args: argparse.Namespace) -> int:
    """Handler for `signposter review write-prompt --repo ... --pr N` (HARDENING-015)."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        path = write_review_prompt_artifact(repo, pr)
        print(f"Signposter Review Prompt — PR #{pr}")
        print("")
        print("Prompt:")
        print(f"  path: {path}")
        print("  reviewer: reviewer")
        print(f"  source: PR #{pr}")
        print("  status: written")
        print("")
        print("Notes:")
        print("  No review was executed.")
        print("  No GitHub review was submitted.")
        print("  No merge was performed.")
        print("  No issue was closed.")
        return 0
    except RuntimeError as e:
        # Expected: plan not ready
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Write prompt failed: {e}", file=sys.stderr)
        return 2


def run_review_execute(args: argparse.Namespace) -> int:
    """Handler for `signposter review execute --repo ... --pr N` (HARDENING-016)."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        result = execute_pr_review(repo, pr)

        print(f"Signposter Review Execute — PR #{pr}")
        print("")
        print("Reviewer:")
        print("  agent: reviewer")
        print(f"  prompt: artifacts/prompts/pr-{pr}-review.md")
        print(f"  raw artifact: {result.get('raw_path') or 'none'}")
        print(f"  summary artifact: {result.get('summary_path') or 'none'}")
        print("")
        status = (
            "completed"
            if result.get("success")
            else f"failed (exit={result.get('exit_code')})"
        )
        print(f"Status:\n  {status}")
        if result.get("error"):
            print(f"  Error: {result['error']}")
        print("")
        print("Notes:")
        print("  No GitHub review was submitted.")
        print("  No PR approval was submitted.")
        print("  No merge was performed.")
        print("  No issue was closed.")
        return 0 if result.get("success") else 1
    except Exception as e:
        print(f"Review execute failed: {e}", file=sys.stderr)
        return 2


def run_review_gate(args: argparse.Namespace) -> int:
    """Handler for `signposter review gate --repo ... --pr N` (HARDENING-017)."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        result = evaluate_review_gate(repo, pr)
        print(format_review_gate(result))
        return 0 if result.gate_pass else 1
    except Exception as e:
        print(f"Review gate failed: {e}", file=sys.stderr)
        return 2


def run_review_submit(args: argparse.Namespace) -> int:
    """Handler for `signposter review submit --repo ... --pr N [--apply]` (HARDENING-018)."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)
    do_apply = getattr(args, "apply", False)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        if not do_apply:
            # Dry-run path (default)
            plan = plan_review_submit(repo, pr)
            print(format_review_submit_plan(plan))
            return 0 if plan.status in ("ready", "ready-for-request-changes") else 1
        else:
            # Mutation path
            result = submit_review(repo, pr, apply=True)
            plan = result.get("plan")

            if result.get("mode") == "apply":
                success = result.get("success", False)
                print(f"Signposter Review Submit — PR #{pr}")
                print("")
                print("GitHub review:")
                print(f"  action: {plan.action if plan else 'unknown'}")
                print(f"  status: {'submitted' if success else 'failed'}")
                if not success and result.get("stderr"):
                    print(f"  stderr: {result['stderr'].strip()[:300]}")
                print("")
                print("Notes:")
                print("  No merge was performed.")
                print("  No issue was closed.")
                return 0 if success else 1
            else:
                # Blocked (including self-review guard from 018A)
                print(f"Signposter Review Submit — PR #{pr}")
                print("")
                print("GitHub review:")
                print(f"  action: {plan.action if plan else 'blocked'}")
                print("  status: refused")
                if plan and plan.failure_reason:
                    print(f"  reason: {plan.failure_reason}")
                print("")
                print("Notes:")
                print("  No merge was performed.")
                print("  No issue was closed.")
                return 1
    except Exception as e:
        print(f"Review submit failed: {e}", file=sys.stderr)
        return 2


def run_merge_plan(args: argparse.Namespace) -> int:
    """Handler for `signposter merge plan --repo ... --pr N` (HARDENING-019)."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        plan = plan_merge_for_pr(repo, pr)
        print(format_merge_plan(plan))
        return 0 if plan.status == "ready" else 1
    except Exception as e:
        print(f"Merge plan failed: {e}", file=sys.stderr)
        return 2


def run_merge_apply(args: argparse.Namespace) -> int:
    """Handler for `signposter merge apply --repo ... --pr N [--apply]` (HARDENING-020)."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)
    do_apply = getattr(args, "apply", False)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        result = apply_merge(repo, pr, apply=do_apply)
        plan = result.get("plan")

        if result.get("mode") == "dry_run":
            print(format_merge_apply_dry_run(plan))
            return 0
        elif result.get("mode") == "apply":
            success = result.get("success", False)
            print(f"Signposter Merge Apply — PR #{pr}")
            print("")
            print("Merge:")
            print(f"  method: {plan.merge_method if plan else 'squash'}")
            del_b = 'yes' if plan and plan.delete_branch_after_merge else 'yes'
            print(f"  delete branch after merge: {del_b}")
            print(f"  status: {'merged' if success else 'failed'}")
            if not success and result.get("stderr"):
                print(f"  stderr: {result['stderr'].strip()[:300]}")
            print("")
            print("Notes:")
            print("  No issue was closed by Signposter.")
            print("  No local worktree was removed.")
            print(
                "  Remote branch deletion was requested via gh pr merge --delete-branch."
            )
            return 0 if success else 1
        else:
            # apply_blocked
            err = result.get("error", plan.status if plan else "unknown")
            print(f"Signposter Merge Apply — PR #{pr}")
            print("")
            print("Merge status: blocked")
            print(f"  reason: {err}")
            print("")
            print("Notes:")
            print("  No merge was performed.")
            print("  No issue was closed.")
            print("  No local worktree was removed.")
            return 1
    except Exception as e:
        print(f"Merge apply failed: {e}", file=sys.stderr)
        return 2


def run_integration_plan(args: argparse.Namespace) -> int:
    """Handler for `signposter integration plan --repo ... --pr N` (HARDENING-021A)."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        plan = plan_integration_for_pr(repo, pr)
        print(format_integration_plan(plan))
        return 0 if plan.status == "ready" else 1
    except Exception as e:
        print(f"Integration plan failed: {e}", file=sys.stderr)
        return 2


def run_integration_apply(args: argparse.Namespace) -> int:
    """Handler for `signposter integration apply --repo ... --pr N [--apply]`."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)
    do_apply = getattr(args, "apply", False)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        result = apply_integration(repo, pr, apply=do_apply)
        plan = result.get("plan")

        if result.get("mode") == "dry_run":
            print(format_integration_apply_dry_run(plan, repo))
            return 0
        elif result.get("mode") == "apply":
            success = result.get("success", False)
            print(f"Signposter Integration Apply — PR #{pr}")
            print("")
            print("Issue:")
            if plan and plan.associated_issue:
                print(f"  issue: #{plan.associated_issue}")
            if success:
                print("  removed label: state:done")
                print("  added label: state:merged")
                print(f"  close reason: {plan.close_reason if plan else 'completed'}")
                print("  state: CLOSED")
            else:
                print("  status: failed")
                if result.get("errors"):
                    for err in result["errors"]:
                        print(f"    {err}")
            print("")
            print("Status:")
            print(f"  {'completed' if success else 'failed'}")
            print("")
            print("Notes:")
            print("  No local worktree was removed.")
            print("  No PR merge was performed.")
            return 0 if success else 1
        else:
            # apply_blocked
            err = result.get("error", plan.status if plan else "unknown")
            print(f"Signposter Integration Apply — PR #{pr}")
            print("")
            print("Status: blocked")
            print(f"  reason: {err}")
            print("")
            print("Notes:")
            print("  No issue was closed.")
            print("  No labels were changed.")
            print("  No local worktree was removed.")
            return 1
    except Exception as e:
        print(f"Integration apply failed: {e}", file=sys.stderr)
        return 2


# =============================================================================
# HARDENING-021C: Local worktree cleanup (plan + guarded apply)
# =============================================================================

from signposter.cleanup import (  # noqa: E402
    apply_cleanup,
    format_cleanup_apply_dry_run,
    format_cleanup_apply_result,
    format_cleanup_plan,
    plan_cleanup_for_pr,
)


def run_cleanup_plan(args: argparse.Namespace) -> int:
    """Handler for `signposter cleanup plan --repo ... --pr N`."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        plan = plan_cleanup_for_pr(repo, pr)
        print(format_cleanup_plan(plan))
        return 0 if plan.status in ("ready", "completed") else 1
    except Exception as e:
        print(f"Cleanup plan failed: {e}", file=sys.stderr)
        return 2


def run_cleanup_apply(args: argparse.Namespace) -> int:
    """Handler for `signposter cleanup apply --repo ... --pr N [--apply]`."""
    repo = getattr(args, "repo", None)
    pr = getattr(args, "pr", None)
    do_apply = getattr(args, "apply", False)

    if not repo or pr is None:
        print("Error: --repo and --pr are required", file=sys.stderr)
        return 1

    try:
        result = apply_cleanup(repo, pr, apply=do_apply)

        if result.get("mode") == "dry_run":
            print(format_cleanup_apply_dry_run(result.get("plan")))
            return 0
        elif result.get("mode") == "apply":
            print(format_cleanup_apply_result(result))
            success = result.get("success", False)
            return 0 if success else 1
        else:
            # apply_blocked
            print(format_cleanup_apply_result(result))
            return 1
    except Exception as e:
        print(f"Cleanup apply failed: {e}", file=sys.stderr)
        return 2


def _register_cleanup_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register the cleanup command group (plan + apply)."""
    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Local worktree/branch cleanup for merged PRs (local only, no GitHub mutations)",
        description="Plan and apply safe removal of finished worker worktrees and local branches.",
    )
    cleanup_subparsers = cleanup_parser.add_subparsers(dest="cleanup_command")

    # cleanup plan
    plan_parser = cleanup_subparsers.add_parser(
        "plan",
        help="Produce a read-only cleanup plan for a merged PR (identifies worktree + branch)",
    )
    plan_parser.add_argument("--repo", required=True)
    plan_parser.add_argument("--pr", type=int, required=True)
    plan_parser.set_defaults(func=run_cleanup_plan)

    # cleanup apply
    apply_parser = cleanup_subparsers.add_parser(
        "apply",
        help=(
            "Remove worktree and local branch for a merged PR "
            "(dry-run by default; --apply to execute)"
        ),
    )
    apply_parser.add_argument("--repo", required=True)
    apply_parser.add_argument("--pr", type=int, required=True)
    apply_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually remove the worktree and delete the local branch (requires explicit use)",
    )
    apply_parser.set_defaults(func=run_cleanup_apply)


# =============================================================================
# HARDENING-022A: Lifecycle status (read-only cross-phase summary)
# =============================================================================

from signposter.lifecycle import (  # noqa: E402
    format_lifecycle_next,
    format_lifecycle_status,
    plan_lifecycle_next,
    plan_lifecycle_status,
)


def run_lifecycle_status(args: argparse.Namespace) -> int:
    """Handler for `signposter lifecycle status --repo ... (--issue N | --pr N)`."""
    repo = getattr(args, "repo", None)
    issue = getattr(args, "issue", None)
    pr = getattr(args, "pr", None)

    if not repo:
        print("Error: --repo is required", file=sys.stderr)
        return 1
    if (issue is None) == (pr is None):
        print("Error: exactly one of --issue or --pr is required", file=sys.stderr)
        return 1

    try:
        status = plan_lifecycle_status(repo, issue=issue, pr=pr)
        print(format_lifecycle_status(status))
        # Non-zero exit only for clearly blocked cases
        if "could not be detected" in status.status or status.status.startswith("incomplete"):
            return 1
        return 0
    except Exception as e:
        print(f"Lifecycle status failed: {e}", file=sys.stderr)
        return 2



def run_lifecycle_next(args: argparse.Namespace) -> int:
    """Handler for `signposter lifecycle next --repo ... (--issue N | --pr P)`."""
    repo = args.repo
    issue = args.issue
    pr = args.pr

    if issue is not None and pr is not None:
        print("Error: choose exactly one of --issue or --pr", file=sys.stderr)
        return 2
    if issue is None and pr is None:
        print("Error: choose exactly one of --issue or --pr", file=sys.stderr)
        return 2

    try:
        result = plan_lifecycle_next(repo, issue=issue, pr=pr)
        print(format_lifecycle_next(result))
        return 0 if result.status in ("actionable", "complete") else 1
    except Exception as e:
        print(f"Lifecycle next failed: {e}", file=sys.stderr)
        return 1

def _register_lifecycle_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register the lifecycle command group."""
    lifecycle_parser = subparsers.add_parser(
        "lifecycle",
        help="Read-only cross-phase lifecycle status for issue or PR",
        description=(
        "Summarize the full lifecycle state (issue + PR + review + "
        "integration + cleanup) in one view."
    ),
    )
    lifecycle_subparsers = lifecycle_parser.add_subparsers(dest="lifecycle_command")

    status_parser = lifecycle_subparsers.add_parser(
        "status",
        help="Show combined lifecycle status for an issue or PR (read-only)",
    )
    status_parser.add_argument("--repo", required=True)
    status_parser.add_argument(
        "--issue", type=int, help="Issue number (exactly one of --issue or --pr)"
    )
    status_parser.add_argument(
        "--pr", type=int, help="PR number (exactly one of --issue or --pr)"
    )
    status_parser.set_defaults(func=run_lifecycle_status)

    next_parser = lifecycle_subparsers.add_parser(
        "next",
        help="Show the next recommended lifecycle action (read-only)",
        description=(
            "Recommend the next safe operator action for an issue or PR "
            "without performing mutations."
        ),
    )
    next_parser.add_argument("--repo", required=True)
    next_parser.add_argument(
        "--issue",
        type=int,
        default=None,
        help="Issue number to inspect",
    )
    next_parser.add_argument(
        "--pr",
        type=int,
        default=None,
        help="Pull request number to inspect",
    )
    next_parser.set_defaults(func=run_lifecycle_next)



# =============================================================================
# HARDENING-024E: Guarded local repository sync/rebase
# =============================================================================


def run_sync_plan(args: argparse.Namespace) -> int:
    """Handler for `signposter sync plan --repo ...`."""
    try:
        plan = plan_sync(Path.cwd())
        print(format_sync_plan(plan))
        return 0 if plan.status in ("completed", "ready") else 1
    except Exception as e:
        print(f"Sync plan failed: {e}", file=sys.stderr)
        return 2


def run_sync_apply(args: argparse.Namespace) -> int:
    """Handler for `signposter sync apply --repo ... [--rebase] [--apply]`."""
    try:
        result = apply_sync(
            Path.cwd(),
            apply=getattr(args, "apply", False),
            rebase=getattr(args, "rebase", False),
        )
        print(format_sync_apply_result(result))

        if result.get("mode") == "apply" and result.get("status") == "completed":
            return 0

        plan = result.get("plan")
        if not getattr(args, "apply", False):
            if plan is not None and getattr(plan, "status", None) in ("completed", "ready"):
                return 0

        return 1
    except Exception as e:
        print(f"Sync apply failed: {e}", file=sys.stderr)
        return 2


def _register_sync_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register guarded local sync/rebase commands."""
    sync_parser = subparsers.add_parser(
        "sync",
        help="Guarded local repository sync/rebase planning",
        description=(
            "Inspect local git sync state and optionally run a guarded "
            "git pull --rebase. Never pushes."
        ),
    )
    sync_subparsers = sync_parser.add_subparsers(dest="sync_command")

    plan_parser = sync_subparsers.add_parser(
        "plan",
        help="Show local repository sync status (read-only except safe fetch)",
    )
    plan_parser.add_argument("--repo", required=True)
    plan_parser.set_defaults(func=run_sync_plan)

    apply_parser = sync_subparsers.add_parser(
        "apply",
        help="Guarded sync apply (dry-run by default; --apply to execute)",
    )
    apply_parser.add_argument("--repo", required=True)
    apply_parser.add_argument(
        "--rebase",
        action="store_true",
        help="Allow git pull --rebase when apply mode is explicitly enabled",
    )
    apply_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually run the guarded sync operation",
    )
    apply_parser.set_defaults(func=run_sync_apply)


# =============================================================================
# HARDENING-023A: Repository label preflight (read-only)
# =============================================================================

from signposter.labels import (  # noqa: E402
    check_labels,
    ensure_labels,
    format_label_check,
    format_label_ensure,
)


def run_labels_check(args: argparse.Namespace) -> int:
    """Handler for `signposter labels check --repo ...`."""
    repo = getattr(args, "repo", None)
    if not repo:
        print("Error: --repo is required", file=sys.stderr)
        return 1

    try:
        result = check_labels(repo)
        print(format_label_check(result))
        return 0 if result.status == "pass" else 1
    except Exception as e:
        print(f"Label check failed: {e}", file=sys.stderr)
        return 2


def run_labels_ensure(args: argparse.Namespace) -> int:
    """Handler for `signposter labels ensure --repo ... [--apply]`."""
    repo = getattr(args, "repo", None)
    do_apply = getattr(args, "apply", False)

    if not repo:
        print("Error: --repo is required", file=sys.stderr)
        return 1

    try:
        result = ensure_labels(repo, apply=do_apply)
        print(format_label_ensure(result))
        if result.status in ("completed", "ready"):
            return 0
        return 1
    except Exception as e:
        print(f"Label ensure failed: {e}", file=sys.stderr)
        return 2


def _register_labels_subcommands(subparsers: argparse._SubParsersAction) -> None:
    """Register the labels command group (check only for now)."""
    labels_parser = subparsers.add_parser(
        "labels",
        help="Repository label preflight checks (read-only)",
        description="Check that required Signposter workflow labels exist in the repository.",
    )
    labels_subparsers = labels_parser.add_subparsers(dest="labels_command")

    check_parser = labels_subparsers.add_parser(
        "check",
        help="Verify that all required workflow labels exist (read-only)",
    )
    check_parser.add_argument("--repo", required=True)
    check_parser.set_defaults(func=run_labels_check)

    # H023B: guarded ensure (dry-run by default, --apply to create)
    ensure_parser = labels_subparsers.add_parser(
        "ensure",
        help="Create missing required workflow labels (dry-run by default)",
    )
    ensure_parser.add_argument("--repo", required=True)
    ensure_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create the missing labels (requires explicit use)",
    )
    ensure_parser.set_defaults(func=run_labels_ensure)

def run_planner_roadmap(args: argparse.Namespace) -> int:
    """Render a generic roadmap document from a local planner draft."""
    plan = load_planner_plan(args.plan)
    roadmap = format_planner_roadmap(plan)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(roadmap + "\n", encoding="utf-8")

    print(roadmap)
    if args.out is not None:
        print()
        print("Output:")
        print(f"  {args.out}")

    return 1 if "Status:\nblocked" in roadmap else 0


def run_planner_mark(args: argparse.Namespace) -> int:
    """Update a local planner task status."""
    result = mark_planner_task(args.plan, args.task, args.status, args.reason)
    print(format_planner_mark_result(args.plan, result))
    return 0 if result["status"] == "updated" else 1


def run_planner_next(args: argparse.Namespace) -> int:
    """Choose the next dependency-ready issue from a local planner draft."""
    plan = load_planner_plan(args.plan)
    next_plan = build_planner_next(plan)
    print(format_planner_next(args.plan, next_plan))
    return 1 if next_plan["status"] == "blocked" else 0


def run_planner_seed(args: argparse.Namespace) -> int:
    """Plan GitHub issue creation from a local planner draft."""
    plan = load_planner_plan(args.plan)
    seed_plan = build_planner_seed_plan(plan)
    print(format_planner_seed_plan(args.plan, seed_plan))
    return 0 if seed_plan["status"] == "ready" else 1


def run_planner_validate(args: argparse.Namespace) -> int:
    """Validate a local planner draft."""
    plan = load_planner_plan(args.plan)
    errors = validate_planner_plan(plan)
    print(format_planner_validation(args.plan, errors))
    return 0 if not errors else 1


def run_planner_draft(args: argparse.Namespace) -> None:
    """Write a local deterministic planner draft."""
    plan = write_planner_draft(args.goal, args.out)
    print(format_planner_draft(plan, args.out))
