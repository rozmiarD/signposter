"""Signposter runner planner (dry-run only).

Determines how a selected claimable item would be executed via a backend.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from signposter.bug_ledger import (
    format_runtime_bug_ledger_record,
    record_runtime_bug_ledger_entry,
)
from signposter.claim import build_claim_plan, perform_claim_mutation, plan_claims
from signposter.codex_cli_backend import (
    execute_codex_cli_invocation,
    plan_codex_cli_invocation,
)
from signposter.dependencies import is_dependency_blocked
from signposter.dispatch import DispatchDecision, classify_candidate
from signposter.execution_backend import (
    build_backend_command_shape,
    resolve_execution_backend,
)
from signposter.git_utils import find_uncommitted_repo_changes
from signposter.openclaw_diagnostics import gather_openclaw_runtime_diagnostics
from signposter.openclaw_preflight import (
    check_openclaw_preflight,
    format_openclaw_preflight_block,
)
from signposter.openclaw_runtime import (
    OpenClawExecutionDiagnosis,
    classify_openclaw_execution,
    normalize_subprocess_output,
    openclaw_timeout_settings,
)
from signposter.role_policy import execution_agent_for_backend, get_role_policy
from signposter.role_routing import resolve_role_execution, select_role_for_issue
from signposter.scan import LabeledItem, fetch_issue_by_number, fetch_issue_context
from signposter.worktree import get_worktree_status_for_issue

DEFAULT_OPENCLAW_SESSION_NAMESPACE = "v2"
OPENCLAW_SESSION_NAMESPACE_ENV = "SIGNPOSTER_OPENCLAW_SESSION_NAMESPACE"
PROMPT_COMPACTION_LIMITS = {
    "issue_body_lines": 48,
    "issue_body_chars": 3200,
    "comments_lines": 16,
    "comments_chars": 1200,
    "worker_issue_body_lines": 32,
    "worker_issue_body_chars": 2200,
    "worker_comments_lines": 8,
    "worker_comments_chars": 700,
    "scan_lines": 60,
    "scan_chars": 3600,
    "claim_lines": 40,
    "claim_chars": 2200,
    "runs_lines": 30,
    "runs_chars": 1800,
    "prompt_preview_lines": 36,
    "prompt_preview_chars": 2200,
    "planner_body_lines": 36,
    "planner_body_chars": 2400,
    "planner_comments_lines": 10,
    "planner_comments_chars": 800,
}


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
    backend_reason: str = "default Codex CLI execution backend"
    backend_execution_supported: bool = True
    backend_notes: tuple[str, ...] = ()
    selected_role_name: str = "WORKER_CODE"
    selected_model: str = "openai/gpt-5.3-codex"
    selected_reasoning_effort: str = "low"
    selected_openclaw_agent: str = "worker"
    role_selection_reason: str = "default role selection"


def _contains_unsupported_model_signal(text: str, model: str) -> bool:
    lowered = text.lower()
    model_lower = model.lower()
    model_name = model_lower.split("/", 1)[-1]
    return (
        "unknown model:" in lowered
        and (model_lower in lowered or model_name in lowered)
    ) or "reason=model_not_found" in lowered


def _fallback_runner_plan(plan: RunnerPlan) -> RunnerPlan | None:
    try:
        policy = get_role_policy(plan.selected_role_name)
    except KeyError:
        return None

    fallback_role = policy.escalation_role or policy.fallback_role
    fallback_model = policy.fallback_model

    if fallback_model and fallback_model != plan.selected_model:
        base_session_key = build_openclaw_session_key(
            target_kind="issue",
            target_number=plan.item.number,
            profile=plan.proposed_profile or "worker",
        )
        fallback_session_key = f"{base_session_key}-fallback-model"
        fallback_agent = plan.selected_openclaw_agent
        return replace(
            plan,
            selected_model=fallback_model,
            role_selection_reason=(
                f"fallback model for {plan.selected_role_name} after runtime reported "
                f"unsupported model for {plan.selected_model}"
            ),
            proposed_command_shape=(
                f"openclaw agent --agent {fallback_agent} "
                f"--session-key {fallback_session_key} "
                f"--model {fallback_model} "
                f"--thinking {plan.selected_reasoning_effort} "
                f'--message "$(cat {plan.proposed_prompt_path})" --local'
            ),
        )

    if not fallback_role:
        return None

    fallback_policy = get_role_policy(fallback_role)
    if fallback_policy.model == plan.selected_model:
        return None

    base_session_key = build_openclaw_session_key(
        target_kind="issue",
        target_number=plan.item.number,
        profile=plan.proposed_profile or "worker",
    )
    fallback_session_key = (
        f"{base_session_key}-fallback-{fallback_policy.name.lower()}"
    )

    fallback_agent = execution_agent_for_backend(fallback_policy, plan.proposed_runner)
    return replace(
        plan,
        selected_role_name=fallback_policy.name,
        selected_model=fallback_policy.model,
        selected_reasoning_effort=fallback_policy.reasoning_effort,
        selected_openclaw_agent=fallback_agent,
        role_selection_reason=(
            f"fallback from {plan.selected_role_name} after runtime reported "
            f"unsupported model for {plan.selected_model}"
        ),
        proposed_command_shape=(
            f"openclaw agent --agent {fallback_agent} "
            f"--session-key {fallback_session_key} "
            f"--model {fallback_policy.model} "
            f"--thinking {fallback_policy.reasoning_effort} "
            f'--message "$(cat {plan.proposed_prompt_path})" --local'
        ),
    )


def openclaw_session_namespace(env: dict[str, str] | None = None) -> str:
    """Return Signposter's OpenClaw session namespace.

    OpenClaw stores model/provider pins on existing session keys. Keep the key
    namespace versioned so Signposter can move to current OpenClaw agent config
    without duplicating model names here.
    """
    source = env if env is not None else os.environ
    namespace = source.get(OPENCLAW_SESSION_NAMESPACE_ENV, "").strip()
    return namespace or DEFAULT_OPENCLAW_SESSION_NAMESPACE


def build_openclaw_session_key(
    *,
    target_kind: str,
    target_number: int,
    profile: str,
    env: dict[str, str] | None = None,
) -> str:
    namespace = openclaw_session_namespace(env)
    return f"signposter-{namespace}-{target_kind}-{target_number}-{profile}"


def _select_runner_and_profile(
    dispatch: DispatchDecision,
    *,
    backend: str | None = None,
) -> tuple[str, str]:
    """Map role + phase to execution backend + profile."""
    backend_plan = resolve_execution_backend(backend)
    role = dispatch.role or ""
    phase = dispatch.phase or ""

    if role == "worker" and phase == "build":
        return backend_plan.backend, "worker"
    elif role == "reviewer" and phase == "review":
        return backend_plan.backend, "reviewer"
    elif role == "planner" and phase == "plan":
        return backend_plan.backend, "planner"
    elif role == "gatekeeper":
        return backend_plan.backend, "gatekeeper"
    else:
        # Conservative default
        return backend_plan.backend, "worker"


def _build_explicit_claim_plan(plan: RunnerPlan):
    return build_claim_plan(plan.dispatch)


def plan_runner(repo: str, *, limit: int = 1, backend: str | None = None) -> list[RunnerPlan]:
    """Produce runner plans for claimable items.

    Reuses the conservative claim planner (limit + deterministic ordering).
    """
    claim_result = plan_claims(repo, limit=limit)
    plans: list[RunnerPlan] = []

    for claim_plan in claim_result.selected:
        dispatch = claim_plan.dispatch
        item = claim_plan.item

        backend_plan = resolve_execution_backend(backend)
        runner, profile = _select_runner_and_profile(dispatch, backend=backend_plan.backend)
        role_selection = select_role_for_issue(item, dispatch)

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
        session_key = build_openclaw_session_key(
            target_kind="issue",
            target_number=item.number,
            profile=profile,
        )
        role_execution = resolve_role_execution(role_selection, backend=runner)
        command_shape = build_backend_command_shape(
            backend=runner,
            agent=role_execution.execution_agent,
            session_key=session_key,
            model=role_execution.model,
            reasoning_effort=role_execution.reasoning_effort,
            prompt_path=prompt_path,
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
            backend_reason=backend_plan.reason,
            backend_execution_supported=backend_plan.execution_supported,
            backend_notes=backend_plan.notes,
            selected_role_name=role_selection.policy.name,
            selected_model=role_execution.model,
            selected_reasoning_effort=role_execution.reasoning_effort,
            selected_openclaw_agent=role_execution.execution_agent,
            role_selection_reason=role_selection.reason,
        )
        plans.append(plan)

    return plans


def plan_runner_for_issue(
    repo: str,
    issue: int,
    *,
    backend: str | None = None,
) -> RunnerPlan | None:
    """Build a RunnerPlan for one specific issue by number.

    Works for state:ready, state:active, etc. Does not filter by claimability.
    Returns None if the issue cannot be fetched.
    """
    item = fetch_issue_by_number(repo, issue)
    if not item:
        return None

    dispatch = classify_candidate(item)
    backend_plan = resolve_execution_backend(backend)
    runner, profile = _select_runner_and_profile(dispatch, backend=backend_plan.backend)
    role_selection = select_role_for_issue(item, dispatch)

    # HARDENING-009: prefer isolated worktree path if it exists
    try:
        ws = get_worktree_status_for_issue(issue, item.title)
        if ws.get("exists"):
            working_dir = ws["path"]
        else:
            working_dir = f"~/projects/signposter-work/{item.number}"
    except Exception:
        working_dir = f"~/projects/signposter-work/{item.number}"

    prompt_path = f"artifacts/prompts/issue-{item.number}.md"

    session_key = build_openclaw_session_key(
        target_kind="issue",
        target_number=item.number,
        profile=profile,
    )
    role_execution = resolve_role_execution(role_selection, backend=runner)
    command_shape = build_backend_command_shape(
        backend=runner,
        agent=role_execution.execution_agent,
        session_key=session_key,
        model=role_execution.model,
        reasoning_effort=role_execution.reasoning_effort,
        prompt_path=prompt_path,
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
        backend_reason=backend_plan.reason,
        backend_execution_supported=backend_plan.execution_supported,
        backend_notes=backend_plan.notes,
        selected_role_name=role_selection.policy.name,
        selected_model=role_execution.model,
        selected_reasoning_effort=role_execution.reasoning_effort,
        selected_openclaw_agent=role_execution.execution_agent,
        role_selection_reason=role_selection.reason,
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


def plan_active_runner_from_prompts(
    repo: str,
    *,
    limit: int = 1,
    backend: str | None = None,
) -> list[RunnerPlan]:
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
        backend_plan = resolve_execution_backend(backend)
        runner, profile = _select_runner_and_profile(dispatch, backend=backend_plan.backend)
        role_selection = select_role_for_issue(item, dispatch)

        working_dir = f"~/projects/signposter-work/{item.number}"
        prompt_path_str = str(prompt_path)
        session_key = build_openclaw_session_key(
            target_kind="issue",
            target_number=item.number,
            profile=profile,
        )
        role_execution = resolve_role_execution(role_selection, backend=runner)
        command_shape = build_backend_command_shape(
            backend=runner,
            agent=role_execution.execution_agent,
            session_key=session_key,
            model=role_execution.model,
            reasoning_effort=role_execution.reasoning_effort,
            prompt_path=prompt_path_str,
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
                backend_reason=backend_plan.reason,
                backend_execution_supported=backend_plan.execution_supported,
                backend_notes=backend_plan.notes,
                selected_role_name=role_selection.policy.name,
                selected_model=role_execution.model,
                selected_reasoning_effort=role_execution.reasoning_effort,
                selected_openclaw_agent=role_execution.execution_agent,
                role_selection_reason=role_selection.reason,
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
        lines.append(f"      execution_profile: {plan.proposed_profile}")
        lines.append(f"      backend_reason:   {plan.backend_reason}")
        lines.append(
            f"      execute_ready:    {'yes' if plan.backend_execution_supported else 'no'}"
        )
        lines.append(f"      selected_role:    {plan.selected_role_name}")
        lines.append(f"      model:            {plan.selected_model}")
        lines.append(f"      reasoning:        {plan.selected_reasoning_effort}")
        lines.append(f"      role_agent:       {plan.selected_openclaw_agent}")
        lines.append(f"      working_dir:      {plan.proposed_working_dir}")
        lines.append(f"      prompt_artifact:  {plan.proposed_prompt_path}")
        lines.append(f"      command_shape:    {plan.proposed_command_shape}")
        lines.append("")
        lines.append(f"    Reason: {plan.reason}")
        lines.append(f"    Role Reason: {plan.role_selection_reason}")
        for note in plan.backend_notes:
            lines.append(f"    Backend Note: {note}")
        lines.append("")

    lines.append(f"Total items planned for execution: {len(plans)}")
    lines.append(
        "Note: This is a DRY RUN. "
        "No execution backend was started and no artifacts were written."
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
        help=(
            "Run the selected backend locally for the selected item "
            "(explicit, read-only on GitHub)"
        ),
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
    parser.add_argument(
        "--backend",
        choices=["openclaw", "codex-cli"],
        help="Execution backend to plan for; default is codex-cli or SIGNPOSTER_EXECUTION_BACKEND",
    )
    args = parser.parse_args()

    write_prompt = args.write_prompt
    claim = args.claim
    execute = args.execute
    issue = getattr(args, "issue", None)
    allow_dirty = getattr(args, "allow_dirty", False)
    use_worktree = getattr(args, "worktree", False)

    return cli_main(
        args.repo,
        limit=args.limit,
        write_prompt=write_prompt,
        claim=claim,
        execute=execute,
        issue=issue,
        allow_dirty=allow_dirty,
        worktree=use_worktree,
        backend=args.backend,
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


def _compact_prompt_text(
    text: str | None,
    *,
    max_lines: int,
    max_chars: int,
    empty_fallback: str,
) -> str:
    """Return a bounded prompt-safe excerpt with deterministic omission markers."""
    normalized = (text or "").strip()
    if not normalized:
        return empty_fallback

    lines = normalized.splitlines()
    selected: list[str] = []
    consumed_chars = 0

    for line in lines:
        line_cost = len(line) + (1 if selected else 0)
        if len(selected) >= max_lines or consumed_chars + line_cost > max_chars:
            break
        selected.append(line)
        consumed_chars += line_cost

    excerpt = "\n".join(selected).strip()

    while True:
        omitted_lines = max(len(lines) - len(selected), 0)
        omitted_chars = max(len(normalized) - len(excerpt), 0)
        if not omitted_lines and not omitted_chars:
            return excerpt or empty_fallback

        omission_marker = f"...[omitted {omitted_lines} lines, {omitted_chars} chars]"
        candidate = f"{excerpt}\n{omission_marker}".strip() if excerpt else omission_marker
        if len(selected) <= max_lines and len(candidate) <= max_chars:
            return candidate

        if selected:
            selected.pop()
            excerpt = "\n".join(selected).strip()
            continue

        if len(omission_marker) > max_chars:
            return omission_marker[:max_chars].rstrip()
        return omission_marker


def _compact_issue_body(text: str | None) -> str:
    return _compact_prompt_text(
        text,
        max_lines=PROMPT_COMPACTION_LIMITS["issue_body_lines"],
        max_chars=PROMPT_COMPACTION_LIMITS["issue_body_chars"],
        empty_fallback="Issue body: empty",
    )


def _compact_comments(text: str | None) -> str:
    return _compact_prompt_text(
        text,
        max_lines=PROMPT_COMPACTION_LIMITS["comments_lines"],
        max_chars=PROMPT_COMPACTION_LIMITS["comments_chars"],
        empty_fallback="(no comments)",
    )


def _compact_worker_issue_body(text: str | None) -> str:
    return _compact_prompt_text(
        text,
        max_lines=PROMPT_COMPACTION_LIMITS["worker_issue_body_lines"],
        max_chars=PROMPT_COMPACTION_LIMITS["worker_issue_body_chars"],
        empty_fallback="Issue body: empty",
    )


def _compact_worker_comments(text: str | None) -> str:
    return _compact_prompt_text(
        text,
        max_lines=PROMPT_COMPACTION_LIMITS["worker_comments_lines"],
        max_chars=PROMPT_COMPACTION_LIMITS["worker_comments_chars"],
        empty_fallback="(no comments)",
    )


def _compact_evidence_text(
    text: str | None,
    *,
    max_lines: int,
    max_chars: int,
    empty_fallback: str,
) -> str:
    return _compact_prompt_text(
        text,
        max_lines=max_lines,
        max_chars=max_chars,
        empty_fallback=empty_fallback,
    )


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
    evidence["scan"] = _compact_evidence_text(
        scan_out,
        max_lines=PROMPT_COMPACTION_LIMITS["scan_lines"],
        max_chars=PROMPT_COMPACTION_LIMITS["scan_chars"],
        empty_fallback="(no scan output)",
    )
    (evidence_dir / "scan.txt").write_text(scan_out)

    # Claim dry-run (useful context for reviewer)
    claim_dry = _capture_command(["signposter", "claim", "--repo", repo, "--dry-run"])
    evidence["claim_dry_run"] = _compact_evidence_text(
        claim_dry,
        max_lines=PROMPT_COMPACTION_LIMITS["claim_lines"],
        max_chars=PROMPT_COMPACTION_LIMITS["claim_chars"],
        empty_fallback="(no claim dry-run)",
    )
    (evidence_dir / "claim-dry-run.txt").write_text(claim_dry)

    # Recent CI runs
    runs_out = _capture_command([
        "gh", "run", "list", "-R", repo, "--limit", "5",
        "--json", "status,conclusion,workflowName,headBranch,updatedAt"
    ])
    evidence["recent_runs"] = _compact_evidence_text(
        runs_out,
        max_lines=PROMPT_COMPACTION_LIMITS["runs_lines"],
        max_chars=PROMPT_COMPACTION_LIMITS["runs_chars"],
        empty_fallback="(no runs)",
    )
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
                preview = f.read(6000)
            prompt_preview = _compact_evidence_text(
                preview,
                max_lines=PROMPT_COMPACTION_LIMITS["prompt_preview_lines"],
                max_chars=PROMPT_COMPACTION_LIMITS["prompt_preview_chars"],
                empty_fallback="(prompt file not found)",
            )
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
        body_text = (
            _compact_worker_issue_body(body) if d.role == "worker" else _compact_issue_body(body)
        )
        state = issue_context.get("state", "unknown")
        comments = issue_context.get("comments", [])
        comments_text = ""
        if comments:
            recent = comments[-2:]
            comments_lines = []
            for c in recent:
                author = c.get("author", {}).get("login", "unknown")
                body_snip = (c.get("body", "") or "").strip()
                comments_lines.append(f"- @{author}: {body_snip}")
            comments_joined = "\n".join(comments_lines)
            comments_text = (
                _compact_worker_comments(comments_joined)
                if d.role == "worker"
                else _compact_comments(comments_joined)
            )
        else:
            comments_text = "(no comments)"
        issue_state = state
    else:
        labels_str = ", ".join(item.labels) if item.labels else "(none)"
        body_text = (
            _compact_worker_issue_body("Issue body: not embedded (context fetch failed)")
            if d.role == "worker"
            else _compact_issue_body("Issue body: not embedded (context fetch failed)")
        )
        comments_text = (
            _compact_worker_comments("(not embedded)")
            if d.role == "worker"
            else _compact_comments("(not embedded)")
        )
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

    if d.role == "worker" and not evidence_bundle:
        return _render_compact_worker_prompt(
            repo=repo,
            item=item,
            dispatch=d,
            plan=plan,
            labels_str=labels_str,
            body_text=body_text,
            comments_text=comments_text,
            issue_state=issue_state,
            task_instruction=task_instruction,
            private_rule=private_rule,
        )
    if d.role == "planner" and not evidence_bundle:
        return _render_compact_planner_prompt(
            repo=repo,
            item=item,
            dispatch=d,
            plan=plan,
            labels_str=labels_str,
            body_text=_compact_evidence_text(
                body_text,
                max_lines=PROMPT_COMPACTION_LIMITS["planner_body_lines"],
                max_chars=PROMPT_COMPACTION_LIMITS["planner_body_chars"],
                empty_fallback="Issue body: empty",
            ),
            comments_text=_compact_evidence_text(
                comments_text,
                max_lines=PROMPT_COMPACTION_LIMITS["planner_comments_lines"],
                max_chars=PROMPT_COMPACTION_LIMITS["planner_comments_chars"],
                empty_fallback="(no comments)",
            ),
            issue_state=issue_state,
            task_instruction=task_instruction,
            private_rule=private_rule,
        )

    # Evidence Bundle (only for reviewer/gatekeeper)
    evidence_section = ""
    if evidence_bundle and d.role in ("reviewer", "gatekeeper"):
        scan = _compact_evidence_text(
            evidence_bundle.get("scan"),
            max_lines=PROMPT_COMPACTION_LIMITS["scan_lines"],
            max_chars=PROMPT_COMPACTION_LIMITS["scan_chars"],
            empty_fallback="(no scan output)",
        )
        claim_dry = _compact_evidence_text(
            evidence_bundle.get("claim_dry_run"),
            max_lines=PROMPT_COMPACTION_LIMITS["claim_lines"],
            max_chars=PROMPT_COMPACTION_LIMITS["claim_chars"],
            empty_fallback="(no claim dry-run)",
        )
        runs = _compact_evidence_text(
            evidence_bundle.get("recent_runs"),
            max_lines=PROMPT_COMPACTION_LIMITS["runs_lines"],
            max_chars=PROMPT_COMPACTION_LIMITS["runs_chars"],
            empty_fallback="(no runs)",
        )
        wd = evidence_bundle.get("working_dir", "unknown")
        wd_status = evidence_bundle.get("working_dir_status", "unknown")
        prompt_path = evidence_bundle.get("prompt_path", plan.proposed_prompt_path)
        prompt_exists = evidence_bundle.get("prompt_exists", False)
        prompt_preview = _compact_evidence_text(
            evidence_bundle.get("prompt_preview"),
            max_lines=PROMPT_COMPACTION_LIMITS["prompt_preview_lines"],
            max_chars=PROMPT_COMPACTION_LIMITS["prompt_preview_chars"],
            empty_fallback="(no preview)",
        )
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

## Selected Role Policy
- backend: {plan.proposed_runner}
- backend reason: {plan.backend_reason}
- role identity: {plan.selected_role_name}
- selected model: {plan.selected_model}
- selected reasoning effort: {plan.selected_reasoning_effort}
- Execution agent/profile: {plan.selected_openclaw_agent}
- role selection reason: {plan.role_selection_reason}
- command shape: {plan.proposed_command_shape}

## Prompt Contract
- expected output format: concise execution summary with changed files, validation,
  safety notes, and completion status
- artifact requirements: keep raw backend output local under artifacts/runs/
  and provide bounded summaries only
- uncertainty handling: if uncertain, state exactly what is missing instead of guessing

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
- If you are uncertain, say exactly what is missing rather than guessing.

## Task
{task_instruction}

---

Begin execution following the constraints and role profile above.
"""
    return content


def _render_compact_worker_prompt(
    *,
    repo: str,
    item: LabeledItem,
    dispatch: DispatchDecision,
    plan: RunnerPlan,
    labels_str: str,
    body_text: str,
    comments_text: str,
    issue_state: str,
    task_instruction: str,
    private_rule: str,
) -> str:
    """Render a compact self-contained prompt for scoped worker tasks."""
    workflow = (
        f"{dispatch.proposed_route}/{dispatch.phase}/{dispatch.role}/"
        f"{dispatch.risk}/{dispatch.area}/{dispatch.proposed_gate}"
    )
    return f"""# Signposter Worker Prompt

## Context
- Repository: {repo}
- Issue: #{item.number} — {item.title}
- URL reference only: {item.html_url}
- State: {issue_state}
- Labels: {labels_str}
- Route/phase/role/risk/area/gate: {workflow}
- Working directory: {plan.proposed_working_dir}
- Prompt artifact: {plan.proposed_prompt_path}

## Selected Role Policy
- backend: {plan.proposed_runner}
- backend reason: {plan.backend_reason}
- role identity: {plan.selected_role_name}
- selected model: {plan.selected_model}
- selected reasoning effort: {plan.selected_reasoning_effort}
- Execution agent/profile: {plan.selected_openclaw_agent}
- role selection reason: {plan.role_selection_reason}
- command shape: {plan.proposed_command_shape}

## Prompt Contract
- expected output format: concise execution summary with changed files, validation,
  safety notes, and completion status
- artifact requirements: keep raw backend output local under artifacts/runs/
  and provide bounded summaries only
- uncertainty handling: if uncertain, state exactly what is missing instead of guessing

## Issue Body
{body_text}

## Recent Comments
{comments_text}

## Rules
- {private_rule}
- Implement only this scoped issue.
- Do not mutate GitHub unless a later command explicitly asks.
- Do not commit unless explicitly instructed.
- Keep raw backend output local under artifacts/runs/.
- Report changed files, validation, safety notes, and remaining risks.
- If uncertain, state the uncertainty explicitly instead of guessing.

## Task
{task_instruction}

## Validation
- Run targeted validation for changed files.
- Run full validation when risk or shared behavior warrants it.
- If the selected backend/provider execution is unavailable, use the manual artifact fallback.
"""


def _render_compact_planner_prompt(
    *,
    repo: str,
    item: LabeledItem,
    dispatch: DispatchDecision,
    plan: RunnerPlan,
    labels_str: str,
    body_text: str,
    comments_text: str,
    issue_state: str,
    task_instruction: str,
    private_rule: str,
) -> str:
    """Render a compact self-contained prompt for scoped planner tasks."""
    workflow = (
        f"{dispatch.proposed_route}/{dispatch.phase}/{dispatch.role}/"
        f"{dispatch.risk}/{dispatch.area}/{dispatch.proposed_gate}"
    )
    return f"""# Signposter Planner Prompt

## Context
- Repository: {repo}
- Issue: #{item.number} — {item.title}
- URL reference only: {item.html_url}
- State: {issue_state}
- Labels: {labels_str}
- Route/phase/role/risk/area/gate: {workflow}
- Working directory: {plan.proposed_working_dir}
- Prompt artifact: {plan.proposed_prompt_path}

## Selected Role Policy
- role identity: {plan.selected_role_name}
- selected model: {plan.selected_model}
- selected reasoning effort: {plan.selected_reasoning_effort}
- Execution agent/profile: {plan.selected_openclaw_agent}
- role selection reason: {plan.role_selection_reason}

## Issue Body
{body_text}

## Recent Comments
{comments_text}

## Rules
- {private_rule}
- Keep the plan scoped to this issue.
- Prefer small deterministic steps over broad proposals.
- Call out dependencies, blockers, and omitted context explicitly.
- Do not mutate GitHub unless a later command explicitly asks.
- If uncertain, state the uncertainty explicitly instead of guessing.

## Task
{task_instruction}

## Output Contract
- Return a compact phased plan.
- Separate deterministic work from LLM-required work.
- Keep acceptance criteria specific and bounded.
"""


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
    allow_dirty: bool = False,
    worktree: bool = False,
    backend: str | None = None,
) -> int:  # noqa: E501
    """Entry point for the run command.

    If `issue` is provided, operates on that specific issue only (explicit targeting).
    Otherwise falls back to the normal claim-planner path.
    """
    try:
        if issue is not None:
            # Explicit single-issue targeting path (HARDENING-004)
            plan = plan_runner_for_issue(repo, issue, backend=backend)
            plans = [plan] if plan else []
            if not plans:
                print(f"Error: Could not fetch issue #{issue} from {repo}.")
                return 1

            print(f"Signposter Run Plan — Explicit target issue #{issue}")
            print(format_runner_plan(plans))

            # HARDENING-004 micro-adjustment + HARDENING-005 deps
            if plans:
                st = (plans[0].dispatch.state or "").lower()
                if st in ("done", "failed"):
                    print(f"Execution status: blocked — state:{st}")

                # Show dependency status for diagnostic purposes (even if not claimable)
                if st == "ready":
                    try:
                        ctx = fetch_issue_context(repo, issue) or {}
                        body = ctx.get("body", "")
                        blocked, d_reason = is_dependency_blocked(repo, body)
                        if blocked:
                            print(f"Dependency status: blocked — {d_reason}")
                        else:
                            print("Dependency status: clear")
                    except Exception:
                        print("Dependency status: check failed")

            # HARDENING-009: worktree awareness (diagnostic only in this phase)
            if plans:
                p = plans[0]
                item_num = p.item.number
                title = p.item.title
                try:
                    ws = get_worktree_status_for_issue(item_num, title)
                    if ws["status"] == "available":
                        print("\nWorktree:")
                        print("  status: available")
                        print(f"  path: {ws['path']}")
                        print(f"  branch: {ws['branch']}")
                        print(f"  runner working_dir: {ws['path']}")
                    else:
                        print("\nWorktree:")
                        print("  status: missing")
                        print(f"  expected path: {ws['path']}")
                        hint = (
                            f"  hint: run `signposter worktree apply --repo {repo} "
                            f"--issue {item_num} --apply`"
                        )
                        print(hint)
                except Exception:
                    print("Worktree status: check failed")

            # Handle explicit single-issue actions
            plan = plans[0]
            item_number = plan.item.number
            current_state = (plan.dispatch.state or "").lower()

            claimed_issue = False
            if claim:
                if current_state == "ready":
                    print("\n=== APPLYING CLAIM MUTATION (explicit --issue) ===\n")
                    claim_plan = _build_explicit_claim_plan(plan)
                    print(f"Claiming issue #{item_number}...")
                    commands = perform_claim_mutation(claim_plan, repo, dry_run=False)
                    for cmd in commands:
                        print(f"  Executed: {cmd}")
                    claimed_issue = True
                    print("Claim mutation complete.")
                else:
                    msg = f"  Note: issue #{item_number} already {current_state}. Skipping claim."
                    print(msg)

            final_plans = plans

            if claimed_issue:
                print("\n=== Refreshing explicit issue plan from current GitHub state ===\n")
                refreshed_plan = plan_runner_for_issue(repo, item_number, backend=backend)
                if refreshed_plan is None:
                    print(
                        f"  Warning: could not re-fetch issue #{item_number}; "
                        "continuing with stale explicit plan"
                    )
                else:
                    final_plans = [refreshed_plan]
                    current_state = (refreshed_plan.dispatch.state or "").lower()
                    print(f"  Refreshed issue #{item_number}: state={current_state}")

            if write_prompt and final_plans:
                print("\n=== Writing Prompt Artifact(s) ===\n")
                for p in final_plans:
                    path = write_prompt_artifact(p, repo)
                    print(f"Wrote: {path}")

            if execute and final_plans:
                print("\n=== EXECUTING RUNNER ===\n")
                execution_failed = False

                for p in final_plans:
                    if not p.backend_execution_supported:
                        print(
                            f"Refusing execution via backend '{p.proposed_runner}': "
                            "execution adapter is not implemented yet."
                        )
                        execution_failed = True
                        continue
                    st = (p.dispatch.state or "").lower()

                    # HARDENING-010: explicit worktree execution path
                    if worktree:
                        if issue is None:
                            print("Refusing --worktree execution: --issue is required.")
                            return 1

                        ws = get_worktree_status_for_issue(issue, p.item.title)
                        if not ws.get("exists"):
                            print("Refusing worktree execution: expected worktree is missing.")
                            hint = (
                                f"Hint: run `signposter worktree apply --repo {repo} "
                                f"--issue {issue} --apply`"
                            )
                            print(hint)
                            return 1

                        if p.proposed_profile != "worker":
                            msg = (
                                f"Refusing --worktree: profile is '{p.proposed_profile}' "
                                "(worker required)"
                            )
                            print(msg)
                            return 1

                        if st != "active":
                            msg = f"Refusing worktree execution: state={st} (requires state:active)"
                            print(msg)
                            return 1

                        worktree_path = ws["path"]

                        # Dirty guard against the worktree itself
                        if not allow_dirty:
                            dirty = find_uncommitted_repo_changes(cwd=worktree_path)
                            if dirty:
                                shown = ", ".join(dirty[:3])
                                print(
                                    "Refusing worktree execution: "
                                    "worktree has uncommitted changes: "
                                    f"{shown}"
                                )
                                return 1

                        result = execute_plan(
                            p, repo, allow_dirty=allow_dirty, worktree_cwd=worktree_path
                        )
                        print(f"Execution completed for issue #{p.item.number} (worktree)")
                        print(f"  Exit code: {result.get('exit_code')}")
                        print(f"  Raw output: {result.get('raw_path')}")
                        print(f"  Summary:   {result.get('summary_path')}")
                        if result.get("exit_code") != 0:
                            execution_failed = True
                        continue  # handled

                    # Normal (non-worktree) execution path
                    if st != "active":
                        msg = f"  Refusing to execute issue #{p.item.number}: state={st}"
                        print(msg + " (requires state:active)")
                        execution_failed = True
                        continue
                    result = execute_plan(p, repo, allow_dirty=allow_dirty)
                    print(f"Execution completed for issue #{p.item.number}")
                    print(f"  Exit code: {result.get('exit_code')}")
                    print(f"  Raw output: {result.get('raw_path')}")
                    print(f"  Summary:   {result.get('summary_path')}")
                    if result.get("exit_code") != 0:
                        execution_failed = True

                if execution_failed:
                    return 1

            return 0

        else:
            plans = plan_runner(repo, limit=limit, backend=backend)
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
                            backend_reason=old_plan.backend_reason,
                            backend_execution_supported=old_plan.backend_execution_supported,
                            backend_notes=old_plan.backend_notes,
                            selected_role_name=old_plan.selected_role_name,
                            selected_model=old_plan.selected_model,
                            selected_reasoning_effort=old_plan.selected_reasoning_effort,
                            selected_openclaw_agent=old_plan.selected_openclaw_agent,
                            role_selection_reason=old_plan.role_selection_reason,
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
            print("\n=== EXECUTING RUNNER ===\n")
            execution_failed = False
            for plan in final_plans:
                if not plan.backend_execution_supported:
                    print(
                        f"Refusing execution via backend '{plan.proposed_runner}': "
                        "execution adapter is not implemented yet."
                    )
                    execution_failed = True
                    continue
                state = (plan.dispatch.state or "").lower()
                if state == "ready" and not claim:
                    print(f"  Refusing to execute issue #{plan.item.number}: state=ready without --claim. Use --claim --execute to claim + run.")  # noqa: E501
                    execution_failed = True
                    continue

                result = execute_plan(plan, repo, allow_dirty=allow_dirty)
                print(f"Execution completed for issue #{plan.item.number}")
                print(f"  Exit code: {result.get('exit_code')}")
                print(f"  Raw output: {result.get('raw_path')}")
                print(f"  Summary:   {result.get('summary_path')}")
                if result.get("exit_code") != 0:
                    execution_failed = True

            if execution_failed:
                return 1

        # Fallback for --execute on already-active items with existing prompt artifacts.
        elif execute and not final_plans:
            print("\n=== EXECUTING RUNNER - active item fallback ===\n")
            execution_failed = False
            try:
                active_plans = plan_active_runner_from_prompts(
                    repo,
                    limit=limit,
                    backend=backend,
                )
                if not active_plans:
                    print("No active items with prompt artifacts found.")
                    execution_failed = True
                for plan in active_plans:
                    if not plan.backend_execution_supported:
                        print(
                            f"Refusing execution via backend '{plan.proposed_runner}': "
                            "execution adapter is not implemented yet."
                        )
                        execution_failed = True
                        continue
                    result = execute_plan(plan, repo, allow_dirty=allow_dirty)
                    print(f"Execution completed for issue #{plan.item.number} (active fallback)")
                    print(f"  Exit code: {result.get('exit_code')}")
                    print(f"  Raw output: {result.get('raw_path')}")
                    print(f"  Summary:   {result.get('summary_path')}")
                    if result.get("exit_code") != 0:
                        execution_failed = True
            except Exception as e:
                print(f"Active fallback execution failed: {e}")
                execution_failed = True

            if execution_failed:
                return 1

        return 0
    except Exception as e:
        print(f"Run failed: {e}", file=__import__("sys").stderr)
        return 1


# --- Execution Layer (for --execute) ---

def execute_plan(
    plan: RunnerPlan,
    repo: str,
    *,
    allow_dirty: bool = False,
    worktree_cwd: str | None = None,
) -> dict:
    """Execute the runner plan using the selected backend (local only).

    Safety: This function assumes the item is already in an executable state
    (e.g. state:active). It does not perform claims.

    For worker profiles, the working tree must be clean (outside of allowed
    runtime artifact directories) unless allow_dirty=True.
    """
    import datetime

    item = plan.item
    profile = plan.proposed_profile or "worker"
    prompt_path = plan.proposed_prompt_path
    if not plan.backend_execution_supported:
        return {
            "exit_code": 1,
            "raw_path": None,
            "summary_path": None,
            "error": f"execution backend '{plan.proposed_runner}' is not implemented yet",
            "success": False,
        }
    session_key = build_openclaw_session_key(
        target_kind="issue",
        target_number=item.number,
        profile=profile,
    )

    # HARDENING-006 + HARDENING-010: Worker isolation guard (respects worktree cwd)
    effective_cwd = worktree_cwd or "."

    if profile == "worker" and not allow_dirty:
        dirty_paths = find_uncommitted_repo_changes(cwd=effective_cwd)
        if dirty_paths:
            shown = ", ".join(dirty_paths[:5])
            extra = "..." if len(dirty_paths) > 5 else ""
            print(
                f"Refusing worker execution: working tree has uncommitted changes. "
                f"Commit/stash first or run in isolated worktree. "
                f"Dirty paths: {shown}{extra}"
            )
            return {
                "exit_code": 1,
                "raw_path": None,
                "summary_path": None,
                "error": "dirty working tree",
            }

    if plan.proposed_runner == "codex-cli":
        runs_dir = Path("artifacts/runs")
        runs_dir.mkdir(parents=True, exist_ok=True)
        raw_path = runs_dir / f"issue-{item.number}-{profile}.raw.txt"
        summary_path = runs_dir / f"issue-{item.number}-{profile}.summary.md"
        last_message_path = runs_dir / f"issue-{item.number}-{profile}.last-message.txt"
        invocation = plan_codex_cli_invocation(
            agent=plan.selected_openclaw_agent,
            session_key=session_key,
            model=plan.selected_model,
            reasoning_effort=plan.selected_reasoning_effort,
            prompt_path=prompt_path,
            working_dir=effective_cwd,
            output_last_message_path=last_message_path,
            timeout_seconds=openclaw_timeout_settings().execute_timeout,
        )
        result = execute_codex_cli_invocation(
            invocation,
            raw_path=raw_path,
            summary_path=summary_path,
        )
        return {
            "exit_code": result.exit_code,
            "raw_path": str(result.raw_path),
            "summary_path": str(result.summary_path),
            "success": result.success,
            "error": None if result.success else result.reason,
            "diagnosis_status": result.status,
        }

    preflight = check_openclaw_preflight(artifact_kind="worker", target=item.number)
    if not preflight.ok:
        print(format_openclaw_preflight_block(preflight))
        return {
            "exit_code": 1,
            "raw_path": None,
            "summary_path": None,
            "error": preflight.reason,
            "success": False,
        }

    # Read the prompt content (we pass it properly, not via shell substitution)
    try:
        with open(prompt_path, encoding="utf-8") as f:
            prompt_content = f.read()
    except Exception as e:
        raise RuntimeError(f"Could not read prompt artifact {prompt_path}: {e}") from e

    # Final command for execution (no shell substitution)
    diagnostics = gather_openclaw_runtime_diagnostics()
    timeout_settings = openclaw_timeout_settings()
    execute_timeout = timeout_settings.execute_timeout
    subprocess_timeout = timeout_settings.subprocess_timeout
    diagnostics_warnings = diagnostics.warnings + timeout_settings.warnings
    config_error = getattr(timeout_settings, "config_error", None)
    if config_error:
        runs_dir = Path("artifacts/runs")
        runs_dir.mkdir(parents=True, exist_ok=True)
        raw_path = runs_dir / f"issue-{item.number}-{profile}.raw.txt"
        summary_path = runs_dir / f"issue-{item.number}-{profile}.summary.md"
        combined = "[CONFIG ERROR]\n" + config_error
        raw_path.write_text(combined, encoding="utf-8")
        diagnosis = OpenClawExecutionDiagnosis(
            status="config-error",
            reason=config_error,
            remediation=(
                "Fix the timeout environment configuration before rerunning OpenClaw.",
                "Do not continue the lifecycle automatically with invalid timeout bounds.",
            ),
        )
        summary = _generate_execution_summary(
            repo=repo,
            plan=plan,
            session_key=session_key,
            exit_code=-1,
            raw_path=str(raw_path),
            stdout="",
            stderr=config_error,
            start_time=datetime.datetime.now(datetime.UTC),
            allow_dirty=allow_dirty,
            diagnosis=diagnosis,
            diagnostics_warnings=diagnostics_warnings,
        )
        summary_path.write_text(summary, encoding="utf-8")
        _record_runner_runtime_bug(
            plan=plan,
            diagnosis=diagnosis,
            raw_path=raw_path,
            summary_path=summary_path,
        )
        return {
            "exit_code": -1,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "success": False,
            "error": diagnosis.reason,
            "diagnosis_status": diagnosis.status,
        }
    exec_cmd = [
        "openclaw", "agent",
        "--agent", plan.selected_openclaw_agent,
        "--session-key", session_key,
        "--model", plan.selected_model,
        "--thinking", plan.selected_reasoning_effort,
        "--message", prompt_content,
        "--local",
        "--timeout", str(execute_timeout),
    ]

    print(
        "Running: "
        f"openclaw agent --agent {plan.selected_openclaw_agent} "
        f"--session-key {session_key} --model {plan.selected_model} "
        f"--thinking {plan.selected_reasoning_effort} --local --timeout {execute_timeout}"
    )
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
            cwd=effective_cwd,  # HARDENING-010: run inside worktree when provided
            timeout=subprocess_timeout,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        combined = stdout
        if stderr:
            combined += "\n\n=== STDERR ===\n" + stderr

        exit_code = proc.returncode
        effective_plan = plan
        effective_session_key = session_key
        fallback_used = False
        original_model = plan.selected_model
        fallback_plan = None

        if exit_code != 0 and _contains_unsupported_model_signal(combined, plan.selected_model):
            fallback_plan = _fallback_runner_plan(plan)
            if fallback_plan is not None:
                fallback_used = True
                effective_plan = fallback_plan
                effective_session_key = (
                    f"{session_key}-fallback-{fallback_plan.selected_role_name.lower()}"
                )
                fallback_cmd = [
                    "openclaw", "agent",
                    "--agent", fallback_plan.selected_openclaw_agent,
                    "--session-key", effective_session_key,
                    "--model", fallback_plan.selected_model,
                    "--thinking", fallback_plan.selected_reasoning_effort,
                    "--message", prompt_content,
                    "--local",
                ]
                print(
                    "Unsupported model detected for "
                    f"{plan.selected_role_name} ({plan.selected_model}). "
                    f"Retrying once with {fallback_plan.selected_role_name} "
                    f"({fallback_plan.selected_model})."
                )
                fallback_proc = subprocess.run(
                    fallback_cmd,
                    capture_output=True,
                    text=True,
                    cwd=effective_cwd,
                    timeout=subprocess_timeout,
                )
                fallback_stdout = fallback_proc.stdout or ""
                fallback_stderr = fallback_proc.stderr or ""
                fallback_combined = fallback_stdout
                if fallback_stderr:
                    fallback_combined += "\n\n=== STDERR ===\n" + fallback_stderr
                combined = (
                    "=== PRIMARY ATTEMPT ===\n"
                    + combined
                    + "\n\n=== FALLBACK ATTEMPT ===\n"
                    + fallback_combined
                )
                stdout = fallback_stdout
                stderr = fallback_stderr
                exit_code = fallback_proc.returncode

        # Write raw output
        raw_path.write_text(combined, encoding="utf-8")
        diagnosis = classify_openclaw_execution(
            exit_code=exit_code,
            combined_output=combined,
            timed_out=False,
            diagnostics_warnings=diagnostics_warnings,
        )

        # Generate summary
        summary = _generate_execution_summary(
            repo=repo,
            plan=effective_plan,
            session_key=effective_session_key,
            exit_code=exit_code,
            raw_path=str(raw_path),
            stdout=stdout,
            stderr=stderr,
            start_time=start_time,
            allow_dirty=allow_dirty,
            fallback_used=fallback_used,
            original_role_name=plan.selected_role_name if fallback_used else None,
            original_model=original_model if fallback_used else None,
            diagnosis=diagnosis,
            diagnostics_warnings=diagnostics_warnings,
        )
        summary_path.write_text(summary, encoding="utf-8")
        _record_runner_runtime_bug(
            plan=effective_plan,
            diagnosis=diagnosis,
            raw_path=raw_path,
            summary_path=summary_path,
        )

        return {
            "exit_code": exit_code,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "success": diagnosis.status == "success",
            "fallback_used": fallback_used,
            "error": None if diagnosis.status == "success" else diagnosis.reason,
            "diagnosis_status": diagnosis.status,
        }

    except subprocess.TimeoutExpired as e:
        stdout = normalize_subprocess_output(e.stdout)
        stderr = normalize_subprocess_output(e.stderr)
        combined = f"[TIMEOUT after {subprocess_timeout}s]\n"
        if stdout:
            combined += stdout
        if stderr:
            combined += "\n\n=== STDERR ===\n" + stderr
        raw_path.write_text(combined, encoding="utf-8")
        diagnosis = classify_openclaw_execution(
            exit_code=None,
            combined_output=combined,
            timed_out=True,
            diagnostics_warnings=diagnostics_warnings,
            timeout_seconds=subprocess_timeout,
        )
        summary = _generate_execution_summary(
            repo=repo,
            plan=plan,
            session_key=session_key,
            exit_code=-1,
            raw_path=str(raw_path),
            stdout=stdout,
            stderr=stderr,
            start_time=start_time,
            allow_dirty=allow_dirty,
            diagnosis=diagnosis,
            diagnostics_warnings=diagnostics_warnings,
        )
        summary_path.write_text(summary, encoding="utf-8")
        _record_runner_runtime_bug(
            plan=plan,
            diagnosis=diagnosis,
            raw_path=raw_path,
            summary_path=summary_path,
        )
        return {
            "exit_code": -1,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "success": False,
            "error": diagnosis.reason,
            "diagnosis_status": diagnosis.status,
        }
    except Exception as e:
        combined = f"[ERROR]\n{e}"
        raw_path.write_text(combined, encoding="utf-8")
        diagnosis = classify_openclaw_execution(
            exit_code=-1,
            combined_output=combined,
            timed_out=False,
            diagnostics_warnings=diagnostics_warnings,
        )
        summary = _generate_execution_summary(
            repo=repo,
            plan=plan,
            session_key=session_key,
            exit_code=-1,
            raw_path=str(raw_path),
            stdout="",
            stderr=str(e),
            start_time=start_time,
            allow_dirty=allow_dirty,
            diagnosis=diagnosis,
            diagnostics_warnings=diagnostics_warnings,
        )
        summary_path.write_text(summary, encoding="utf-8")
        _record_runner_runtime_bug(
            plan=plan,
            diagnosis=diagnosis,
            raw_path=raw_path,
            summary_path=summary_path,
        )
        return {
            "exit_code": -1,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "success": False,
            "error": diagnosis.reason,
            "diagnosis_status": diagnosis.status,
        }


def _record_runner_runtime_bug(
    *,
    plan: RunnerPlan,
    diagnosis: OpenClawExecutionDiagnosis,
    raw_path: Path,
    summary_path: Path,
) -> None:
    record = record_runtime_bug_ledger_entry(
        target_kind="issue",
        target_number=plan.item.number,
        diagnosis_status=diagnosis.status,
        diagnosis_reason=diagnosis.reason,
        selected_role=plan.selected_role_name,
        selected_model=plan.selected_model,
        raw_path=str(raw_path),
        summary_path=str(summary_path),
    )
    summary_path.write_text(
        summary_path.read_text(encoding="utf-8")
        + "\n## Bug ledger\n\n"
        + format_runtime_bug_ledger_record(record)
        + "\n",
        encoding="utf-8",
    )


def _generate_execution_summary(
    *, repo: str, plan: RunnerPlan, session_key: str, exit_code: int,
    raw_path: str, stdout: str, stderr: str, start_time,
    allow_dirty: bool = False,
    fallback_used: bool = False,
    original_role_name: str | None = None,
    original_model: str | None = None,
    diagnosis: OpenClawExecutionDiagnosis | None = None,
    diagnostics_warnings: tuple[str, ...] = (),
) -> str:
    """Generate a mechanical summary for the execution run."""
    item = plan.item
    lines = [
        "# Signposter Execution Summary\n",
        f"**Repository:** {repo}",
        f"**Issue:** #{item.number} — {item.title}",
        f"**Agent:** {plan.proposed_profile}",
        f"**Selected Role:** {plan.selected_role_name}",
        f"**Selected Model:** {plan.selected_model}",
        f"**Selected Reasoning Effort:** {plan.selected_reasoning_effort}",
        f"**Selected OpenClaw Agent:** {plan.selected_openclaw_agent}",
        f"**Role Selection Reason:** {plan.role_selection_reason}",
        f"**Session Key:** {session_key}",
        f"**Command Shape:** {plan.proposed_command_shape}",
        f"**Prompt Artifact:** {plan.proposed_prompt_path}",
        f"**Started (UTC):** {start_time.isoformat()}",
        f"**Exit Code:** {exit_code}",
        f"**Raw Output:** {raw_path}",
        "",
    ]
    if diagnosis is not None:
        lines.append(f"**Execution Status:** {diagnosis.status}")
        lines.append(f"**Execution Reason:** {diagnosis.reason}")

    # HARDENING-006 micro-adjustment: record dirty tree guard status
    if plan.proposed_profile == "worker":
        if allow_dirty:
            lines.append("**Dirty Guard:** bypassed by --allow-dirty")
        else:
            lines.append("**Dirty Guard:** clean")
    if fallback_used:
        lines.append("**Runtime Fallback Used:** yes")
        lines.append(f"**Original Selected Role:** {original_role_name or 'unknown'}")
        lines.append(f"**Original Selected Model:** {original_model or 'unknown'}")
    else:
        lines.append("**Runtime Fallback Used:** no")

    # Basic stats
    raw_text = stdout + ("\n" + stderr if stderr else "")
    line_count = len(raw_text.splitlines())
    byte_count = len(raw_text.encode("utf-8"))
    lines.append(f"**Output Size:** {line_count} lines, {byte_count} bytes")
    if diagnosis is not None and diagnosis.remediation:
        lines.append("\n## Remediation\n")
        lines.extend(f"- {item}" for item in diagnosis.remediation)
    if diagnostics_warnings:
        lines.append("\n## Runtime warnings\n")
        lines.extend(f"- {warning}" for warning in diagnostics_warnings)

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
