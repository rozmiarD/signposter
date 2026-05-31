"""Unit tests for dependency parsing and computed blocked status (HARDENING-005)."""

from signposter.dependencies import parse_depends_on


def test_parse_depends_on_single():
    body = "Some text\nDepends-On: #3\nMore text"
    assert parse_depends_on(body) == [3]


def test_parse_depends_on_comma_separated():
    body = "Depends-On: #3, #7, #12"
    assert parse_depends_on(body) == [3, 7, 12]


def test_parse_depends_on_multiple_lines():
    body = """Depends-On: #3
Depends-On: #7
Depends-On: #12"""
    assert parse_depends_on(body) == [3, 7, 12]


def test_parse_depends_on_duplicates_deduped():
    body = "Depends-On: #3, #3, #7"
    assert parse_depends_on(body) == [3, 7]


def test_parse_depends_on_mixed():
    body = "Depends-On: #10\nAlso Depends-On: #5, #10"
    assert parse_depends_on(body) == [5, 10]


def test_parse_depends_on_none():
    assert parse_depends_on(None) == []
    assert parse_depends_on("") == []
    assert parse_depends_on("No dependencies here") == []


def test_parse_depends_on_case_insensitive():
    body = "depends-on: #42"
    assert parse_depends_on(body) == [42]


# --- Integration-style tests with mocks (no real network) ---


def test_is_dependency_blocked_all_done(monkeypatch):
    from signposter.dependencies import is_dependency_blocked

    def fake_state(repo, num):
        return "done"

    monkeypatch.setattr("signposter.dependencies.fetch_issue_state_label", fake_state)

    blocked, reason = is_dependency_blocked("test/repo", "Depends-On: #3, #4")
    assert blocked is False
    assert "all dependencies complete" in reason


def test_is_dependency_blocked_all_merged(monkeypatch):
    from signposter.dependencies import is_dependency_blocked

    def fake_state(repo, num):
        return "merged"

    monkeypatch.setattr("signposter.dependencies.fetch_issue_state_label", fake_state)

    blocked, reason = is_dependency_blocked("test/repo", "Depends-On: #3, #4")
    assert blocked is False
    assert "all dependencies complete" in reason


def test_is_dependency_blocked_active_dep(monkeypatch):
    from signposter.dependencies import is_dependency_blocked

    def fake_state(repo, num):
        return "active" if num == 7 else "done"

    monkeypatch.setattr("signposter.dependencies.fetch_issue_state_label", fake_state)

    blocked, reason = is_dependency_blocked("test/repo", "Depends-On: #3, #7")
    assert blocked is True
    assert "#7 → state:active" in reason


def test_is_dependency_blocked_missing_dep(monkeypatch):
    from signposter.dependencies import is_dependency_blocked

    def fake_state(repo, num):
        return None

    monkeypatch.setattr("signposter.dependencies.fetch_issue_state_label", fake_state)

    blocked, reason = is_dependency_blocked("test/repo", "Depends-On: #99")
    assert blocked is True
    assert "missing/unknown" in reason
