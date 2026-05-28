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
from signposter.gate import format_gate_report, run_gate_dry_run
from signposter.report import report_main
from signposter.runner import cli_main as runner_cli_main
from signposter.scan import cli_main as scan_cli_main
from signposter.transitions import (
    format_transition_plan,
    perform_transition_mutation,
    run_transition_dry_run,
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
    run_parser.set_defaults(func=run_runner)

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
        print("\n=== APPLYING RELEASE MUTATION ===\n")
        commands = perform_transition_mutation(plan, repo, dry_run=False)
        for cmd in commands:
            print(f"  Executed: {cmd}")
        print("\nRelease mutation complete.")
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
        print("\n=== APPLYING COMPLETE MUTATION ===\n")
        commands = perform_transition_mutation(plan, repo, dry_run=False)
        for cmd in commands:
            print(f"  Executed: {cmd}")
        print("\nComplete mutation complete.")
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
    return runner_cli_main(
        repo, limit=limit, write_prompt=write_prompt, claim=claim, execute=execute, issue=issue
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
