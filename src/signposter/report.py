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

from signposter.artifact_safety import find_stale_or_failover_signal

# Regex to strip ANSI escape sequences (colors, cursor moves, etc.)
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


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

    Never posts full raw output. Uses bounded excerpt only.
    """
    lines = [
        "# Signposter Runner Report",
        "",
        f"**Repository:** {repo}",
        f"**Issue:** #{issue}",
        "",
        "## Execution Summary",
        "",
    ]

    # Extract key fields from the summary
    for line in summary_content.splitlines():
        if line.startswith("**Agent:**"):
            lines.append(line)
        elif line.startswith("**Exit Code:**"):
            lines.append(line)
        elif line.startswith("**Output Size:**"):
            lines.append(line)
        elif line.startswith("**Started (UTC):**"):
            lines.append(line)

    # Local artifact paths (explicit and clear)
    lines.extend(
        [
            "",
            "## Local Artifacts",
            "",
        ]
    )

    if summary_path:
        lines.append(f"- **Summary:** `{summary_path}`")
    else:
        lines.append("- **Summary:** missing")

    if raw_path:
        lines.append(f"- **Raw output:** `{raw_path}` (full log, stored locally)")
    else:
        lines.append("- **Raw output:** missing")

    if prompt_path:
        lines.append(f"- **Prompt used:** `{prompt_path}`")
    else:
        lines.append("- **Prompt used:** missing")

    # Key Excerpt - prefer raw content if provided
    excerpt_source = raw_content or summary_content
    excerpt = _make_bounded_excerpt(excerpt_source)

    lines.extend(
        [
            "",
            "## Key Excerpt (first ~20 lines or 1500 chars)",
            "",
            "```",
            excerpt,
            "```",
            "",
        ]
    )

    lines.append(
        "_This comment was posted by `signposter report --apply`. "
        "Full execution logs remain local only._"
    )

    return "\n".join(lines)


def _make_bounded_excerpt(text: str, max_lines: int = 20, max_chars: int = 1500) -> str:
    """Return a safe, bounded excerpt from raw or summary output.

    ANSI escape codes are stripped so they do not appear in GitHub comments.
    """
    if not text:
        return "(no output captured)"

    # Sanitize ANSI before any excerpt processing (GitHub comments must be clean)
    text = strip_ansi(text)
    lines = text.splitlines()
    # Take first N lines, but also respect total char budget
    selected = []
    total_chars = 0
    for line in lines[:max_lines]:
        if total_chars + len(line) + 1 > max_chars:
            break
        selected.append(line[:300])  # truncate extremely long lines
        total_chars += len(line) + 1

    excerpt = "\n".join(selected)
    if len(text) > max_chars or len(lines) > max_lines:
        excerpt += "\n... (truncated)"
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

        dry_run = not apply

        print("=== Signposter Report (dry-run mode)" if dry_run else "=== Signposter Report")
        print(f"Target: {repo}#{issue}")
        print(f"Source summary: {summary_p}")
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
