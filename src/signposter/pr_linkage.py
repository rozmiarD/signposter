"""PR-to-issue linkage parsing helpers.

The helpers are intentionally local and deterministic. They do not query
GitHub and they do not treat auto-close keywords as a safe linkage source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from signposter.comments import contains_auto_close_keyword


@dataclass(frozen=True)
class PrIssueLinkage:
    associated_issue: int | None
    status: str
    source: str
    confidence: str
    reason: str
    candidates: dict[str, int]

    @property
    def ambiguous(self) -> bool:
        return self.status == "ambiguous"


def _first_match(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    return int(match.group(1))


def _confidence_wording(source: str, confidence: str) -> str:
    if source == "branch-pattern":
        return f"confidence: {confidence}; branch naming is the strongest Signposter signal"
    if source == "pr-body-related-issue":
        return f"confidence: {confidence}; explicit Related issue line fallback"
    if source == "pr-body-issue-reference":
        return f"confidence: {confidence}; generic issue mention fallback"
    if source == "ambiguous":
        return f"confidence: {confidence}; conflicting linkage signals require inspection"
    return f"confidence: {confidence}"


def detect_pr_issue_linkage(head_branch: str | None, body: str | None) -> PrIssueLinkage:
    """Detect a single safe associated issue for a Signposter PR.

    Branch convention remains the strongest signal. Body references are accepted
    only when they do not conflict with the branch signal or each other.
    """
    head = head_branch or ""
    text = body or ""
    body_has_auto_close = contains_auto_close_keyword(text)
    candidates: dict[str, int] = {}

    branch_issue = _first_match(r"(?:^|/)issue-(\d+)(?:-|$)", head)
    if branch_issue is not None:
        candidates["branch-pattern"] = branch_issue

    related_issue = _first_match(r"^\s*Related issue:\s*#?(\d+)\b", text)
    if related_issue is not None:
        candidates["pr-body-related-issue"] = related_issue

    generic_issue = (
        None
        if body_has_auto_close
        else _first_match(r"\bissue\s*#(\d+)\b", text)
    )
    if generic_issue is not None and "pr-body-related-issue" not in candidates:
        candidates["pr-body-issue-reference"] = generic_issue

    unique_issue_numbers = set(candidates.values())
    if len(unique_issue_numbers) > 1:
        details = ", ".join(
            f"{source}=#{number}" for source, number in sorted(candidates.items())
        )
        return PrIssueLinkage(
            associated_issue=None,
            status="ambiguous",
            source="ambiguous",
            confidence="low",
            reason=(
                f"associated issue link is ambiguous ({details}); "
                f"{_confidence_wording('ambiguous', 'low')}"
            ),
            candidates=candidates,
        )

    if branch_issue is not None:
        return PrIssueLinkage(
            associated_issue=branch_issue,
            status="detected",
            source="branch-pattern",
            confidence="high",
            reason=(
                f"associated issue detected from branch pattern: #{branch_issue}; "
                f"{_confidence_wording('branch-pattern', 'high')}"
            ),
            candidates=candidates,
        )

    if related_issue is not None:
        return PrIssueLinkage(
            associated_issue=related_issue,
            status="detected",
            source="pr-body-related-issue",
            confidence="medium",
            reason=(
                f"associated issue detected from Related issue line: #{related_issue}; "
                f"{_confidence_wording('pr-body-related-issue', 'medium')}"
            ),
            candidates=candidates,
        )

    if generic_issue is not None:
        return PrIssueLinkage(
            associated_issue=generic_issue,
            status="detected",
            source="pr-body-issue-reference",
            confidence="low",
            reason=(
                f"associated issue detected from issue reference: #{generic_issue}; "
                f"{_confidence_wording('pr-body-issue-reference', 'low')}"
            ),
            candidates=candidates,
        )

    return PrIssueLinkage(
        associated_issue=None,
        status="missing",
        source="unknown",
        confidence="low",
        reason="associated issue could not be detected; confidence: low",
        candidates=candidates,
    )
