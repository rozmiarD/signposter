"""Signposter runner planner (dry-run only).

Determines how a selected claimable item would be executed via OpenClaw.
"""

from __future__ import annotations

import os
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


def main() -> int:
    """Direct CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Runner planner")
    parser.add_argument("--repo", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in read-only planning mode (default)",
    )
    parser.add_argument(
        "--write-prompt",
        action="store_true",
        help="Generate and write the prompt artifact file locally",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of items to plan (default: 1)",
    )
    args = parser.parse_args()

    # Default to dry-run behavior if neither flag is passed
    write_prompt = args.write_prompt

    return cli_main(args.repo, limit=args.limit, write_prompt=write_prompt)


# --- Prompt Artifact Generation ---

def render_prompt(plan: RunnerPlan, repo: str) -> str:
    """Generate the full prompt artifact content for a RunnerPlan.

    This function is pure and contains no I/O.
    """
    item = plan.item
    d = plan.dispatch

    labels_str = ", ".join(item.labels) if item.labels else "(none)"

    task_instruction = {
        "reviewer": (
            "Review the issue/request and propose next steps. "
            "Do not edit files yet. Provide clear findings, risks, and recommended actions."
        ),
        "worker": (
            "Implement only the scoped, low-risk changes described. "
            "Do not broaden scope. Report all changes made."
        ),
        "planner": (
            "Create a clear plan/roadmap only. Do not implement changes. "
            "Break the work into phases with clear success criteria."
        ),
        "gatekeeper": (
            "Review the provided evidence and decide pass/fail on the gate. "
            "Be strict and cite specific observations."
        ),
    }.get(d.role or "", "Execute the task according to the classification above.")

    content = f"""# Signposter Task Prompt

**Repository:** {repo}
**Issue:** #{item.number} — {item.title}
**URL:** {item.html_url}

## Labels
{labels_str}

## Classification
- route:  {d.proposed_route}
- phase:  {d.phase}
- role:   {d.role}
- risk:   {d.risk}
- area:   {d.area}
- gate:   {d.proposed_gate}

## Proposed Execution
- runner: openclaw
- profile: {plan.proposed_profile}
- working_dir: {plan.proposed_working_dir}
- prompt_artifact: {plan.proposed_prompt_path}

## Operator Constraints
- Do not broaden scope beyond this issue.
- Do not mutate GitHub unless explicitly instructed in a later step.
- Do not commit unless explicitly instructed.
- Report findings with evidence.

## Task
{task_instruction}

---

Begin execution following the constraints above.
"""
    return content


def write_prompt_artifact(plan: RunnerPlan, repo: str) -> str:
    """Render and write the prompt artifact to disk.

    Returns the path of the written file.
    Creates parent directories if needed.
    """
    content = render_prompt(plan, repo)
    path = plan.proposed_prompt_path

    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return path


def cli_main(repo: str, limit: int = 1, *, write_prompt: bool = False) -> int:
    """Entry point for the run command."""
    try:
        plans = plan_runner(repo, limit=limit)
        print(format_runner_plan(plans))

        if write_prompt and plans:
            print("\n=== Writing Prompt Artifact(s) ===\n")
            for plan in plans:
                path = write_prompt_artifact(plan, repo)
                print(f"Wrote: {path}")

        return 0
    except Exception as e:
        print(f"Run failed: {e}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
