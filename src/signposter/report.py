"""Report runner execution summaries back to GitHub issues.

Safety-first design:
- Default is dry-run (no GitHub writes)
- --apply is required for any mutation
- Only posts a concise summary, never full raw logs
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from signposter.artifact import (
    format_worker_artifact_validation,
    validate_worker_summary_artifact,
)
from signposter.artifact_safety import find_stale_or_failover_signal
from signposter.comments import (
    DEFAULT_MAX_COMMENT_CHARS,
    audit_github_comment_body,
    ensure_github_comment_body,
)

# Regex to strip ANSI escape sequences (colors, cursor moves, etc.)
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_PREFERRED_EXCERPT_SECTIONS = (
    "## Scoped completion evidence",
    "## Validation evidence",
    "## Gate recommendation",
)
REPORT_COMMENT_MAX_CHARS = DEFAULT_MAX_COMMENT_CHARS
_REPORT_SUMMARY_FIELD_MAX_CHARS = 240
_REPORT_EXCERPT_BUDGETS = (
    (12, 900),
    (8, 650),
    (4, 320),
    (2, 180),
)
_REPORT_IMPLEMENTED_BUDGET = (3, 360)
_LOCAL_ARTIFACT_MARKERS = (
    "artifacts/",
    ".signposter-local/",
    "signposter-artifacts/",
    ".local/signposter/",
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text.

    Used to sanitize raw runner output before including excerpts in GitHub comments.
    Does not modify source artifacts on disk.
    """
    if not text:
        return text
    return _ANSI_ESCAPE_RE.sub("", text)


def load_summary(summary_path: str | Path) -> str:
    """Read the local summary artifact."""
    path = Path(summary_path)
    if not path.exists():
        raise FileNotFoundError(f"Summary artifact not found: {path}")
    return path.read_text(encoding="utf-8")


def derive_raw_path(summary_path: str | Path) -> Path:
    """Derive the raw output path from a summary path.

    Example: artifacts/runs/issue-2-reviewer.summary.md
          -> artifacts/runs/issue-2-reviewer.raw.txt
    """
    p = Path(summary_path)
    return p.with_name(p.name.replace(".summary.md", ".raw.txt"))


def format_comment(
    summary_content: str,
    repo: str,
    issue: int,
    *,
    summary_path: str | Path | None = None,
    raw_path: str | Path | None = None,
    prompt_path: str | None = None,
    raw_content: str | None = None,
) -> str:
    """Create a safe, concise GitHub comment from a runner summary.

    Never posts full raw output or local artifact paths. Uses bounded excerpts only.
    """
    lines = [
        "# Signposter Report",
        "",
        "## Findings",
        "",
    ]
    lines.extend(_report_findings_lines(summary_content))
    lines.extend(
        [
            "",
            "## Implemented",
            "",
            _implemented_summary_excerpt(summary_content),
            "",
            "## Impact",
            "",
            f"- Repository: `{_bounded_inline(repo, _REPORT_SUMMARY_FIELD_MAX_CHARS)}`",
            f"- Issue: `#{issue}`",
            "- Local artifacts remain local-only; no lifecycle transition is implied.",
        ]
    )

    excerpt_source = _select_evidence_excerpt_source(summary_content, raw_content)
    return _fit_report_comment(lines, excerpt_source)


def _bounded_inline(text: str, max_chars: int) -> str:
    """Return a single-line value bounded for inclusion in report comments."""
    text = strip_ansi(text or "").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    marker = "... (truncated)"
    return text[: max(0, max_chars - len(marker))].rstrip() + marker


def _report_findings_lines(summary_content: str) -> list[str]:
    """Return short status bullets from the local summary without artifact paths."""
    fields = (
        "**Agent:**",
        "**Exit Code:**",
        "**Execution Status:**",
        "**Execution Reason:**",
        "**Output Size:**",
        "**Started (UTC):**",
    )
    findings: list[str] = []
    for line in (summary_content or "").splitlines():
        stripped = line.strip()
        if any(stripped.startswith(field) for field in fields):
            findings.append(f"- {_bounded_inline(stripped, _REPORT_SUMMARY_FIELD_MAX_CHARS)}")
        if len(findings) >= 5:
            break

    if findings:
        return findings
    return ["- Local runner summary was parsed; detailed evidence remains local."]


def _implemented_summary_excerpt(summary_content: str) -> str:
    """Return a compact implemented-behavior excerpt for the GitHub-facing comment."""
    section = _extract_summary_section(
        summary_content,
        ("## Implemented behavior / verified behavior", "## Scoped completion evidence"),
    )
    if not section:
        return "- See bounded evidence below; no implementation summary section was found."
    max_lines, max_chars = _REPORT_IMPLEMENTED_BUDGET
    sanitized = _strip_local_artifact_reference_lines(section)
    return _make_bounded_excerpt(sanitized, max_lines=max_lines, max_chars=max_chars)


def _build_report_comment(lines: list[str], excerpt: str, *, fenced: bool = True) -> str:
    """Build the final report comment body from bounded sections."""
    body_lines = [*lines, "", "## Evidence (bounded)", ""]
    if fenced:
        body_lines.extend(["```", excerpt, "```", ""])
    else:
        body_lines.extend([excerpt, ""])

    body_lines.append(
        "_This comment was posted by `signposter report --apply`. "
        "Full execution logs remain local only._"
    )
    return "\n".join(body_lines)


def _fit_report_comment(lines: list[str], excerpt_source: str) -> str:
    """Return a GitHub-safe report comment that fits the report body budget."""
    for max_lines, max_chars in _REPORT_EXCERPT_BUDGETS:
        excerpt = _make_bounded_excerpt(
            excerpt_source,
            max_lines=max_lines,
            max_chars=max_chars,
        )
        candidate = _build_report_comment(lines, excerpt)
        if len(candidate) <= REPORT_COMMENT_MAX_CHARS:
            return ensure_github_comment_body(
                candidate,
                max_chars=REPORT_COMMENT_MAX_CHARS,
            )

    omitted = (
        f"(evidence excerpt omitted; report comment limited to "
        f"{REPORT_COMMENT_MAX_CHARS} chars)"
    )
    candidate = _build_report_comment(lines, omitted, fenced=False)
    if len(candidate) <= REPORT_COMMENT_MAX_CHARS:
        return ensure_github_comment_body(candidate, max_chars=REPORT_COMMENT_MAX_CHARS)

    # If unusually long metadata still consumes the budget, emit a compact,
    # auditable fallback instead of failing before the GitHub comment boundary.
    fallback = "\n".join(
        [
            "# Signposter Report",
            "",
            "## Report Summary",
            "",
            "Signposter report body exceeded the size budget after local truncation.",
            "",
            "## Evidence (bounded)",
            "",
            omitted,
            "",
            "_This comment was posted by `signposter report --apply`. "
            "Full execution logs remain local only._",
        ]
    )
    return ensure_github_comment_body(fallback, max_chars=REPORT_COMMENT_MAX_CHARS)


def format_report_comment_audit(body: str) -> str:
    """Return a compact boundedness audit for the final GitHub report comment."""
    audit = audit_github_comment_body(body, max_chars=REPORT_COMMENT_MAX_CHARS)
    status = "pass" if audit.valid else "blocked"
    bounded = "yes" if audit.char_count <= REPORT_COMMENT_MAX_CHARS else "no"
    lines = [
        "Report comment boundedness audit:",
        f"  status: {status}",
        f"  chars: {audit.char_count}/{REPORT_COMMENT_MAX_CHARS}",
        f"  bounded: {bounded}",
        "  raw logs: local only",
    ]
    for error in audit.errors:
        lines.append(f"  error: {error}")
    for note in audit.notes:
        lines.append(f"  note: {note}")
    return "\n".join(lines)


def _extract_preferred_summary_sections(summary_content: str) -> str | None:
    """Return preferred evidence sections from a worker/reviewer summary."""
    selected = _extract_summary_sections(summary_content, _PREFERRED_EXCERPT_SECTIONS)
    if not selected:
        return None
    return "\n\n".join(selected)


def _extract_summary_section(summary_content: str, headings: tuple[str, ...]) -> str | None:
    selected = _extract_summary_sections(summary_content, headings)
    return selected[0] if selected else None


def _extract_summary_sections(summary_content: str, headings: tuple[str, ...]) -> list[str]:
    lines = (summary_content or "").splitlines()
    selected_sections: list[str] = []

    for heading in headings:
        try:
            start = next(
                idx for idx, line in enumerate(lines) if line.strip() == heading
            )
        except StopIteration:
            continue

        end = len(lines)
        for idx in range(start + 1, len(lines)):
            line = lines[idx].strip()
            if line.startswith("## ") and line != heading:
                end = idx
                break

        section = "\n".join(lines[start:end]).strip()
        if section:
            selected_sections.append(section)

    if not selected_sections:
        return []
    return selected_sections


def _select_evidence_excerpt_source(
    summary_content: str,
    raw_content: str | None,
) -> str:
    """Prefer structured evidence summary sections, then raw output, then summary."""
    preferred = _extract_preferred_summary_sections(summary_content)
    if preferred:
        return _strip_local_artifact_reference_lines(preferred)
    return _strip_local_artifact_reference_lines(raw_content or summary_content)


def _strip_local_artifact_reference_lines(text: str) -> str:
    """Remove local artifact path lines before building GitHub-facing excerpts."""
    lines = (text or "").splitlines()
    kept = [
        line
        for line in lines
        if not any(marker in line for marker in _LOCAL_ARTIFACT_MARKERS)
    ]
    if kept:
        return "\n".join(kept)
    return "(local artifact references omitted; detailed evidence remains local)"


def _make_bounded_excerpt(text: str, max_lines: int = 20, max_chars: int = 1500) -> str:
    """Return a safe, bounded excerpt from raw or summary output.

    ANSI escape codes are stripped so they do not appear in GitHub comments.
    """
    if not text:
        return "(no output captured)"

    text = strip_ansi(text)
    lines = text.splitlines()
    line_limit = max(0, max_lines)
    char_limit = max(0, max_chars)
    selected: list[str] = []
    used_chars = 0

    for line in lines[:line_limit]:
        separator_chars = 1 if selected else 0
        remaining_chars = char_limit - used_chars - separator_chars
        if remaining_chars <= 0:
            break

        selected.append(line[:remaining_chars])
        used_chars += separator_chars + len(selected[-1])
        if len(line) > remaining_chars:
            break

    excerpt = "\n".join(selected)
    omitted_lines = max(len(lines) - len(selected), 0)
    omitted_chars = max(len(text) - len(excerpt), 0)
    if omitted_lines or omitted_chars:
        marker = (
            f"... (omitted; excerpt limited to {line_limit} lines / {char_limit} chars; "
            f"omitted {omitted_lines} lines / {omitted_chars} chars)"
        )
        excerpt = f"{excerpt}\n{marker}" if excerpt else marker
    return excerpt or "(no content)"


def load_raw_output(raw_path: str | Path) -> str | None:
    """Try to load raw output for excerpt. Returns None if missing."""
    path = Path(raw_path)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def find_report_artifact_safety_signal(
    summary_content: str,
    raw_content: str | None = None,
) -> str | None:
    """Return stale/failover signal that should block GitHub reporting."""
    return find_stale_or_failover_signal(
        (summary_content or "") + "\n" + (raw_content or "")
    )


def _is_worker_summary_artifact(path: Path) -> bool:
    return path.name.endswith("-worker.summary.md")


def post_comment(
    repo: str,
    issue: int,
    body: str,
    *,
    dry_run: bool = True,
) -> list[str]:
    """Post (or dry-run) a comment to a GitHub issue.

    Returns the list of gh commands that were (or would be) executed.
    """
    body = ensure_github_comment_body(body)

    cmd = [
        "gh",
        "issue",
        "comment",
        str(issue),
        "-R",
        repo,
        "--body",
        body,
    ]

    if dry_run:
        return [ " ".join(cmd) + "   # (dry-run, not executed)" ]

    # Real execution
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"gh issue comment failed: {result.stderr.strip()}")

    return cmd


def report_main(
    repo: str,
    issue: int,
    summary_path: str | Path,
    *,
    apply: bool = False,
) -> int:
    """Main entry point for the report command."""
    try:
        summary_content = load_summary(summary_path)
        summary_p = Path(summary_path)
        raw_p = derive_raw_path(summary_p)

        raw_content = load_raw_output(raw_p)
        stale_signal = find_report_artifact_safety_signal(summary_content, raw_content)
        if stale_signal:
            print("Report blocked: local artifact contains stale/failover signal.")
            print(f"Signal: {stale_signal}")
            print("No GitHub mutation was performed.")
            print("Use an explicit human/operator summary artifact after reviewing local logs.")
            return 1

        if _is_worker_summary_artifact(summary_p):
            worker_validation = validate_worker_summary_artifact(
                issue,
                summary_path=summary_p,
            )
            if worker_validation.status != "pass":
                print("Report blocked: worker summary validation did not pass.")
                print("")
                print(format_worker_artifact_validation(worker_validation))
                print("")
                print("No GitHub mutation was performed.")
                print(
                    "Run: signposter artifact validate-worker-summary "
                    f"--issue {issue} --summary {summary_p}"
                )
                return 1

        # Try to extract prompt path from summary
        prompt_path = None
        for line in summary_content.splitlines():
            if "Prompt Artifact" in line and ":" in line:
                # Handle both "**Prompt Artifact:** foo" and "**Prompt Artifact:** foo"
                parts = line.split(":", 1)
                if len(parts) > 1:
                    prompt_path = parts[1].strip().lstrip("* ")
                break

        comment_body = format_comment(
            summary_content,
            repo,
            issue,
            summary_path=str(summary_p),
            raw_path=str(raw_p) if raw_p.exists() else None,
            prompt_path=prompt_path,
            raw_content=raw_content,
        )
        comment_audit = audit_github_comment_body(
            comment_body,
            max_chars=REPORT_COMMENT_MAX_CHARS,
        )
        if not comment_audit.valid:
            print("Report blocked: final comment body failed boundedness audit.")
            print(format_report_comment_audit(comment_body))
            print("No GitHub mutation was performed.")
            return 1

        dry_run = not apply

        print("=== Signposter Report (dry-run mode)" if dry_run else "=== Signposter Report")
        print(f"Target: {repo}#{issue}")
        print(f"Source summary: {summary_p}")
        print("")
        print(format_report_comment_audit(comment_body))
        print("\n--- Proposed Comment Body ---\n")
        print(comment_body)
        print("\n--- End of Comment ---\n")

        commands = post_comment(repo, issue, comment_body, dry_run=dry_run)

        if dry_run:
            print("=== Dry-run: No GitHub mutation performed ===")
            print("Would run:")
            for c in commands:
                print(f"  {c}")
        else:
            print("=== Applied ===")
            print(f"Posted comment to {repo}#{issue}")

        return 0

    except Exception as e:
        print(f"Report failed: {e}", file=__import__("sys").stderr)
        return 1
