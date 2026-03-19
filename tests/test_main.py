import pytest
import os
from unittest.mock import patch, MagicMock
from src.main import build_initial_state, validate_environment


class TestBuildInitialState:
    def test_builds_state_from_env(self, monkeypatch):
        monkeypatch.setenv("ISSUE_NUMBER", "42")
        monkeypatch.setenv("ISSUE_TITLE", "Fix bug")
        monkeypatch.setenv("ISSUE_BODY", "Something is broken")
        monkeypatch.setenv("ISSUE_URL", "https://github.com/test/repo/issues/42")
        monkeypatch.setenv("GITHUB_REPO", "test/repo")

        state = build_initial_state()
        assert state["issue_number"] == 42
        assert state["issue_title"] == "Fix bug"
        assert state["repo_name"] == "test/repo"
        assert state["retry_count"] == 0

    def test_exits_on_invalid_issue_number(self, monkeypatch):
        monkeypatch.setenv("ISSUE_NUMBER", "not-a-number")
        monkeypatch.setenv("ISSUE_TITLE", "title")
        monkeypatch.setenv("ISSUE_BODY", "body")
        monkeypatch.setenv("ISSUE_URL", "url")
        monkeypatch.setenv("GITHUB_REPO", "test/repo")

        with pytest.raises(SystemExit):
            build_initial_state()


class TestValidateEnvironment:
    def test_passes_when_all_vars_set(self, monkeypatch):
        required = [
            "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GH_PAT",
            "GITHUB_REPO", "ISSUE_NUMBER", "ISSUE_TITLE", "ISSUE_BODY", "ISSUE_URL"
        ]
        for var in required:
            monkeypatch.setenv(var, "test-value")
        # Should not raise
        validate_environment()

    def test_exits_on_missing_vars(self, monkeypatch):
        # Remove all required vars
        required = [
            "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GH_PAT",
            "GITHUB_REPO", "ISSUE_NUMBER", "ISSUE_TITLE", "ISSUE_BODY", "ISSUE_URL"
        ]
        for var in required:
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(SystemExit):
            validate_environment()
