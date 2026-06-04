"""Token usage accounting helpers for execution artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenUsageAccounting:
    """Bounded token/cost evidence for an execution role."""

    role: str
    model: str
    reasoning_effort: str
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
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=estimated_cost_usd,
        source=source,
    )


def format_token_usage_accounting(accounting: TokenUsageAccounting) -> str:
    """Render deterministic token/cost evidence for local summaries."""
    return "\n".join(
        [
            "## Token usage accounting",
            "",
            f"Status: {accounting.status}",
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
