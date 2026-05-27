"""Signposter Command Line Interface.

Currently in bootstrap phase. Only the `doctor` command is implemented.
"""

from __future__ import annotations

import argparse
import sys

from signposter.claim import cli_main as claim_cli_main
from signposter.dispatch import cli_main as dispatch_cli_main
from signposter.doctor import main as doctor_main
from signposter.scan import cli_main as scan_cli_main
from signposter.transitions import format_transition_plan, run_transition_dry_run


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

    # release / complete / fail subcommands (dry-run only for now)
    release_parser = subparsers.add_parser(
        "release",
        help="Release an active item back to ready (dry-run only)",
    )
    release_parser.add_argument("--repo", required=True)
    release_parser.add_argument("--issue", type=int, required=True)
    release_parser.add_argument("--dry-run", action="store_true", required=True)
    release_parser.set_defaults(func=run_release)

    complete_parser = subparsers.add_parser(
        "complete",
        help="Mark an active item as successfully completed (dry-run only)",
    )
    complete_parser.add_argument("--repo", required=True)
    complete_parser.add_argument("--issue", type=int, required=True)
    complete_parser.add_argument("--dry-run", action="store_true", required=True)
    complete_parser.set_defaults(func=run_complete)

    fail_parser = subparsers.add_parser(
        "fail",
        help="Mark an active item as failed (dry-run only)",
    )
    fail_parser.add_argument("--repo", required=True)
    fail_parser.add_argument("--issue", type=int, required=True)
    fail_parser.add_argument("--dry-run", action="store_true", required=True)
    fail_parser.set_defaults(func=run_fail)

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
    """Execute release dry-run."""
    repo = getattr(args, "repo", None)
    issue = getattr(args, "issue", None)
    if not repo or issue is None:
        print("Error: --repo and --issue are required", file=sys.stderr)
        return 1
    plan = run_transition_dry_run(repo, issue, "release")
    print(format_transition_plan(plan))
    return 0 if plan.valid else 1


def run_complete(args: argparse.Namespace) -> int:
    """Execute complete dry-run."""
    repo = getattr(args, "repo", None)
    issue = getattr(args, "issue", None)
    if not repo or issue is None:
        print("Error: --repo and --issue are required", file=sys.stderr)
        return 1
    plan = run_transition_dry_run(repo, issue, "complete")
    print(format_transition_plan(plan))
    return 0 if plan.valid else 1


def run_fail(args: argparse.Namespace) -> int:
    """Execute fail dry-run."""
    repo = getattr(args, "repo", None)
    issue = getattr(args, "issue", None)
    if not repo or issue is None:
        print("Error: --repo and --issue are required", file=sys.stderr)
        return 1
    plan = run_transition_dry_run(repo, issue, "fail")
    print(format_transition_plan(plan))
    return 0 if plan.valid else 1


if __name__ == "__main__":
    main()
