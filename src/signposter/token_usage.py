"""Token usage accounting helpers for execution artifacts."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class TokenUsageAccounting:
    """Bounded token/cost evidence for an execution role."""

    role: str
    model: str
    reasoning_effort: str
    backend: str = "unknown"
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: str | None = None
    source: str = "backend did not report token usage"

    @property
    def status(self) -> str:
        if any(
            value is not None
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.total_tokens,
                self.estimated_cost_usd,
            )
        ):
            return "reported"
        return "unknown"


@dataclass(frozen=True)
class TokenUsageAggregate:
    """Grouped token/cost evidence for repeated execution roles."""

    backend: str
    role: str
    model: str
    reasoning_effort: str
    runs: int
    reported_runs: int
    unknown_runs: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: str | None = None

    @property
    def status(self) -> str:
        return "reported" if self.reported_runs else "unknown"


_INT_PATTERNS: dict[str, tuple[str, ...]] = {
    "input_tokens": (
        r"\binput[_ -]?tokens\b\s*[:=]\s*([0-9][0-9_,]*)",
        r"\bprompt[_ -]?tokens\b\s*[:=]\s*([0-9][0-9_,]*)",
    ),
    "output_tokens": (
        r"\boutput[_ -]?tokens\b\s*[:=]\s*([0-9][0-9_,]*)",
        r"\bcompletion[_ -]?tokens\b\s*[:=]\s*([0-9][0-9_,]*)",
    ),
    "total_tokens": (
        r"\btotal[_ -]?tokens\b\s*[:=]\s*([0-9][0-9_,]*)",
    ),
}

_COST_PATTERNS: tuple[str, ...] = (
    r"\bestimated[_ -]?cost[_ -]?usd\b\s*[:=]\s*\$?([0-9]+(?:\.[0-9]+)?)",
    r"\bcost[_ -]?usd\b\s*[:=]\s*\$?([0-9]+(?:\.[0-9]+)?)",
    r"\bcost\b\s*[:=]\s*\$([0-9]+(?:\.[0-9]+)?)",
)


def summarize_token_usage(
    *,
    role: str,
    model: str,
    reasoning_effort: str,
    backend: str = "unknown",
    output_text: str = "",
) -> TokenUsageAccounting:
    """Extract optional token/cost evidence, falling back to explicit unknown."""
    input_tokens = _extract_int(output_text, _INT_PATTERNS["input_tokens"])
    output_tokens = _extract_int(output_text, _INT_PATTERNS["output_tokens"])
    total_tokens = _extract_int(output_text, _INT_PATTERNS["total_tokens"])
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    estimated_cost_usd = _extract_cost(output_text)
    source = (
        "raw execution output token fields"
        if any(
            value is not None
            for value in (input_tokens, output_tokens, total_tokens, estimated_cost_usd)
        )
        else "backend did not report token usage"
    )

    return TokenUsageAccounting(
        role=role,
        model=model,
        reasoning_effort=reasoning_effort,
        backend=backend,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=estimated_cost_usd,
        source=source,
    )


def aggregate_token_usage_by_role(
    records: Iterable[TokenUsageAccounting],
) -> tuple[TokenUsageAggregate, ...]:
    """Aggregate token/cost evidence by backend, role, model, and reasoning."""
    groups: dict[tuple[str, str, str, str], list[TokenUsageAccounting]] = {}
    for record in records:
        key = (
            record.backend or "unknown",
            record.role or "unknown",
            record.model or "unknown",
            record.reasoning_effort or "unknown",
        )
        groups.setdefault(key, []).append(record)

    aggregates: list[TokenUsageAggregate] = []
    for (backend, role, model, reasoning_effort), items in sorted(groups.items()):
        input_tokens: int | None = None
        output_tokens: int | None = None
        total_tokens: int | None = None
        estimated_cost: Decimal | None = None
        reported_runs = 0
        for item in items:
            if item.status == "reported":
                reported_runs += 1
            input_tokens = _sum_optional_int(input_tokens, item.input_tokens)
            output_tokens = _sum_optional_int(output_tokens, item.output_tokens)
            total_tokens = _sum_optional_int(total_tokens, item.total_tokens)
            estimated_cost = _sum_optional_decimal(estimated_cost, item.estimated_cost_usd)
        aggregates.append(
            TokenUsageAggregate(
                backend=backend,
                role=role,
                model=model,
                reasoning_effort=reasoning_effort,
                runs=len(items),
                reported_runs=reported_runs,
                unknown_runs=len(items) - reported_runs,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=_format_optional_decimal(estimated_cost),
            )
        )
    return tuple(aggregates)


def format_token_usage_aggregates(aggregates: Iterable[TokenUsageAggregate]) -> str:
    """Render deterministic aggregate token/cost evidence for operators."""
    items = tuple(aggregates)
    lines = ["## Token usage aggregates", ""]
    if not items:
        lines.append("Status: none")
        return "\n".join(lines)
    for aggregate in items:
        lines.extend(
            [
                f"- Backend: {aggregate.backend}",
                f"  Role: {aggregate.role}",
                f"  Model: {aggregate.model}",
                f"  Reasoning: {aggregate.reasoning_effort}",
                f"  Runs: {aggregate.runs}",
                f"  Reported runs: {aggregate.reported_runs}",
                f"  Unknown runs: {aggregate.unknown_runs}",
                f"  Input tokens: {_format_optional_int(aggregate.input_tokens)}",
                f"  Output tokens: {_format_optional_int(aggregate.output_tokens)}",
                f"  Total tokens: {_format_optional_int(aggregate.total_tokens)}",
                f"  Estimated cost USD: {aggregate.estimated_cost_usd or 'unknown'}",
            ]
        )
    return "\n".join(lines)


def format_token_usage_accounting(accounting: TokenUsageAccounting) -> str:
    """Render deterministic token/cost evidence for local summaries."""
    return "\n".join(
        [
            "## Token usage accounting",
            "",
            f"Status: {accounting.status}",
            f"Backend: {accounting.backend or 'unknown'}",
            f"Role: {accounting.role or 'unknown'}",
            f"Model: {accounting.model or 'unknown'}",
            f"Reasoning: {accounting.reasoning_effort or 'unknown'}",
            f"Input tokens: {_format_optional_int(accounting.input_tokens)}",
            f"Output tokens: {_format_optional_int(accounting.output_tokens)}",
            f"Total tokens: {_format_optional_int(accounting.total_tokens)}",
            f"Estimated cost USD: {accounting.estimated_cost_usd or 'unknown'}",
            f"Source: {accounting.source}",
        ]
    )


def _extract_int(text: str, patterns: tuple[str, ...]) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", "").replace("_", ""))
    return None


def _extract_cost(text: str) -> str | None:
    for pattern in _COST_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _format_optional_int(value: int | None) -> str:
    return str(value) if value is not None else "unknown"


def _sum_optional_int(current: int | None, value: int | None) -> int | None:
    if value is None:
        return current
    return (current or 0) + value


def _sum_optional_decimal(current: Decimal | None, value: str | None) -> Decimal | None:
    if not value:
        return current
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return current
    return (current or Decimal("0")) + parsed


def _format_optional_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
