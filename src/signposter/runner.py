"""Signposter runner planner (dry-run only).

Determines how a selected claimable item would be executed via OpenClaw.
"""

from __future__ import annotations

from dataclasses import dataclass

from signposter.claim import plan_claims
from signposter.dispatch import DispatchDecision
from signposter.scan import LabeledItem


@dataclass(frozen=True)
class RunnerPlan:
    """Represents the planned execution for a single item."""

    item: LabeledItem
    dispatch: DispatchDecision
    proposed_runner: str
    proposed_profile: str
    proposed_working_dir: str
    proposed_prompt_path: str
    proposed_command_shape: str
    reason: str


def _select_runner_and_profile(dispatch: DispatchDecision) -> tuple[str, str]:
    """Map role + phase to runner + OpenClaw profile."""
    role = dispatch.role or ""
    phase = dispatch.phase or ""

    if role == "worker" and phase == "build":
        return "openclaw", "worker"
    elif role == "reviewer" and phase == "review":
        return "openclaw", "reviewer"
    elif role == "planner" and phase == "plan":
        return "openclaw", "planner"
    elif role == "gatekeeper":
        return "openclaw", "gatekeeper"
    else:
        # Conservative default
        return "openclaw", "worker"


def plan_runner(repo: str, *, limit: int = 1) -> list[RunnerPlan]:
    """Produce runner plans for claimable items.

    Reuses the conservative claim planner (limit + deterministic ordering).
    """
    claim_result = plan_claims(repo, limit=limit)
    plans: list[RunnerPlan] = []

    for claim_plan in claim_result.selected:
        dispatch = claim_plan.dispatch
        item = claim_plan.item

        runner, profile = _select_runner_and_profile(dispatch)

        # Proposed paths (dry-run only)
        working_dir = f"~/projects/signposter-work/{item.number}"
        prompt_path = f"artifacts/prompts/issue-{item.number}.md"
        command_shape = (
            f"openclaw run --profile {profile} "
            f"--prompt {prompt_path} --cwd {working_dir}"
        )

        reason = (
            f"Selected via claim planner for route='{dispatch.proposed_route}' "
            f"(role={dispatch.role}, phase={dispatch.phase})"
        )

        plan = RunnerPlan(
            item=item,
            dispatch=dispatch,
            proposed_runner=runner,
            proposed_profile=profile,
            proposed_working_dir=working_dir,
            proposed_prompt_path=prompt_path,
            proposed_command_shape=command_shape,
            reason=reason,
        )
        plans.append(plan)

    return plans


def format_runner_plan(plans: list[RunnerPlan]) -> str:
    """Human-readable dry-run output for runner planning."""
    if not plans:
        return "Signposter Run Dry-Run: No claimable items found.\n"

    lines = ["Signposter Run Dry-Run Plan\n"]

    for i, plan in enumerate(plans, 1):
        item = plan.item
        d = plan.dispatch

        lines.append(f"[{i}] ISSUE #{item.number} — {item.title}")
        lines.append(f"    URL: {item.html_url}")
        lines.append("")
        lines.append("    Classification:")
        lines.append(f"      route:  {d.proposed_route}")
        lines.append(f"      phase:  {d.phase}")
        lines.append(f"      role:   {d.role}")
        lines.append(f"      risk:   {d.risk}")
        lines.append(f"      area:   {d.area}")
        lines.append(f"      gate:   {d.proposed_gate}")
        lines.append("")
        lines.append("    Proposed Execution:")
        lines.append(f"      runner:           {plan.proposed_runner}")
        lines.append(f"      openclaw_profile: {plan.proposed_profile}")
        lines.append(f"      working_dir:      {plan.proposed_working_dir}")
        lines.append(f"      prompt_artifact:  {plan.proposed_prompt_path}")
        lines.append(f"      command_shape:    {plan.proposed_command_shape}")
        lines.append("")
        lines.append(f"    Reason: {plan.reason}")
        lines.append("")

    lines.append(f"Total items planned for execution: {len(plans)}")
    lines.append(
        "Note: This is a DRY RUN. "
        "No OpenClaw session was started and no artifacts were written."
    )

    return "\n".join(lines)


def cli_main(repo: str, limit: int = 1) -> int:
    """Entry point for the run command."""
    try:
        plans = plan_runner(repo, limit=limit)
        print(format_runner_plan(plans))
        return 0
    except Exception as e:
        print(f"Run dry-run failed: {e}", file=__import__("sys").stderr)
        return 1


def main() -> int:
    """Direct CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Runner planner (dry-run)")
    parser.add_argument("--repo", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Required: run in dry-run mode only",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of items to plan (default: 1)",
    )
    args = parser.parse_args()

    return cli_main(args.repo, limit=args.limit)


if __name__ == "__main__":
    import sys
    sys.exit(main())
