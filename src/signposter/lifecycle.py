"""Lifecycle status command (HARDENING-022A).

Read-only cross-phase summary for an issue or PR.
No mutations of any kind.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from signposter.cleanup import _extract_issue_number, _local_branch_exists, _worktree_exists
from signposter.labels import check_labels
from signposter.review import _run_gh_pr_view
from signposter.scan import fetch_issue_by_number, fetch_issue_context
from signposter.sync import plan_sync

# =============================================================================
# WATCH-002: Read-only lifecycle watch data collector
# =============================================================================


@dataclass(frozen=True)
class LifecycleWatchRequest:
    """Input contract for the lifecycle watch data collector (WATCH-002)."""

    repo: str | None
    issue: int | None
    interval: int = 5


@dataclass(frozen=True)
class LifecycleWatchSnapshot:
    """Structured read-only result from the lifecycle watch data collector.

    This is the narrow data collector surface for WATCH-002.
    No polling or refresh logic lives here (WATCH-003+).
    """ 

    request: LifecycleWatchRequest
    status: str  # "ready" | "blocked"
    reason: str | None
    notes: list[str]


def collect_lifecycle_watch_data(req: LifecycleWatchRequest) -> LifecycleWatchSnapshot:
    """Read-only lifecycle watch data collector (WATCH-002).

    Performs only the narrow precondition checks required by the CLI contract.
    Returns a deterministic snapshot. No GitHub calls, no mutations.
    """ 
    if not req.repo or req.issue is None:
        return LifecycleWatchSnapshot(
            request=req,
            status="blocked",
            reason="--repo and --issue are required",
            notes=[
                "No GitHub mutation was performed.",
                "No OpenClaw execution was performed.",
            ],
        )

    # Happy / ready path (WATCH-001 contract surface)
    return LifecycleWatchSnapshot(
        request=req,
        status="ready",
        reason=None,
        notes=[
            "No GitHub mutation was performed.",
            "No OpenClaw execution was performed.",
            f"Interval requested: {req.interval}s (polling not in this surface)",
        ],
    )


def format_lifecycle_watch(snapshot: LifecycleWatchSnapshot) -> str:
    """Render the exact CLI contract output for lifecycle watch."""
    if snapshot.status == "blocked":
        return (
            "Signposter Lifecycle Watch\n"
            "\n"
            "Status:\n"
            "  blocked\n"
            "\n"
            "Reason:\n"
            f"  {snapshot.reason}\n"
            "\n"
            "Notes:\n"
            + "\n".join(f"  {n}" for n in snapshot.notes)
        )

    # ready path
    issue = snapshot.request.issue
    return (
        f"Signposter Lifecycle Watch — Issue #{issue}\n"
        "\n"
        "Status:\n"
        "  ready\n"
        "\n"
        "Notes:\n"
        + "\n".join(f"  {n}" for n in snapshot.notes)
    )



@dataclass(frozen=True)
class LifecycleStatus:
    """Compact read-only lifecycle summary for an issue/PR."""

    # Input
    query_issue: int | None
    query_pr: int | None

    # Issue
    issue_number: int | None
    issue_state: str | None  # OPEN / CLOSED / ...
    workflow_state: str | None  # state:merged etc.
    phase: str | None
    risk: str | None
    role: str | None
    area: str | None

    # PR
    pr_number: int | None
    pr_state: str | None  # MERGED / OPEN / ...
    pr_base: str | None
    pr_head: str | None
    pr_merged: bool
    merge_commit: str | None

    # Review
    review_decision: str | None  # APPROVED / ...
    has_non_author_approval: bool
    reviewer_login: str | None

    # Integration
    integrated: bool
    issue_closed: bool

    # Cleanup (local)
    expected_worktree: str | None
    worktree_exists: bool
    local_branch_exists: bool
    cleanup_complete: bool

    # Linkage (H022C)
    linkage_source: str | None  # branch-pattern | pr-body-related-issue | closing-keyword | ...
    linkage_confidence: str | None  # high | medium | low
    formal_github_development_link: str | None  # yes | no | unknown
    auto_close_keyword: bool

    # Overall
    status: str
    # "complete" | "incomplete — <reason>" |
    # "incomplete — associated ... could not be detected"
    notes: list[str]


WORKFLOW_CATEGORIES = ("state:", "phase:", "risk:", "role:", "area:")


def _extract_workflow_labels(labels: list[str]) -> dict[str, str]:
    """Return the last seen label for each category (state/phase/risk/role/area)."""
    result: dict[str, str] = {}
    for label in labels or []:
        for prefix in WORKFLOW_CATEGORIES:
            if label.startswith(prefix):
                result[prefix.rstrip(":")] = label
    return result


def _fetch_pr_details(repo: str, pr: int) -> dict[str, Any]:
    """Fetch minimal PR fields needed for lifecycle."""
    return _run_gh_pr_view(
        repo,
        pr,
        ["number", "state", "baseRefName", "headRefName", "mergeCommit", "body", "reviews"],
    )


def _fetch_issue_labels_and_state(repo: str, issue: int) -> tuple[list[str], str | None]:
    """Return (labels, state) for an issue."""
    item = fetch_issue_by_number(repo, issue)
    if not item:
        return [], None

    ctx = fetch_issue_context(repo, issue) or {}
    state = ctx.get("state")

    labels: list[str] = []
    if item and item.labels:
        labels = list(item.labels)
    elif ctx.get("labels"):
        for lbl in ctx["labels"]:
            if isinstance(lbl, dict) and lbl.get("name"):
                labels.append(lbl["name"])
            elif isinstance(lbl, str):
                labels.append(lbl)

    return labels, state


def _contains_auto_close_keyword(text: str) -> bool:
    """
    Case-insensitive detection of *intentional* auto-close keywords.

    Only matches when the keyword appears to be referencing an issue
    (e.g. "Closes #4", "Fixes #123", "Resolves issue #4").

    This prevents false positives from words like "close" appearing
    in normal prose ("integration/close policy", "close the loop", etc).
    """
    if not text:
        return False

    # Match keyword followed by optional "issue" and then #number or number
    pattern = re.compile(
        r"\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)"
        r"(?:s|d)?(?:\s+issue)?\s*#?\d+",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def _detect_link_source(
    *, pr_head: str | None, body: str | None, detected_from: str
) -> tuple[str, str]:
    """
    Return (source, confidence).

    Rules (H022C):
    - branch-pattern: head matches work/issue-N-* → high
    - closing-keyword: body contains Closes/Fixes/Resolves → high
    - pr-body-related-issue: body contains "Related issue: #N" → medium
    - detected-pr-search: issue → PR discovery via search → medium
    - unknown: low
    """
    body = body or ""

    if pr_head and re.search(r"work/issue-\d+", pr_head):
        return "branch-pattern", "high"

    if _contains_auto_close_keyword(body):
        return "closing-keyword", "high"

    if re.search(r"Related issue:\s*#?\d+", body, re.IGNORECASE):
        return "pr-body-related-issue", "medium"

    if detected_from == "issue-search":
        return "detected-pr-search", "medium"

    return "unknown", "low"


def _detect_associated_pr_from_issue(repo: str, issue: int) -> int | None:
    """Best-effort: look for an open/merged PR linked by safe Signposter signals."""
    branch_prefix = f"work/issue-{issue}-"
    related_issue_re = re.compile(
        rf"(?im)^\s*Related issue:\s*#?{issue}\b"
    )

    try:
        for state in ("open", "merged"):
            result = subprocess.run(
                [
                    "gh", "pr", "list", "-R", repo,
                    "--state", state,
                    "--json", "number,headRefName,body",
                    "--limit", "50",
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                continue

            prs = __import__("json").loads(result.stdout or "[]")
            for p in prs:
                head = p.get("headRefName", "") or ""
                body = p.get("body", "") or ""
                if head.startswith(branch_prefix) or related_issue_re.search(body):
                    return int(p["number"])
    except Exception:
        pass
    return None


def _get_non_author_approval(reviews: list[dict]) -> tuple[bool, str | None]:
    """Return (has_non_author_approval, reviewer_login) from reviews list."""
    if not reviews:
        return False, None
    for r in reviews:
        if not isinstance(r, dict):
            continue
        state = (r.get("state") or "").upper()
        author = r.get("author") or {}
        login = author.get("login") if isinstance(author, dict) else None
        if state == "APPROVED" and login:
            return True, login
    return False, None



def _has_validated_noop_lifecycle_evidence(issue_number: int | None) -> bool:
    """Return True only when local gate evidence proves validated no-op completion."""
    if issue_number is None:
        return False

    path = Path(f"artifacts/runs/issue-{issue_number}-gate.summary.md")
    if not path.is_file():
        return False

    try:
        text = path.read_text(encoding="utf-8").lower()
    except OSError:
        return False

    required = [
        "validated no-op",
        "no files were changed",
        "targeted validation",
        "full validation",
        "manual cli smoke passed",
        "no github mutation",
        "no openclaw execution",
    ]
    return all(signal in text for signal in required)


def plan_lifecycle_status(
    repo: str,
    *,
    issue: int | None = None,
    pr: int | None = None,
) -> LifecycleStatus:
    """Produce a read-only LifecycleStatus.

    Accepts exactly one of --issue or --pr.
    """
    notes = [
        "Read-only status only.",
        "No GitHub mutation was performed.",
        "No local cleanup was performed.",
    ]

    if (issue is None) == (pr is None):
        # Caller must ensure exactly one is provided
        return LifecycleStatus(
            query_issue=issue,
            query_pr=pr,
            issue_number=None,
            issue_state=None,
            workflow_state=None,
            phase=None,
            risk=None,
            role=None,
            area=None,
            pr_number=None,
            pr_state=None,
            pr_base=None,
            pr_head=None,
            pr_merged=False,
            merge_commit=None,
            review_decision=None,
            has_non_author_approval=False,
            reviewer_login=None,
            integrated=False,
            issue_closed=False,
            expected_worktree=None,
            worktree_exists=False,
            local_branch_exists=False,
            cleanup_complete=False,
            linkage_source="unknown",
            linkage_confidence="low",
            formal_github_development_link="no/unknown",
            auto_close_keyword=False,
            status="incomplete — exactly one of --issue or --pr is required",
            notes=notes,
        )

    query_issue = issue
    query_pr = pr

    # === Resolve core identifiers ===
    issue_number = issue
    pr_number = pr
    pr_data: dict[str, Any] = {}
    issue_labels: list[str] = []
    issue_state: str | None = None

    try:
        if pr is not None:
            pr_data = _fetch_pr_details(repo, pr)
            pr_number = pr_data.get("number")
            head = pr_data.get("headRefName", "") or ""
            body = pr_data.get("body", "") or ""
            detected_issue = _extract_issue_number(head, body)
            if detected_issue is not None:
                issue_number = detected_issue

        if issue_number is not None:
            issue_labels, issue_state = _fetch_issue_labels_and_state(repo, issue_number)

        # If we still have no PR but have an issue, try best-effort detection
        if pr_number is None and issue_number is not None:
            detected_pr = _detect_associated_pr_from_issue(repo, issue_number)
            if detected_pr is not None:
                pr_number = detected_pr
                pr_data = _fetch_pr_details(repo, pr_number)

    except Exception as e:
        return LifecycleStatus(
            query_issue=query_issue,
            query_pr=query_pr,
            issue_number=issue_number,
            issue_state=issue_state,
            workflow_state=None,
            phase=None,
            risk=None,
            role=None,
            area=None,
            pr_number=pr_number,
            pr_state=None,
            pr_base=None,
            pr_head=None,
            pr_merged=False,
            merge_commit=None,
            review_decision=None,
            has_non_author_approval=False,
            reviewer_login=None,
            integrated=False,
            issue_closed=False,
            expected_worktree=None,
            worktree_exists=False,
            local_branch_exists=False,
            cleanup_complete=False,
            linkage_source="unknown",
            linkage_confidence="low",
            formal_github_development_link="no/unknown",
            auto_close_keyword=False,
            status=f"incomplete — failed to fetch data: {str(e)[:120]}",
            notes=notes,
        )

    # === Issue workflow labels ===
    wf = _extract_workflow_labels(issue_labels)
    workflow_state = wf.get("state")
    phase = wf.get("phase")
    risk = wf.get("risk")
    role = wf.get("role")
    area = wf.get("area")

    # === PR fields ===
    pr_state = pr_data.get("state") if pr_data else None
    pr_base = pr_data.get("baseRefName") if pr_data else None
    pr_head = pr_data.get("headRefName") if pr_data else None
    merge_commit_data = pr_data.get("mergeCommit") if pr_data else None
    merge_commit = merge_commit_data.get("oid") if isinstance(merge_commit_data, dict) else None
    pr_merged = pr_state == "MERGED" and bool(merge_commit)

    # === Review ===
    reviews = pr_data.get("reviews", []) if pr_data else []
    has_non_author, reviewer_login = _get_non_author_approval(reviews)
    review_decision = None
    if reviews:
        for r in reviews:
            if isinstance(r, dict) and (r.get("state") or "").upper() == "APPROVED":
                review_decision = "APPROVED"
                break

    # === Integration ===
    integrated = bool(workflow_state == "state:merged" and issue_state == "CLOSED")
    issue_closed = issue_state == "CLOSED" if issue_state else False

    # === Local cleanup ===
    expected_worktree = None
    worktree_exists = False
    local_branch_exists = False
    cleanup_complete = False

    if issue_number is not None:
        expected_worktree = str(Path("..") / "signposter-work" / str(issue_number))
        worktree_exists = _worktree_exists(expected_worktree)

    if pr_head:
        local_branch_exists = _local_branch_exists(pr_head)

    cleanup_complete = not worktree_exists and not local_branch_exists

    # === Overall status ===
    status = "complete"

    if issue_number is None:
        status = "incomplete — associated issue could not be detected"
    elif (
        pr_number is None
        and integrated
        and cleanup_complete
        and _has_validated_noop_lifecycle_evidence(issue_number)
    ):
        status = "complete"
    elif pr_number is None:
        status = "incomplete — associated PR could not be detected"
    elif issue_state is None or issue_state.upper() != "CLOSED":
        status = f"incomplete — issue #{issue_number} is not CLOSED"
    elif workflow_state != "state:merged":
        status = f"incomplete — issue #{issue_number} lacks state:merged label"
    elif not pr_merged:
        status = f"incomplete — PR #{pr_number} is not merged"
    elif not merge_commit:
        status = f"incomplete — PR #{pr_number} has no merge commit"
    elif worktree_exists:
        status = "incomplete — local worktree still exists"
    elif local_branch_exists:
        status = "incomplete — local branch still exists"
    else:
        status = "complete"

    # === Linkage source detection (H022C) ===
    body = pr_data.get("body", "") if pr_data else ""
    detected_from = (
        "issue-search"
        if (pr is None and issue_number is not None and pr_number is not None)
        else "direct"
    )

    source, confidence = _detect_link_source(
        pr_head=pr_head,
        body=body,
        detected_from=detected_from,
    )

    auto_close = _contains_auto_close_keyword(body)

    # Formal GitHub development link:
    # Only claim "yes" if we explicitly detected a closing keyword as the linkage source.
    if source == "closing-keyword":
        formal_dev_link = "yes"
    else:
        formal_dev_link = "no/unknown"

    return LifecycleStatus(
        query_issue=query_issue,
        query_pr=query_pr,
        issue_number=issue_number,
        issue_state=issue_state,
        workflow_state=workflow_state,
        phase=phase,
        risk=risk,
        role=role,
        area=area,
        pr_number=pr_number,
        pr_state=pr_state,
        pr_base=pr_base,
        pr_head=pr_head,
        pr_merged=pr_merged,
        merge_commit=merge_commit,
        review_decision=review_decision,
        has_non_author_approval=has_non_author,
        reviewer_login=reviewer_login,
        integrated=integrated,
        issue_closed=issue_closed,
        expected_worktree=expected_worktree,
        worktree_exists=worktree_exists,
        local_branch_exists=local_branch_exists,
        cleanup_complete=cleanup_complete,
        linkage_source=source,
        linkage_confidence=confidence,
        formal_github_development_link=formal_dev_link,
        auto_close_keyword=auto_close,
        status=status,
        notes=notes,
    )


def format_lifecycle_status(status: LifecycleStatus) -> str:
    """Compact deterministic output."""
    header = (
        f"Signposter Lifecycle Status — Issue #{status.issue_number}"
        if status.issue_number
        else f"Signposter Lifecycle Status — PR #{status.pr_number}"
    )
    lines = [f"{header}\n"]

    # Issue
    lines.append("Issue:")
    lines.append(f"  state: {status.issue_state or 'unknown'}")
    lines.append(f"  workflow state: {status.workflow_state or 'unknown'}")
    if status.phase:
        lines.append(f"  phase: {status.phase}")
    if status.risk:
        lines.append(f"  risk: {status.risk}")
    if status.role:
        lines.append(f"  role: {status.role}")
    if status.area:
        lines.append(f"  area: {status.area}")

    # PR
    lines.append("\nPR:")
    if status.pr_number:
        lines.append(f"  pr: #{status.pr_number}")
    else:
        lines.append("  pr: none detected")
    lines.append(f"  state: {status.pr_state or 'unknown'}")
    lines.append(f"  base: {status.pr_base or 'unknown'}")
    lines.append(f"  head: {status.pr_head or 'unknown'}")
    lines.append(f"  merged: {'yes' if status.pr_merged else 'no'}")
    lines.append(f"  merge commit: {status.merge_commit or 'none'}")

    # Review
    lines.append("\nReview:")
    lines.append(f"  review decision: {status.review_decision or 'unknown'}")
    lines.append(f"  non-author approval: {'yes' if status.has_non_author_approval else 'no'}")
    if status.reviewer_login:
        lines.append(f"  reviewer: {status.reviewer_login}")

    # Integration
    lines.append("\nIntegration:")
    lines.append(f"  integrated: {'yes' if status.integrated else 'no'}")
    lines.append(f"  issue closed: {'yes' if status.issue_closed else 'no'}")
    lines.append(f"  workflow state: {status.workflow_state or 'unknown'}")

    # Cleanup
    lines.append("\nCleanup:")
    lines.append(f"  expected worktree: {status.expected_worktree or 'n/a'}")
    lines.append(f"  worktree exists: {'yes' if status.worktree_exists else 'no'}")
    lines.append(f"  local branch exists: {'yes' if status.local_branch_exists else 'no'}")
    lines.append(f"  cleanup complete: {'yes' if status.cleanup_complete else 'no'}")

    # Linkage (H022C)
    lines.append("\nLinkage:")
    if status.linkage_source:
        lines.append("  issue-pr linked: yes")
        lines.append(f"  source: {status.linkage_source}")
        lines.append(f"  confidence: {status.linkage_confidence or 'unknown'}")
        formal = status.formal_github_development_link or "no/unknown"
        lines.append(f"  formal GitHub development link: {formal}")
        lines.append(f"  auto-close keyword: {'yes' if status.auto_close_keyword else 'no'}")
    else:
        lines.append("  issue-pr linked: no")

    # Status
    lines.append("\nStatus:")
    lines.append(f"  {status.status}")

    if status.notes:
        lines.append("\nNotes:")
        for n in status.notes:
            lines.append(f"  {n}")

    return "\n".join(lines)

@dataclass(frozen=True)
class LifecyclePreflight:
    """Result of preflight checks before recommending next lifecycle action (H025D-FIX2)."""

    labels_status: str
    sync_status: str
    worktree_status: str


@dataclass(frozen=True)
class LifecycleNext:
    """Read-only recommendation for the next safe lifecycle action."""

    query_issue: int | None
    query_pr: int | None
    issue_number: int | None
    pr_number: int | None
    issue_state: str | None
    workflow_state: str | None
    pr_state: str | None
    worktree_exists: bool
    local_branch_exists: bool
    prompt_exists: bool
    worker_summary_exists: bool
    preflight: LifecyclePreflight
    blocked_next_action: str | None  # the action that was blocked by preflight, if any
    action: str
    command: str
    status: str
    reason: str | None
    notes: list[str]


def _prompt_exists(issue: int | None) -> bool:
    if issue is None:
        return False
    return Path(f"artifacts/prompts/issue-{issue}.md").exists()


def _worker_summary_exists(issue: int | None) -> bool:
    if issue is None:
        return False
    return bool(list(Path("artifacts/runs").glob(f"issue-{issue}-*.summary.md")))


def _check_required_labels(repo: str) -> str:
    """H025D-FIX2: Return human-readable labels preflight status."""
    try:
        result = check_labels(repo)
        if result.status.startswith("blocked"):
            if result.missing:
                return f"blocked — required labels missing: {', '.join(result.missing)}"
            return result.status
        return "pass"
    except Exception as e:
        return f"error — {str(e)[:120]}"


def _check_working_tree() -> str:
    """H025D-FIX2: Return working tree cleanliness status."""
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return "error — could not check working tree"
    return "dirty" if proc.stdout.strip() else "clean"


def _check_sync_state(repo: str) -> str:
    """H025D-FIX2: Return sync state using existing sync plan (safe fetch allowed)."""
    try:
        plan = plan_sync(repo)
        if plan.ahead == 0 and plan.behind == 0:
            return "up-to-date"
        if plan.ahead > 0 and plan.behind > 0:
            return "ready — rebase recommended"
        if plan.ahead == 0 and plan.behind > 0:
            return "ready — fast-forward recommended"
        if plan.ahead > 0 and plan.behind == 0:
            return "warning — push may be needed"
        return "unknown"
    except Exception as e:
        return f"error — {str(e)[:100]}"


def _run_preflight_checks(repo: str) -> LifecyclePreflight:
    """H025D-FIX2: Run all preflight checks for lifecycle next."""
    labels = _check_required_labels(repo)
    worktree = _check_working_tree()
    sync = _check_sync_state(repo)
    return LifecyclePreflight(
        labels_status=labels,
        sync_status=sync,
        worktree_status=worktree,
    )


def plan_lifecycle_next(
    repo: str,
    *,
    issue: int | None = None,
    pr: int | None = None,
) -> LifecycleNext:
    """Recommend the next safe operator action without performing mutations."""
    status = plan_lifecycle_status(repo, issue=issue, pr=pr)

    issue_number = status.issue_number
    pr_number = status.pr_number
    workflow_state = status.workflow_state
    prompt_exists = _prompt_exists(issue_number)
    worker_summary_exists = _worker_summary_exists(issue_number)

    # H025D-FIX2: Run preflight checks first (always)
    preflight = _run_preflight_checks(repo)

    notes = [
        "Read-only recommendation only.",
        "No GitHub mutation was performed.",
        "No local mutation was performed.",
    ]

    action = "diagnose"
    command = (
        f"signposter lifecycle status --repo {repo} "
        + (f"--issue {issue}" if issue is not None else f"--pr {pr}")
    )
    next_status = "blocked"
    reason: str | None = None
    blocked_next_action: str | None = None

    # H025D-FIX2: Completed lifecycle always wins (even if preflights have issues)
    if status.status == "complete":
        action = "none"
        command = "(none)"
        next_status = "complete"
        reason = "lifecycle already complete"

    # H025D-FIX2: Preflight blocking logic (only for non-complete lifecycles)
    elif preflight.labels_status.startswith("blocked"):
        action = "labels-ensure"
        command = f"signposter labels ensure --repo {repo} --apply"
        next_status = "blocked"
        reason = "required labels must exist before lifecycle mutations"
        blocked_next_action = "(would have recommended normal next action)"

    elif preflight.worktree_status == "dirty":
        action = "inspect-working-tree"
        command = "git status --short --branch"
        next_status = "blocked"
        reason = "local working tree must be clean before lifecycle mutation"
        blocked_next_action = "(would have recommended normal next action)"

    elif preflight.sync_status in (
        "ready — rebase recommended",
        "ready — fast-forward recommended",
    ):
        action = "sync-rebase"
        command = f"signposter sync apply --repo {repo} --rebase --apply"
        next_status = "blocked"
        reason = "repository is behind or diverged — rebase recommended before mutation"
        blocked_next_action = "(would have recommended normal next action)"

    elif issue_number is None:
        action = "diagnose-mapping"
        command = f"signposter lifecycle status --repo {repo} --pr {pr}"
        next_status = "blocked"
        reason = "associated issue could not be detected"

    elif pr_number is None and workflow_state == "state:done":
        action = "create-pr"
        command = f"signposter pr plan --repo {repo} --issue {issue_number}"
        next_status = "actionable"
        reason = "issue is done and no associated PR was detected"

    elif status.pr_state == "MERGED" and workflow_state == "state:done":
        action = "integrate-issue"
        command = f"signposter integration apply --repo {repo} --pr {pr_number} --apply"
        next_status = "actionable"
        reason = "PR is merged and issue is ready for integration"

    elif status.integrated and (status.worktree_exists or status.local_branch_exists):
        action = "cleanup"
        command = f"signposter cleanup apply --repo {repo} --pr {pr_number} --apply"
        next_status = "actionable"
        reason = "issue is integrated and local cleanup remains"

    elif workflow_state == "state:ready" and not status.worktree_exists:
        action = "create-worktree"
        command = f"signposter worktree apply --repo {repo} --issue {issue_number} --apply"
        next_status = "actionable"
        reason = "ready issue has no local worktree"

    elif workflow_state == "state:ready" and status.worktree_exists:
        action = "claim-issue"
        command = f"signposter run --repo {repo} --issue {issue_number} --claim"
        next_status = "actionable"
        reason = "ready issue has a worktree and can be claimed"

    elif workflow_state == "state:active" and not prompt_exists:
        action = "write-prompt"
        command = f"signposter run --repo {repo} --issue {issue_number} --write-prompt"
        next_status = "actionable"
        reason = "active issue has no prompt artifact"

    elif workflow_state == "state:active" and prompt_exists and not worker_summary_exists:
        action = "execute-worker"
        command = f"signposter run --repo {repo} --issue {issue_number} --execute --worktree"
        next_status = "actionable"
        reason = "active issue has a prompt but no worker summary"

    elif workflow_state == "state:active" and worker_summary_exists:
        action = "check-gate"
        command = f"signposter gate --repo {repo} --issue {issue_number}"
        next_status = "actionable"
        reason = "worker evidence exists and gate should be checked"

    elif status.pr_state == "OPEN" and status.review_decision != "APPROVED":
        action = "review-pr"
        command = f"signposter review plan --repo {repo} --pr {pr_number}"
        next_status = "actionable"
        reason = "PR is open and not approved"

    elif status.pr_state == "OPEN" and status.review_decision == "APPROVED":
        action = "merge-pr"
        command = f"signposter merge apply --repo {repo} --pr {pr_number} --apply"
        next_status = "actionable"
        reason = "PR is approved and may be mergeable"

    elif workflow_state == "state:merged" and status.issue_state == "CLOSED":
        action = "none"
        command = "(none)"
        next_status = "complete"
        reason = "issue is already closed and merged"

    return LifecycleNext(
        query_issue=issue,
        query_pr=pr,
        issue_number=issue_number,
        pr_number=pr_number,
        issue_state=status.issue_state,
        workflow_state=workflow_state,
        pr_state=status.pr_state,
        worktree_exists=status.worktree_exists,
        local_branch_exists=status.local_branch_exists,
        prompt_exists=prompt_exists,
        worker_summary_exists=worker_summary_exists,
        preflight=preflight,
        blocked_next_action=blocked_next_action,
        action=action,
        command=command,
        status=next_status,
        reason=reason,
        notes=notes,
    )


def format_lifecycle_next(result: LifecycleNext) -> str:
    """Compact deterministic next-step output."""
    if result.issue_number:
        header = f"Signposter Lifecycle Next — Issue #{result.issue_number}"
    else:
        header = f"Signposter Lifecycle Next — PR #{result.pr_number}"

    lines = [f"{header}\n"]

    lines.append("Current:")
    lines.append(f"  issue state: {result.issue_state or 'unknown'}")
    lines.append(f"  workflow state: {result.workflow_state or 'unknown'}")
    if result.pr_number:
        lines.append(f"  pr: #{result.pr_number} ({result.pr_state or 'unknown'})")
    else:
        lines.append("  pr: none detected")
    lines.append(f"  worktree: {'present' if result.worktree_exists else 'missing'}")
    lines.append(f"  local branch: {'present' if result.local_branch_exists else 'missing'}")
    lines.append(f"  prompt: {'present' if result.prompt_exists else 'missing'}")
    lines.append(f"  worker summary: {'present' if result.worker_summary_exists else 'missing'}")

    # H025D-FIX2: Always show Preflight section
    lines.append("\nPreflight:")
    lines.append(f"  labels: {result.preflight.labels_status}")
    lines.append(f"  repo sync: {result.preflight.sync_status}")
    lines.append(f"  working tree: {result.preflight.worktree_status}")
    if result.blocked_next_action:
        lines.append(f"  blocked_next_action: {result.blocked_next_action}")

    lines.append("\nNext:")
    lines.append(f"  action: {result.action}")
    lines.append(f"  command: {result.command}")
    if result.reason:
        lines.append(f"  reason: {result.reason}")

    lines.append("\nStatus:")
    lines.append(f"  {result.status}")

    if result.notes:
        lines.append("\nNotes:")
        for note in result.notes:
            lines.append(f"  {note}")

    return "\n".join(lines)
