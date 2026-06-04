from signposter.token_usage import format_token_usage_accounting, summarize_token_usage


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
    assert accounting.source == "backend did not report token usage"

    output = format_token_usage_accounting(accounting)
    assert "Status: unknown" in output
    assert "Input tokens: unknown" in output
    assert "Estimated cost USD: unknown" in output


def test_token_usage_extracts_common_usage_fields() -> None:
    accounting = summarize_token_usage(
        role="codex_worker_core",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
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
