"""Signposter runner planner (dry-run only).

Determines how a selected claimable item would be executed via OpenClaw.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from signposter.claim import perform_claim_mutation, plan_claims
from signposter.dispatch import DispatchDecision, classify_candidate
from signposter.scan import LabeledItem, fetch_issue_by_number, fetch_issue_context


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

        # Realistic OpenClaw invocation (as of 2026.5):
        # - No "openclaw run" subcommand exists.
        # - Use "openclaw agent --message" with a session selector.
        # - The signposter "profile" (reviewer/worker/...) maps to an OpenClaw agent id
        #   or routing binding that has the appropriate skills loaded.
        # - Prompt content is passed via --message (or heredoc in real scripts).
        # - Working directory is typically managed via the prompt instructions or agent workspace.
        command_shape = (
            f"openclaw agent --agent {profile} "
            f"--session-key signposter-issue-{item.number} "
            f"--message \"$(cat {prompt_path})\" --local"
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


def plan_runner_for_issue(repo: str, issue: int) -> RunnerPlan | None:
    """Build a RunnerPlan for one specific issue by number.

    Works for state:ready, state:active, etc. Does not filter by claimability.
    Returns None if the issue cannot be fetched.
    """
    item = fetch_issue_by_number(repo, issue)
    if not item:
        return None

    dispatch = classify_candidate(item)
    runner, profile = _select_runner_and_profile(dispatch)

    working_dir = f"~/projects/signposter-work/{item.number}"
    prompt_path = f"artifacts/prompts/issue-{item.number}.md"

    command_shape = (
        f"openclaw agent --agent {profile} "
        f"--session-key signposter-issue-{item.number}-{profile} "
        f"--message \"$(cat {prompt_path})\" --local"
    )

    reason = (
        f"Explicit target via --issue for route='{dispatch.proposed_route}' "
        f"(role={dispatch.role}, phase={dispatch.phase}, state={dispatch.state})"
    )

    return RunnerPlan(
        item=item,
        dispatch=dispatch,
        proposed_runner=runner,
        proposed_profile=profile,
        proposed_working_dir=working_dir,
        proposed_prompt_path=prompt_path,
        proposed_command_shape=command_shape,
        reason=reason,
    )


def _prompt_issue_number(prompt_path: Path) -> int | None:
    """Extract issue number from artifacts/prompts/issue-N.md."""
    stem = prompt_path.stem
    prefix = "issue-"
    if not stem.startswith(prefix):
        return None

    try:
        return int(stem[len(prefix):])
    except ValueError:
        return None


def plan_active_runner_from_prompts(repo: str, *, limit: int = 1) -> list[RunnerPlan]:
    """Produce runner plans for already-active items with existing prompt artifacts."""
    prompt_dir = Path("artifacts/prompts")
    if not prompt_dir.exists():
        return []

    prompt_candidates: list[tuple[int, Path]] = []
    for prompt_path in prompt_dir.glob("issue-*.md"):
        issue_number = _prompt_issue_number(prompt_path)
        if issue_number is not None:
            prompt_candidates.append((issue_number, prompt_path))

    plans: list[RunnerPlan] = []

    for issue_number, prompt_path in sorted(prompt_candidates, key=lambda x: x[0], reverse=True):
        item = fetch_issue_by_number(repo, issue_number)
        if not item:
            continue

        labels = {label.lower() for label in item.labels}
        if "state:active" not in labels:
            continue

        dispatch = classify_candidate(item)
        runner, profile = _select_runner_and_profile(dispatch)

        working_dir = f"~/projects/signposter-work/{item.number}"
        prompt_path_str = str(prompt_path)
        command_shape = (
            f"openclaw agent --agent {profile} "
            f"--session-key signposter-issue-{item.number}-{profile} "
            f'--message "$(cat {prompt_path_str})" --local'
        )

        plans.append(
            RunnerPlan(
                item=item,
                dispatch=dispatch,
                proposed_runner=runner,
                proposed_profile=profile,
                proposed_working_dir=working_dir,
                proposed_prompt_path=prompt_path_str,
                proposed_command_shape=command_shape,
                reason="Already-active item with existing prompt artifact",
            )
        )

        if len(plans) >= limit:
            break

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
        "--claim",
        action="store_true",
        help="Actually claim the selected item(s) on GitHub (requires explicit use)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run the OpenClaw agent locally for the selected item (explicit, read-only on GitHub)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum number of items to plan (default: 1)",
    )
    parser.add_argument(
        "--issue",
        type=int,
        help="Target a specific issue number explicitly (bypasses claim planner)",
    )
    args = parser.parse_args()

    write_prompt = args.write_prompt
    claim = args.claim
    execute = args.execute
    issue = getattr(args, "issue", None)

    return cli_main(
        args.repo,
        limit=args.limit,
        write_prompt=write_prompt,
        claim=claim,
        execute=execute,
        issue=issue,
    )  # noqa: E501


# --- Prompt Artifact Generation ---

def _get_role_profile(role: str | None) -> str:
    """Return compact role-specific profile instructions."""
    role = (role or "").lower()
    if role == "reviewer":
        return """# Reviewer Profile
You are the Signposter reviewer.
Your job is to review embedded evidence, identify risks, and recommend next steps.
Do not mutate GitHub.
Do not edit files.
Do not commit.
Do not fetch private GitHub URLs.
Use only embedded context and local artifacts provided in this prompt.
If evidence is missing, say exactly what is missing.
Prefer concise, actionable review findings."""
    elif role == "worker":
        return """# Worker Profile
You are the Signposter worker.
Implement only the scoped, low-risk changes described in the embedded context.
Do not broaden scope. Report all changes with evidence."""
    elif role == "planner":
        return """# Planner Profile
You are the Signposter planner.
Create a clear plan/roadmap only. Break work into phases with success criteria.
Do not implement changes."""
    elif role == "gatekeeper":
        return """# Gatekeeper Profile
You are the Signposter gatekeeper.
Review evidence strictly and decide pass/fail on the gate.
Cite specific observations. Be conservative."""
    else:
        return """# Agent Profile
Execute the task according to the classification and constraints below."""


def _ensure_evidence_dir(number: int) -> Path:
    """Ensure artifacts/evidence/issue-<number>/ exists."""
    path = Path(f"artifacts/evidence/issue-{number}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _capture_command(cmd: list[str], timeout: int = 30) -> str:
    """Run a command and return stdout or error message.

    Tries the bare command first, then falls back to common venv locations.
    """
    candidates = [cmd]
    # Common venv location when running from source tree
    if cmd[0] == "signposter":
        venv_bin = Path(".venv/bin/signposter")
        if venv_bin.exists():
            candidates.append([str(venv_bin)] + cmd[1:])

    for c in candidates:
        try:
            result = subprocess.run(c, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                return result.stdout.strip()
            return f"[command failed: {result.stderr.strip()[:200]}]"
        except Exception as e:
            last_err = str(e)
    return f"[error: {last_err}]"


def collect_evidence_bundle(repo: str, number: int, plan: RunnerPlan | None = None) -> dict:
    """Collect current evidence for reviewer/gatekeeper prompts.

    Saves snapshots to artifacts/evidence/issue-<number>/
    """
    evidence: dict = {}
    evidence_dir = _ensure_evidence_dir(number)

    # Current scan output (exact CLI view)
    scan_out = _capture_command(["signposter", "scan", "--repo", repo])
    evidence["scan"] = scan_out
    (evidence_dir / "scan.txt").write_text(scan_out)

    # Claim dry-run (useful context for reviewer)
    claim_dry = _capture_command(["signposter", "claim", "--repo", repo, "--dry-run"])
    evidence["claim_dry_run"] = claim_dry
    (evidence_dir / "claim-dry-run.txt").write_text(claim_dry)

    # Recent CI runs
    runs_out = _capture_command([
        "gh", "run", "list", "-R", repo, "--limit", "5",
        "--json", "status,conclusion,workflowName,headBranch,updatedAt"
    ])
    evidence["recent_runs"] = runs_out
    (evidence_dir / "runs.txt").write_text(runs_out)

    # Working directory status
    working_dir = plan.proposed_working_dir if plan else f"~/projects/signposter-work/{number}"
    try:
        expanded = os.path.expanduser(working_dir)
        exists = os.path.isdir(expanded)
    except Exception:
        exists = False

    evidence["working_dir"] = working_dir
    evidence["working_dir_status"] = "prepared" if exists else "not prepared yet"

    # Prompt artifact details
    prompt_path = plan.proposed_prompt_path if plan else f"artifacts/prompts/issue-{number}.md"
    prompt_exists = os.path.isfile(prompt_path)
    prompt_preview = "(prompt file not found)"

    if prompt_exists:
        try:
            with open(prompt_path, encoding="utf-8") as f:
                preview = f.read(2500)  # bounded preview
            lines = preview.splitlines()[:80]
            prompt_preview = "\n".join(lines)
        except Exception as e:
            prompt_preview = f"(error reading prompt: {e})"

    evidence["prompt_path"] = prompt_path
    evidence["prompt_exists"] = prompt_exists
    evidence["prompt_preview"] = prompt_preview

    if plan:
        evidence["command_shape"] = plan.proposed_command_shape

    evidence["note"] = (
        "Use the embedded evidence below. Do not fetch GitHub URLs. "
        "A missing working_dir is not a failure before execution. "
        "Treat it as pending preparation unless this task is an execution step."
    )

    return evidence


def render_prompt(
    plan: RunnerPlan,
    repo: str,
    issue_context: dict | None = None,
    evidence_bundle: dict | None = None,
) -> str:
    """Generate the full prompt artifact content for a RunnerPlan.

    When issue_context is provided (from authenticated gh issue view), the prompt
    becomes self-contained for private repositories.
    evidence_bundle is added for reviewer/gatekeeper roles.
    """
    item = plan.item
    d = plan.dispatch

    # Use rich context if available, else fall back to labels from item
    if issue_context:
        labels = [lbl["name"] for lbl in issue_context.get("labels", [])]
        labels_str = ", ".join(labels) if labels else "(none)"
        body = issue_context.get("body") or ""
        body_text = body.strip() if body.strip() else "Issue body: empty"
        state = issue_context.get("state", "unknown")
        comments = issue_context.get("comments", [])
        comments_text = ""
        if comments:
            recent = comments[-3:]  # last 3 comments
            comments_lines = []
            for c in recent:
                author = c.get("author", {}).get("login", "unknown")
                body_snip = (c.get("body", "") or "")[:300]
                comments_lines.append(f"- @{author}: {body_snip}")
            comments_text = "\n".join(comments_lines)
        else:
            comments_text = "(no comments)"
        issue_state = state
    else:
        labels_str = ", ".join(item.labels) if item.labels else "(none)"
        body_text = "Issue body: not embedded (context fetch failed)"
        comments_text = "(not embedded)"
        issue_state = d.state or "unknown"

    role_profile = _get_role_profile(d.role)

    task_instruction = {
        "reviewer": (
            "Review the embedded issue context and any attached artifacts. "
            "Identify risks, gaps, and propose clear next steps."
        ),
        "worker": "Implement only the scoped changes described in the embedded context.",
        "planner": "Create a clear, phased plan based on the embedded context.",
        "gatekeeper": (
            "Evaluate the embedded evidence against the gate criteria and decide pass/fail."
        ),
    }.get(d.role or "", "Execute the task using only the provided embedded context.")

    private_rule = (
        "Do not fetch the GitHub URL. This is a private repository. "
        "Use only the embedded issue context, labels, body, and local artifacts "
        "included in this prompt."
    )

    # Evidence Bundle (only for reviewer/gatekeeper)
    evidence_section = ""
    if evidence_bundle and d.role in ("reviewer", "gatekeeper"):
        scan = evidence_bundle.get("scan", "(no scan output)")
        claim_dry = evidence_bundle.get("claim_dry_run", "(no claim dry-run)")
        runs = evidence_bundle.get("recent_runs", "(no runs)")
        wd = evidence_bundle.get("working_dir", "unknown")
        wd_status = evidence_bundle.get("working_dir_status", "unknown")
        prompt_path = evidence_bundle.get("prompt_path", plan.proposed_prompt_path)
        prompt_exists = evidence_bundle.get("prompt_exists", False)
        prompt_preview = evidence_bundle.get("prompt_preview", "(no preview)")
        cmd_shape = evidence_bundle.get("command_shape", plan.proposed_command_shape)

        evidence_section = f"""

## Evidence Bundle
{evidence_bundle.get("note", "Use the embedded evidence below. Do not fetch GitHub URLs.")}

**Current Scan Output:**
{scan}

**Claim Dry-Run:**
{claim_dry}

**Recent CI Runs (last 5):**
{runs}

**Working Directory:** {wd}
**Status:** {wd_status}

**Prompt Artifact:** {prompt_path}
**Exists:** {prompt_exists}

**Prompt Preview (first ~80 lines or bounded):**
{prompt_preview}

**Command Shape:** {cmd_shape}
"""

    content = f"""# Signposter Task Prompt

## Role Profile
{role_profile}

## Private Repository Rule
{private_rule}

## Issue Context
**Repository:** {repo}
**Issue:** #{item.number} — {item.title}
**URL (reference only):** {item.html_url}
**State:** {issue_state}

**Labels:** {labels_str}

**Body:**
{body_text}

**Recent Comments:**
{comments_text}

## Workflow State
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
- prompt_artifact: {plan.proposed_prompt_path}{evidence_section}

## Operator Constraints
- Do not broaden scope beyond this issue.
- Do not mutate GitHub unless explicitly instructed in a later step.
- Do not commit unless explicitly instructed.
- Report findings with evidence.

## Task
{task_instruction}

---

Begin execution following the constraints and role profile above.
"""
    return content


def write_prompt_artifact(plan: RunnerPlan, repo: str) -> str:
    """Render and write the prompt artifact to disk.

    Fetches full issue context + evidence bundle (for reviewer/gatekeeper).
    Returns the path of the written file.
    """
    context = fetch_issue_context(repo, plan.item.number)

    evidence = None
    role = (plan.dispatch.role or "").lower()
    if role in ("reviewer", "gatekeeper"):
        evidence = collect_evidence_bundle(repo, plan.item.number, plan)

    content = render_prompt(plan, repo, issue_context=context, evidence_bundle=evidence)
    path = plan.proposed_prompt_path

    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return path


def cli_main(
    repo: str,
    limit: int = 1,
    *,
    write_prompt: bool = False,
    claim: bool = False,
    execute: bool = False,
    issue: int | None = None,
) -> int:  # noqa: E501
    """Entry point for the run command.

    If `issue` is provided, operates on that specific issue only (explicit targeting).
    Otherwise falls back to the normal claim-planner path.
    """
    try:
        if issue is not None:
            # Explicit single-issue targeting path (HARDENING-004)
            plan = plan_runner_for_issue(repo, issue)
            plans = [plan] if plan else []
            if not plans:
                print(f"Error: Could not fetch issue #{issue} from {repo}.")
                return 1

            print(f"Signposter Run Plan — Explicit target issue #{issue}")
            print(format_runner_plan(plans))

            # HARDENING-004 micro-adjustment: explicit status for terminal states
            if plans:
                st = (plans[0].dispatch.state or "").lower()
                if st in ("done", "failed"):
                    print(f"Execution status: blocked — state:{st}")

            # Handle explicit single-issue actions
            plan = plans[0]
            item_number = plan.item.number
            current_state = (plan.dispatch.state or "").lower()

            if claim:
                if current_state == "ready":
                    print("\n=== APPLYING CLAIM MUTATION (explicit --issue) ===\n")
                    # Use the normal claim planner but limited to this issue conceptually.
                    # For safety we still go through plan_claims + filter (conservative).
                    claim_result = plan_claims(repo, limit=1)
                    for claim_plan in claim_result.selected:
                        if claim_plan.item.number == item_number:
                            print(f"Claiming issue #{item_number}...")
                            commands = perform_claim_mutation(claim_plan, repo, dry_run=False)
                            for cmd in commands:
                                print(f"  Executed: {cmd}")
                    print("Claim mutation complete.")
                else:
                    msg = f"  Note: issue #{item_number} already {current_state}. Skipping claim."
                    print(msg)

            final_plans = plans

            if write_prompt and final_plans:
                print("\n=== Writing Prompt Artifact(s) ===\n")
                for p in final_plans:
                    path = write_prompt_artifact(p, repo)
                    print(f"Wrote: {path}")

            if execute and final_plans:
                print("\n=== EXECUTING RUNNER (OpenClaw) ===\n")
                for p in final_plans:
                    st = (p.dispatch.state or "").lower()
                    if st != "active":
                        msg = f"  Refusing to execute issue #{p.item.number}: state={st}"
                        print(msg + " (requires state:active)")
                        continue
                    result = execute_plan(p, repo)
                    print(f"Execution completed for issue #{p.item.number}")
                    print(f"  Exit code: {result.get('exit_code')}")
                    print(f"  Raw output: {result.get('raw_path')}")
                    print(f"  Summary:   {result.get('summary_path')}")

            return 0

        else:
            plans = plan_runner(repo, limit=limit)
            print(format_runner_plan(plans))

        claimed_numbers: list[int] = []
        if claim and plans:
            print("\n=== APPLYING CLAIM MUTATION (from run command) ===\n")
            # We need the original ClaimPlan objects for mutation
            claim_result = plan_claims(repo, limit=limit)
            for claim_plan in claim_result.selected:
                print(f"Claiming issue #{claim_plan.item.number}...")
                commands = perform_claim_mutation(claim_plan, repo, dry_run=False)
                for cmd in commands:
                    print(f"  Executed: {cmd}")
                claimed_numbers.append(claim_plan.item.number)
            print("Claim mutation complete.")

        # If we claimed items and want to write prompts, re-fetch current state
        # so the artifact reflects post-claim labels (e.g. state:active + gate:review)
        final_plans = plans
        if write_prompt and claimed_numbers:
            print("\n=== Refreshing plans from current GitHub state (post-claim) ===\n")
            refreshed: list[RunnerPlan] = []
            for num in claimed_numbers:
                fresh_item = fetch_issue_by_number(repo, num)
                if not fresh_item:
                    print(f"  Warning: could not re-fetch issue #{num}, using stale plan")
                    continue
                fresh_dispatch = classify_candidate(fresh_item)
                # Rebuild RunnerPlan with fresh item + dispatch (preserve other fields)
                old_plan = next((p for p in plans if p.item.number == num), None)
                if old_plan:
                    refreshed.append(
                        RunnerPlan(
                            item=fresh_item,
                            dispatch=fresh_dispatch,
                            proposed_runner=old_plan.proposed_runner,
                            proposed_profile=old_plan.proposed_profile,
                            proposed_working_dir=old_plan.proposed_working_dir,
                            proposed_prompt_path=old_plan.proposed_prompt_path,
                            proposed_command_shape=old_plan.proposed_command_shape,
                            reason=old_plan.reason,
                        )
                    )
            if refreshed:
                final_plans = refreshed
                print(f"  Refreshed {len(refreshed)} plan(s) with current labels.")

        if write_prompt and final_plans:
            print("\n=== Writing Prompt Artifact(s) ===\n")
            for plan in final_plans:
                path = write_prompt_artifact(plan, repo)
                print(f"Wrote: {path}")

        if execute and final_plans:
            print("\n=== EXECUTING RUNNER (OpenClaw) ===\n")
            for plan in final_plans:
                state = (plan.dispatch.state or "").lower()
                if state == "ready" and not claim:
                    print(f"  Refusing to execute issue #{plan.item.number}: state=ready without --claim. Use --claim --execute to claim + run.")  # noqa: E501
                    continue

                result = execute_plan(plan, repo)
                print(f"Execution completed for issue #{plan.item.number}")
                print(f"  Exit code: {result.get('exit_code')}")
                print(f"  Raw output: {result.get('raw_path')}")
                print(f"  Summary:   {result.get('summary_path')}")

        # Fallback for --execute on already-active items with existing prompt artifacts.
        elif execute and not final_plans:
            print('\n=== EXECUTING RUNNER (OpenClaw) - active item fallback ===\n')
            try:
                active_plans = plan_active_runner_from_prompts(repo, limit=limit)
                if not active_plans:
                    print("No active items with prompt artifacts found.")
                for plan in active_plans:
                    result = execute_plan(plan, repo)
                    print(f"Execution completed for issue #{plan.item.number} (active fallback)")
                    print(f"  Exit code: {result.get('exit_code')}")
                    print(f"  Raw output: {result.get('raw_path')}")
                    print(f"  Summary:   {result.get('summary_path')}")
            except Exception as e:
                print(f"Active fallback execution failed: {e}")

        return 0
    except Exception as e:
        print(f"Run failed: {e}", file=__import__("sys").stderr)
        return 1


# --- Execution Layer (for --execute) ---

def execute_plan(plan: RunnerPlan, repo: str) -> dict:
    """Execute the runner plan using OpenClaw (local only).

    Safety: This function assumes the item is already in an executable state
    (e.g. state:active). It does not perform claims.
    """
    import datetime

    item = plan.item
    profile = plan.proposed_profile or "worker"
    prompt_path = plan.proposed_prompt_path
    session_key = f"signposter-issue-{item.number}-{profile}"

    # Read the prompt content (we pass it properly, not via shell substitution)
    try:
        with open(prompt_path, encoding="utf-8") as f:
            prompt_content = f.read()
    except Exception as e:
        raise RuntimeError(f"Could not read prompt artifact {prompt_path}: {e}") from e

    # Final command for execution (no shell substitution)
    exec_cmd = [
        "openclaw", "agent",
        "--agent", profile,
        "--session-key", session_key,
        "--message", prompt_content,
        "--local",
    ]

    print(f"Running: openclaw agent --agent {profile} --session-key {session_key} --local")
    print(f"Using prompt: {prompt_path} (length: {len(prompt_content)} chars)")

    # Ensure output directory
    runs_dir = Path("artifacts/runs")
    runs_dir.mkdir(parents=True, exist_ok=True)

    raw_path = runs_dir / f"issue-{item.number}-{profile}.raw.txt"
    summary_path = runs_dir / f"issue-{item.number}-{profile}.summary.md"

    start_time = datetime.datetime.now(datetime.UTC)

    try:
        proc = subprocess.run(
            exec_cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute safety timeout
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        combined = stdout
        if stderr:
            combined += "\n\n=== STDERR ===\n" + stderr

        exit_code = proc.returncode

        # Write raw output
        raw_path.write_text(combined, encoding="utf-8")

        # Generate summary
        summary = _generate_execution_summary(
            repo=repo,
            plan=plan,
            session_key=session_key,
            exit_code=exit_code,
            raw_path=str(raw_path),
            stdout=stdout,
            stderr=stderr,
            start_time=start_time,
        )
        summary_path.write_text(summary, encoding="utf-8")

        return {
            "exit_code": exit_code,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "success": exit_code == 0,
        }

    except subprocess.TimeoutExpired as e:
        raw_path.write_text(f"[TIMEOUT after 600s]\n{e}", encoding="utf-8")
        return {"exit_code": -1, "raw_path": str(raw_path), "success": False}
    except Exception as e:
        raw_path.write_text(f"[ERROR]\n{e}", encoding="utf-8")
        return {"exit_code": -1, "raw_path": str(raw_path), "success": False}


def _generate_execution_summary(
    *, repo: str, plan: RunnerPlan, session_key: str, exit_code: int,
    raw_path: str, stdout: str, stderr: str, start_time,
) -> str:
    """Generate a mechanical summary for the execution run."""
    item = plan.item
    lines = [
        "# Signposter Execution Summary\n",
        f"**Repository:** {repo}",
        f"**Issue:** #{item.number} — {item.title}",
        f"**Agent:** {plan.proposed_profile}",
        f"**Session Key:** {session_key}",
        f"**Command Shape:** {plan.proposed_command_shape}",
        f"**Prompt Artifact:** {plan.proposed_prompt_path}",
        f"**Started (UTC):** {start_time.isoformat()}",
        f"**Exit Code:** {exit_code}",
        f"**Raw Output:** {raw_path}",
        "",
    ]

    # Basic stats
    raw_text = stdout + ("\n" + stderr if stderr else "")
    line_count = len(raw_text.splitlines())
    byte_count = len(raw_text.encode("utf-8"))
    lines.append(f"**Output Size:** {line_count} lines, {byte_count} bytes")

    # Excerpts
    lines.append("\n## First 30 lines of output\n")
    first_lines = raw_text.splitlines()[:30]
    lines.append("```\n" + "\n".join(first_lines) + "\n```")

    if line_count > 60:
        lines.append("\n## Last 20 lines of output\n")
        last_lines = raw_text.splitlines()[-20:]
        lines.append("```\n" + "\n".join(last_lines) + "\n```")

    lines.append("\n---\nGenerated by Signposter runner (local execution capture)")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.exit(main())
