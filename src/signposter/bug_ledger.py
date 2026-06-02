"""Deterministic local bug ledger for automation-discovered issues."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_LEDGER_PATH = Path("artifacts/automation/bug-ledger.json")
DEFAULT_RENDER_LIMIT = 8
ALLOWED_BUG_STATUSES = {
    "open",
    "fixed-in-current-issue",
    "deferred-to-issue",
    "runtime-blocker",
}


@dataclass(frozen=True)
class BugLedgerEntry:
    key: str
    summary: str
    status: str
    source_issue: int | None = None
    source_pr: int | None = None
    current_issue: int | None = None
    current_pr: int | None = None
    follow_up_issue: int | None = None
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class BugLedgerPlan:
    action: str
    path: str
    status: str
    entry: BugLedgerEntry | None
    entries: tuple[BugLedgerEntry, ...]
    notes: tuple[str, ...]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeBugLedgerRecord:
    status: str
    path: str
    entry_key: str | None = None
    error: str | None = None


RUNTIME_BUG_LEDGER_STATUSES = {
    "missing-binary",
    "missing-prompt",
    "timeout",
    "runtime-stall",
    "unsupported-model",
    "malformed-output",
    "runtime-error",
    "failover-or-stale-runtime",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _normalize_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized not in ALLOWED_BUG_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_BUG_STATUSES))
        raise ValueError(f"status must be one of {allowed}")
    return normalized


def _coerce_entry(raw: dict[str, object]) -> BugLedgerEntry:
    return BugLedgerEntry(
        key=str(raw.get("key", "")).strip(),
        summary=str(raw.get("summary", "")).strip(),
        status=_normalize_status(str(raw.get("status", "open"))),
        source_issue=_coerce_optional_int(raw.get("source_issue")),
        source_pr=_coerce_optional_int(raw.get("source_pr")),
        current_issue=_coerce_optional_int(raw.get("current_issue")),
        current_pr=_coerce_optional_int(raw.get("current_pr")),
        follow_up_issue=_coerce_optional_int(raw.get("follow_up_issue")),
        notes=str(raw.get("notes", "")).strip(),
        created_at=str(raw.get("created_at", "")).strip(),
        updated_at=str(raw.get("updated_at", "")).strip(),
    )


def _coerce_optional_int(value: object) -> int | None:
    if value in (None, "", "none"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_bug_ledger(path: str | Path = DEFAULT_LEDGER_PATH) -> tuple[BugLedgerEntry, ...]:
    ledger_path = Path(path)
    if not ledger_path.exists():
        return ()

    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("bug ledger must contain a JSON object")
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("bug ledger entries must be a JSON list")
    return tuple(_coerce_entry(raw) for raw in entries if isinstance(raw, dict))


def write_bug_ledger(
    entries: tuple[BugLedgerEntry, ...],
    path: str | Path = DEFAULT_LEDGER_PATH,
) -> None:
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "bug-ledger.v0.1",
        "entries": [
            {
                "key": entry.key,
                "summary": entry.summary,
                "status": entry.status,
                "source_issue": entry.source_issue,
                "source_pr": entry.source_pr,
                "current_issue": entry.current_issue,
                "current_pr": entry.current_pr,
                "follow_up_issue": entry.follow_up_issue,
                "notes": entry.notes,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
            }
            for entry in entries
        ],
    }
    ledger_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _next_bug_key(entries: tuple[BugLedgerEntry, ...]) -> str:
    highest = 0
    for entry in entries:
        if entry.key.startswith("BUG-"):
            try:
                highest = max(highest, int(entry.key.split("-", 1)[1]))
            except ValueError:
                continue
    return f"BUG-{highest + 1:04d}"


def plan_record_bug(
    *,
    summary: str,
    status: str = "open",
    source_issue: int | None = None,
    source_pr: int | None = None,
    current_issue: int | None = None,
    current_pr: int | None = None,
    follow_up_issue: int | None = None,
    notes: str = "",
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
) -> BugLedgerPlan:
    entries = load_bug_ledger(ledger_path)
    now = _now()
    entry = BugLedgerEntry(
        key=_next_bug_key(entries),
        summary=summary.strip(),
        status=_normalize_status(status),
        source_issue=source_issue,
        source_pr=source_pr,
        current_issue=current_issue,
        current_pr=current_pr,
        follow_up_issue=follow_up_issue,
        notes=notes.strip(),
        created_at=now,
        updated_at=now,
    )
    errors = _validate_entry(entry)
    return BugLedgerPlan(
        action="record",
        path=str(ledger_path),
        status="blocked" if errors else "ready",
        entry=entry,
        entries=entries + (entry,),
        notes=_default_notes(apply=False),
        errors=tuple(errors),
    )


def plan_update_bug(
    *,
    key: str,
    status: str | None = None,
    current_issue: int | None = None,
    current_pr: int | None = None,
    follow_up_issue: int | None = None,
    notes: str | None = None,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
) -> BugLedgerPlan:
    entries = list(load_bug_ledger(ledger_path))
    target_index = next((i for i, entry in enumerate(entries) if entry.key == key), None)
    if target_index is None:
        return BugLedgerPlan(
            action="update",
            path=str(ledger_path),
            status="blocked",
            entry=None,
            entries=tuple(entries),
            notes=_default_notes(apply=False),
            errors=(f"unknown bug key: {key}",),
        )

    entry = entries[target_index]
    updated = replace(
        entry,
        status=_normalize_status(status) if status is not None else entry.status,
        current_issue=current_issue if current_issue is not None else entry.current_issue,
        current_pr=current_pr if current_pr is not None else entry.current_pr,
        follow_up_issue=(
            follow_up_issue if follow_up_issue is not None else entry.follow_up_issue
        ),
        notes=notes.strip() if notes is not None else entry.notes,
        updated_at=_now(),
    )
    errors = _validate_entry(updated)
    entries[target_index] = updated
    return BugLedgerPlan(
        action="update",
        path=str(ledger_path),
        status="blocked" if errors else "ready",
        entry=updated,
        entries=tuple(entries),
        notes=_default_notes(apply=False),
        errors=tuple(errors),
    )


def plan_show_bugs(
    *,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    limit: int = DEFAULT_RENDER_LIMIT,
) -> BugLedgerPlan:
    entries = load_bug_ledger(ledger_path)
    bounded = entries[-limit:] if limit > 0 else entries
    status = "empty" if not entries else "ready"
    return BugLedgerPlan(
        action="show",
        path=str(ledger_path),
        status=status,
        entry=None,
        entries=bounded,
        notes=_default_notes(apply=False),
        errors=(),
    )


def apply_bug_ledger_plan(plan: BugLedgerPlan, *, apply: bool = False) -> bool:
    if plan.status != "ready":
        return False
    if not apply:
        return False
    write_bug_ledger(plan.entries, plan.path)
    return True


def record_runtime_bug_ledger_entry(
    *,
    target_kind: str,
    target_number: int,
    diagnosis_status: str,
    diagnosis_reason: str,
    selected_role: str,
    selected_model: str,
    raw_path: str,
    summary_path: str,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
) -> RuntimeBugLedgerRecord:
    """Record deterministic local runtime blockers without changing workflow state."""
    if diagnosis_status not in RUNTIME_BUG_LEDGER_STATUSES:
        return RuntimeBugLedgerRecord(status="skipped", path=str(ledger_path))

    current_issue = target_number if target_kind == "issue" else None
    current_pr = target_number if target_kind == "pr" else None
    summary = f"{target_kind} #{target_number}: runtime {diagnosis_status} for {selected_role}"
    notes = (
        f"model={selected_model}; raw={raw_path}; summary={summary_path}; "
        f"reason={diagnosis_reason[:180]}"
    )
    try:
        plan = plan_record_bug(
            summary=summary[:240],
            status="runtime-blocker",
            current_issue=current_issue,
            current_pr=current_pr,
            notes=notes,
            ledger_path=ledger_path,
        )
        if plan.status != "ready" or plan.entry is None:
            error = "; ".join(plan.errors) or "bug ledger plan was not ready"
            return RuntimeBugLedgerRecord(
                status="blocked",
                path=str(ledger_path),
                error=error,
            )
        apply_bug_ledger_plan(plan, apply=True)
        return RuntimeBugLedgerRecord(
            status="recorded",
            path=str(ledger_path),
            entry_key=plan.entry.key,
        )
    except Exception as exc:
        return RuntimeBugLedgerRecord(
            status="error",
            path=str(ledger_path),
            error=str(exc),
        )


def format_runtime_bug_ledger_record(record: RuntimeBugLedgerRecord) -> str:
    """Format runtime ledger recording status for bounded execution summaries."""
    if record.status == "recorded":
        return f"**Bug Ledger:** recorded {record.entry_key} in {record.path}"
    if record.status == "skipped":
        return "**Bug Ledger:** not applicable for this execution status"
    reason = f" ({record.error})" if record.error else ""
    return f"**Bug Ledger:** {record.status}{reason} in {record.path}"


def format_bug_ledger_plan(
    plan: BugLedgerPlan,
    *,
    apply: bool = False,
    limit: int = DEFAULT_RENDER_LIMIT,
) -> str:
    final_status = "completed" if apply and plan.status == "ready" else plan.status
    lines = [
        "Signposter Bug Ledger",
        "",
        "Action:",
        f"  {plan.action}",
        "",
        "Ledger:",
        f"  {plan.path}",
        "",
        "Status:",
        f"  {final_status}",
    ]
    if plan.entry is not None:
        lines.extend(
            [
                "",
                "Entry:",
                f"  key: {plan.entry.key}",
                f"  status: {plan.entry.status}",
                f"  summary: {plan.entry.summary}",
            ]
        )
        if plan.entry.source_issue is not None:
            lines.append(f"  source issue: #{plan.entry.source_issue}")
        if plan.entry.source_pr is not None:
            lines.append(f"  source pr: #{plan.entry.source_pr}")
        if plan.entry.current_issue is not None:
            lines.append(f"  current issue: #{plan.entry.current_issue}")
        if plan.entry.current_pr is not None:
            lines.append(f"  current pr: #{plan.entry.current_pr}")
        if plan.entry.follow_up_issue is not None:
            lines.append(f"  follow-up issue: #{plan.entry.follow_up_issue}")
        if plan.entry.notes:
            lines.append(f"  notes: {plan.entry.notes}")

    lines.extend(["", "Recent entries:"])
    if not plan.entries:
        lines.append("  none")
    else:
        for entry in plan.entries[-limit:]:
            target = ""
            if entry.follow_up_issue is not None:
                target = f" -> follow-up #{entry.follow_up_issue}"
            elif entry.current_issue is not None:
                target = f" -> current #{entry.current_issue}"
            lines.append(f"  {entry.key} [{entry.status}] {entry.summary}{target}")

    if plan.errors:
        lines.extend(["", "Errors:"])
        lines.extend(f"  - {error}" for error in plan.errors)

    lines.extend(["", "Notes:"])
    lines.extend(f"  {note}" for note in plan.notes)
    if not apply:
        lines.append("  Dry-run only. Use --apply to write the ledger.")
    return "\n".join(lines)


def _default_notes(*, apply: bool) -> tuple[str, ...]:
    return (
        "No GitHub mutation was performed.",
        "No OpenClaw execution was performed.",
        "Local ledger only." if apply else "Read-only planning only.",
    )


def _validate_entry(entry: BugLedgerEntry) -> list[str]:
    errors: list[str] = []
    if not entry.key:
        errors.append("entry key must not be empty")
    if not entry.summary:
        errors.append("summary must not be empty")
    if len(entry.summary) > 240:
        errors.append("summary must be 240 chars or fewer")
    if entry.status == "deferred-to-issue" and entry.follow_up_issue is None:
        errors.append("deferred-to-issue requires follow_up_issue")
    return errors
