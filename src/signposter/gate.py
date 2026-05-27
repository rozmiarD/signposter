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
    summary_path: str | Path = "artifacts/runs/issue-2-reviewer.summary.md",
) -> dict:
    """Main dry-run gate evaluation."""
    issue_state = fetch_issue_state(repo, issue)
    labels = issue_state["labels"]

    has_active = "state:active" in labels
    has_gate_review = "gate:review" in labels

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

    decision = evaluate_gate(exit_code, summary_text, raw_text)

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
        "summary_path": str(summary_path),
        "raw_path": str(raw_path) if raw_path.exists() else None,
        "exit_code": exit_code,
        "decision": decision.decision,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "proposed_transition": decision.proposed_transition,
        "proposed_command": proposed_cmd,
        "valid_for_gate": has_active and has_gate_review,
    }


def format_gate_report(result: dict) -> str:
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
    if result["raw_path"]:
        lines.append(f"  Raw:     {result['raw_path']}")
    else:
        lines.append("  Raw:     (not found)")

    lines.extend(
        [
            "",
            "Gate Validation:",
            f"  state:active present: {result['has_state_active']}",
            f"  gate:review present:  {result['has_gate_review']}",
            "",
            "Decision:",
            f"  {result['decision'].upper()}",
            f"  Reason: {result['reason']}",
            f"  Confidence: {result['confidence']}",
        ]
    )

    if result["proposed_transition"]:
        lines.append(f"  Proposed: {result['proposed_transition']}")

    if result["proposed_command"]:
        lines.append("")
        lines.append("Suggested next command:")
        lines.append(f"  {result['proposed_command']}")

    if not result["valid_for_gate"]:
        lines.append("")
        lines.append("WARNING: This issue does not appear ready for a review gate decision.")

    return "\n".join(lines)
