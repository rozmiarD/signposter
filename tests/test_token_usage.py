from signposter.token_usage import (
    aggregate_token_usage_by_role,
    format_token_usage_accounting,
    format_token_usage_aggregates,
    summarize_token_usage,
)


def test_token_usage_reports_unknown_when_backend_omits_usage() -> None:
    accounting = summarize_token_usage(
        role="WORKER_CORE",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        output_text="completed without usage metadata",
    )

    assert accounting.status == "unknown"
    assert accounting.input_tokens is None
    assert accounting.output_tokens is None
    assert accounting.total_tokens is None
    assert accounting.backend == "unknown"
    assert accounting.source == "backend did not report token usage"

    output = format_token_usage_accounting(accounting)
    assert "Status: unknown" in output
    assert "Backend: unknown" in output
    assert "Input tokens: unknown" in output
    assert "Estimated cost USD: unknown" in output


def test_token_usage_extracts_common_usage_fields() -> None:
    accounting = summarize_token_usage(
        role="codex_worker_core",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        backend="codex-cli",
        output_text=(
            "usage: input_tokens=1,200 output_tokens=300 "
            "total_tokens=1,500 estimated_cost_usd=0.0123"
        ),
    )

    assert accounting.status == "reported"
    assert accounting.input_tokens == 1200
    assert accounting.output_tokens == 300
    assert accounting.total_tokens == 1500
    assert accounting.estimated_cost_usd == "0.0123"
    assert accounting.backend == "codex-cli"
    assert accounting.source == "raw execution output token fields"


def test_token_usage_derives_total_when_only_prompt_and_completion_are_reported() -> None:
    accounting = summarize_token_usage(
        role="REVIEWER_CORE",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        output_text="prompt_tokens: 100 completion_tokens: 25",
    )

    assert accounting.status == "reported"
    assert accounting.input_tokens == 100
    assert accounting.output_tokens == 25
    assert accounting.total_tokens == 125


def test_token_usage_aggregates_by_backend_role_model_and_reasoning() -> None:
    records = (
        summarize_token_usage(
            backend="codex-cli",
            role="WORKER_CORE",
            model="openai/gpt-5.4",
            reasoning_effort="medium",
            output_text="input_tokens=100 output_tokens=50 cost_usd=0.003",
        ),
        summarize_token_usage(
            backend="codex-cli",
            role="WORKER_CORE",
            model="openai/gpt-5.4",
            reasoning_effort="medium",
            output_text="prompt_tokens=25 completion_tokens=5 estimated_cost_usd=0.001",
        ),
        summarize_token_usage(
            backend="openclaw",
            role="REVIEWER_LIGHT",
            model="xai/grok-build-0.1",
            reasoning_effort="low",
            output_text="provider omitted usage",
        ),
    )

    aggregates = aggregate_token_usage_by_role(records)

    assert len(aggregates) == 2
    codex = aggregates[0]
    assert codex.backend == "codex-cli"
    assert codex.role == "WORKER_CORE"
    assert codex.model == "openai/gpt-5.4"
    assert codex.reasoning_effort == "medium"
    assert codex.runs == 2
    assert codex.reported_runs == 2
    assert codex.unknown_runs == 0
    assert codex.input_tokens == 125
    assert codex.output_tokens == 55
    assert codex.total_tokens == 180
    assert codex.estimated_cost_usd == "0.004"

    openclaw = aggregates[1]
    assert openclaw.backend == "openclaw"
    assert openclaw.role == "REVIEWER_LIGHT"
    assert openclaw.status == "unknown"
    assert openclaw.runs == 1
    assert openclaw.reported_runs == 0
    assert openclaw.unknown_runs == 1
    assert openclaw.total_tokens is None


def test_token_usage_aggregate_output_surfaces_unknown_fallbacks() -> None:
    aggregates = aggregate_token_usage_by_role(
        [
            summarize_token_usage(
                backend="codex-cli",
                role="ARTIFACT_SUMMARIZER",
                model="openai/gpt-5.4-mini",
                reasoning_effort="minimal",
                output_text="no usage metadata",
            )
        ]
    )

    output = format_token_usage_aggregates(aggregates)

    assert "## Token usage aggregates" in output
    assert "Backend: codex-cli" in output
    assert "Role: ARTIFACT_SUMMARIZER" in output
    assert "Model: openai/gpt-5.4-mini" in output
    assert "Reasoning: minimal" in output
    assert "Reported runs: 0" in output
    assert "Unknown runs: 1" in output
    assert "Total tokens: unknown" in output
