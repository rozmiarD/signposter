"""Gate decision logic for Signposter.

Conservative, evidence-based gate evaluation for review gates.
All functions are pure where possible for easy testing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GateDecision:
    decision: str  # "pass", "needs-work", or "fail"
    reason: str
    confidence: str  # "high", "medium", "low"
    proposed_transition: str | None
    proposed_command: str | None


def evaluate_gate(
    exit_code: int,
    summary_text: str,
    raw_text: str | None = None,
) -> GateDecision:
    """Pure decision function based on exit code and text content.

    Conservative heuristic for BOOTSTRAP-022.
    """
    text = (summary_text or "") + "\n" + (raw_text or "")

    if exit_code != 0:
        return GateDecision(
            decision="fail",
            reason=f"Non-zero exit code from reviewer: {exit_code}",
            confidence="high",
            proposed_transition="state:failed",
            proposed_command=None,
        )

    # Look for strong negative signals
    negative_signals = [
        "critical blocker",
        "cannot proceed",
        "missing required evidence",
        "evidence is insufficient",
        "review failed",
    ]
    for signal in negative_signals:
        if signal.lower() in text.lower():
            return GateDecision(
                decision="needs-work",
                reason=f"Reviewer mentioned: '{signal}'",
                confidence="medium",
                proposed_transition="state:active (or release to state:ready)",
                proposed_command=None,
            )

    # Look for positive completion signals
    positive_signals = [
        "review complete",
        "no risks",
        "no new risks",
        "no critical",
        "ready for",
        "no blockers",
        "all constraints respected",
    ]
    positive_count = sum(1 for s in positive_signals if s.lower() in text.lower())

    if positive_count >= 2:
        return GateDecision(
            decision="pass",
            reason=(
                "Reviewer completed successfully (exit 0) with multiple "
                "positive signals and no blockers mentioned."
            ),
            confidence="medium",
            proposed_transition="state:active → state:done",
            proposed_command="signposter complete --repo {repo} --issue {issue} --apply",
        )

    # Default conservative fallback
    return GateDecision(
        decision="needs-work",
        reason=(
            "Reviewer exited 0 but did not clearly signal completion "
            "with multiple positive indicators."
        ),
        confidence="low",
        proposed_transition="state:active (reviewer should be re-run with more evidence)",
        proposed_command=None,
    )


def evaluate_ci_gate(
    exit_code: int,
    summary_text: str,
    raw_text: str | None = None,
) -> GateDecision:
    """Decision function for worker/CI gates.

    This is intentionally conservative but distinct from review gates:
    worker tasks are allowed to pass when execution completed cleanly and
    the artifact shows scoped changes/evidence.
    """
    text = ((summary_text or "") + "\n" + (raw_text or "")).lower()

    if exit_code != 0:
        return GateDecision(
            decision="fail",
            reason=f"Non-zero exit code from worker: {exit_code}",
            confidence="high",
            proposed_transition="state:failed",
            proposed_command=None,
        )

    negative_signals = [
        "critical blocker",
        "cannot proceed",
        "missing required evidence",
        "execution failed",
        "error:",
        "traceback",
    ]
    for signal in negative_signals:
        if signal in text:
            return GateDecision(
                decision="needs-work",
                reason=f"Worker output mentioned: '{signal}'",
                confidence="medium",
                proposed_transition="state:active (worker should be re-run)",
                proposed_command=None,
            )

    positive_signals = [
        "execution complete",
        "files changed",
        "readme.md",
        "only file edited",
        "git diff -- readme.md",
        "code behavior",
    ]
    positive_count = sum(1 for signal in positive_signals if signal in text)

    if positive_count >= 3:
        return GateDecision(
            decision="pass",
            reason=(
                "Worker completed successfully (exit 0) with scoped change "
                "evidence and no blocker signals."
            ),
            confidence="medium",
            proposed_transition="state:active → state:done",
            proposed_command="signposter complete --repo {repo} --issue {issue} --apply",
        )

    # H024G: Strong scoped worker completion evidence (docs-only / README-only cases)
    if _has_scoped_worker_completion_evidence(summary_text + "\n" + (raw_text or "")):
        return GateDecision(
            decision="pass",
            reason=(
                "Worker completed successfully (exit 0) with strong scoped completion evidence "
                "(scope followed 100%, dirty guard clean, README/docs-only, no code changes, "
                "no scope broadening)."
            ),
            confidence="medium",
            proposed_transition="state:active → state:done",
            proposed_command="signposter complete --repo {repo} --issue {issue} --apply",
        )

    return GateDecision(
        decision="needs-work",
        reason=(
            "Worker exited 0 but did not provide enough scoped completion "
            "evidence for the CI gate."
        ),
        confidence="low",
        proposed_transition="state:active (worker should be re-run with more evidence)",
        proposed_command=None,
    )


def _has_scoped_worker_completion_evidence(text: str) -> bool:
    """H024G: Detect strong, conservative evidence of scoped worker completion.

    Recognizes realistic low-risk docs-only / README-only worker summaries
    without making the gate permissive. Requires exit_code==0 already checked.
    """
    t = (text or "").lower()

    # Core strong signals (require several of these)
    strong_signals = [
        "scope followed: 100%",
        "scope followed 100%",
        "dirty guard: clean",
        "dirty guard clean",
        "readme.md only",
        "docs-only",
        "no code changes",
        "no scope broadening",
        "exact line",
        "exact diff",
        "only file edited",
    ]
    strong_count = sum(1 for s in strong_signals if s in t)

    # Supportive scoped evidence
    supportive = [
        "files changed",
        "readme.md",
        "git diff -- readme.md",
        "execution complete",
        "worker completed",
        "task complete",
        "no risks",
        "no blockers",
    ]
    supportive_count = sum(1 for s in supportive if s in t)

    # Negative / disqualifying signals (even if strong signals present)
    disqualifiers = [
        "task incomplete",
        "not complete",
        "incomplete",
        "dirty guard: failed",
        "dirty guard failed",
        "dirty: true",
        "working tree dirty",
        "scope not followed",
        "scope broadening",
        "unexpected change",
        "code change",
        "modified python",
        "modified src/",
    ]
    if any(d in t for d in disqualifiers):
        return False

    # Require strong evidence + some supportive context
    return strong_count >= 2 and supportive_count >= 1


def _is_already_integrated_issue(issue_state: dict) -> bool:
    """Return True if issue is CLOSED and carries the state:merged workflow label.

    This indicates the issue has completed the full lifecycle (PR merged + integrated).
    Used for early short-circuit in gate evaluation.
    """
    state = issue_state.get("state", "")
    labels = issue_state.get("labels", []) or []
    return state == "CLOSED" and "state:merged" in labels


def fetch_issue_state(repo: str, issue: int) -> dict:
    """Fetch current labels and state for the issue (read-only)."""
    result = subprocess.run(
        [
            "gh", "issue", "view", str(issue),
            "-R", repo,
            "--json", "number,title,state,labels",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch issue {issue}: {result.stderr.strip()}")

    import json
    data = json.loads(result.stdout)
    labels = [lbl["name"] for lbl in data.get("labels", [])]
    return {
        "number": data["number"],
        "title": data["title"],
        "state": data["state"],
        "labels": labels,
    }


def load_summary(summary_path: str | Path) -> str:
    path = Path(summary_path)
    if not path.exists():
        raise FileNotFoundError(f"Summary artifact not found: {path}")
    return path.read_text(encoding="utf-8")


def load_raw_if_exists(raw_path: str | Path) -> str | None:
    path = Path(raw_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def run_gate_dry_run(
    repo: str,
    issue: int,
    summary_path: str | Path,
) -> dict:
    """Main dry-run gate evaluation."""
    issue_state = fetch_issue_state(repo, issue)
    labels = issue_state["labels"]

    # HARDENING-025E-B: Already-integrated short-circuit
    # Must happen before any gate label checks or artifact loading.
    if _is_already_integrated_issue(issue_state):
        return {
            "repo": repo,
            "issue": issue,
            "issue_title": issue_state["title"],
            "current_state": issue_state["state"],
            "labels": labels,
            "is_already_integrated": True,
            "decision": "NOT-APPLICABLE",
            "reason": "Issue is already closed and integrated.",
            "confidence": "high",
            "status": "completed",
            "notes": [
                "No gate action is required.",
                "No GitHub mutation was performed.",
            ],
        }

    has_active = "state:active" in labels
    has_gate_review = "gate:review" in labels
    has_gate_ci = "gate:ci" in labels

    if has_gate_review:
        gate_type = "review"
    elif has_gate_ci:
        gate_type = "ci"
    else:
        gate_type = "unknown"

    summary_text = load_summary(summary_path)

    # Try to derive raw path and load it for better signals
    raw_path = Path(summary_path).with_name(
        Path(summary_path).name.replace(".summary.md", ".raw.txt")
    )
    raw_text = load_raw_if_exists(raw_path)

    # Extract exit code from summary (simple heuristic)
    exit_code = 0
    for line in summary_text.splitlines():
        if line.startswith("**Exit Code:**"):
            try:
                exit_code = int(line.split(":", 1)[1].strip())
            except Exception:
                pass
            break

    if gate_type == "ci":
        decision = evaluate_ci_gate(exit_code, summary_text, raw_text)
    elif gate_type == "review":
        decision = evaluate_gate(exit_code, summary_text, raw_text)
    else:
        decision = GateDecision(
            decision="needs-work",
            reason="Issue has no supported gate label (expected gate:ci or gate:review).",
            confidence="high",
            proposed_transition=None,
            proposed_command=None,
        )

    # Fill in repo/issue in proposed command if present
    proposed_cmd = None
    if decision.proposed_command:
        proposed_cmd = decision.proposed_command.format(repo=repo, issue=issue)

    return {
        "repo": repo,
        "issue": issue,
        "issue_title": issue_state["title"],
        "current_state": issue_state["state"],
        "labels": labels,
        "has_state_active": has_active,
        "has_gate_review": has_gate_review,
        "has_gate_ci": has_gate_ci,
        "gate_type": gate_type,
        "summary_path": str(summary_path),
        "raw_path": str(raw_path) if raw_path.exists() else None,
        "exit_code": exit_code,
        "decision": decision.decision,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "proposed_transition": decision.proposed_transition,
        "proposed_command": proposed_cmd,
        "valid_for_gate": has_active and gate_type in {"review", "ci"},
    }


def format_gate_report(result: dict) -> str:
    # HARDENING-025E-B: Clean output for already-integrated issues
    if result.get("is_already_integrated"):
        lines = [
            "Signposter Gate Decision (dry-run)",
            "",
            f"Repository: {result['repo']}",
            f"Issue: #{result['issue']} — {result['issue_title']}",
            "",
            "Current State:",
            f"  state: {result['current_state']}",
            f"  labels: {', '.join(result['labels'])}",
            "",
            "Decision:",
            f"  {result['decision']}",
            f"  Reason: {result['reason']}",
            f"  Confidence: {result['confidence']}",
            "",
            "Status:",
            f"  {result['status']}",
            "",
            "Notes:",
        ]
        for note in result.get("notes", []):
            lines.append(f"  {note}")
        return "\n".join(lines)

    lines = [
        "Signposter Gate Decision (dry-run)",
        "",
        f"Repository: {result['repo']}",
        f"Issue: #{result['issue']} — {result['issue_title']}",
        "",
        "Current State:",
        f"  state: {result['current_state']}",
        f"  labels: {', '.join(result['labels'])}",
        "",
        "Evidence:",
        f"  Summary: {result['summary_path']}",
    ]
    if result.get("raw_path"):
        lines.append(f"  Raw:     {result['raw_path']}")
    else:
        lines.append("  Raw:     (not found)")

    lines.extend(
        [
            "",
            "Gate Validation:",
            f"  state:active present: {result.get('has_state_active', False)}",
            f"  gate type:            {result.get('gate_type', 'unknown')}",
            f"  gate:review present:  {result.get('has_gate_review', False)}",
            f"  gate:ci present:      {result.get('has_gate_ci', False)}",
            "",
            "Decision:",
            f"  {result['decision'].upper()}",
            f"  Reason: {result['reason']}",
            f"  Confidence: {result['confidence']}",
        ]
    )

    if result.get("proposed_transition"):
        lines.append(f"  Proposed: {result['proposed_transition']}")

    if result.get("proposed_command"):
        lines.append("")
        lines.append("Suggested next command:")
        lines.append(f"  {result['proposed_command']}")

    if not result.get("valid_for_gate", False):
        lines.append("")
        lines.append("WARNING: This issue does not appear ready for a supported gate decision.")

    return "\n".join(lines)


def evaluate_gate_for_complete(repo: str, issue: int) -> tuple[bool, str, str, str]:
    """H024F: Evaluate whether the current gate for this issue allows 'complete'.

    Returns:
        (passes: bool, decision: str, reason: str, gate_type: str)

    Reuses existing gate machinery. Only 'gate:ci' is currently enforced for complete.
    If no supported gate label is present, returns:
    (True, 'no-gate', 'no gate label present', 'none').
    """
    try:
        issue_state = fetch_issue_state(repo, issue)
    except Exception as e:
        return False, "error", f"Failed to fetch issue state: {e}", "error"

    labels = issue_state.get("labels", [])
    has_gate_ci = "gate:ci" in labels
    has_gate_review = "gate:review" in labels

    if not has_gate_ci and not has_gate_review:
        # No gate label present — preserve existing (non-gated) behavior for now
        return True, "no-gate", "no supported gate label present", "none"

    # Find latest summary artifact for this issue
    from signposter.cli import _find_latest_summary_for_issue

    summary_path = _find_latest_summary_for_issue(issue)
    if not summary_path:
        return (
            False,
            "needs-work",
            (
                f"No summary artifact found for issue #{issue} "
                f"(expected artifacts/runs/issue-{issue}-*.summary.md)"
            ),
            "ci" if has_gate_ci else "review",
        )

    try:
        summary_text = load_summary(summary_path)
    except Exception as e:
        return False, "error", f"Failed to load summary: {e}", "ci" if has_gate_ci else "review"

    # Load raw if present for better signals
    from pathlib import Path
    raw_path = Path(summary_path).with_name(
        Path(summary_path).name.replace(".summary.md", ".raw.txt")
    )
    raw_text = load_raw_if_exists(raw_path)

    # Extract exit code
    exit_code = 0
    for line in summary_text.splitlines():
        if line.startswith("**Exit Code:**"):
            try:
                exit_code = int(line.split(":", 1)[1].strip())
            except Exception:
                pass
            break

    if has_gate_ci:
        decision = evaluate_ci_gate(exit_code, summary_text, raw_text)
        gate_type = "ci"
    else:
        decision = evaluate_gate(exit_code, summary_text, raw_text)
        gate_type = "review"

    passes = decision.decision == "pass"
    return passes, decision.decision, decision.reason, gate_type
