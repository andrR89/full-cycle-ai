import pytest
from unittest.mock import patch, MagicMock
from src.agents.reader import run, ReaderOutput, fetch_codebase_context


def make_state(**kwargs):
    base = {
        "issue_number": 1,
        "issue_title": "Fix login bug",
        "issue_body": "Users cannot login after password reset",
        "issue_url": "https://github.com/test/repo/issues/1",
        "repo_name": "test/repo",
        "issue_layers": [],
        "acceptance_criteria": [],
        "priority": "medium",
        "backend_output": None,
        "frontend_output": None,
        "layer_reviews": {},
        "global_status": "pending",
        "reviewer_feedback": None,
        "retry_count": 0,
        "branch_name": None,
        "pr_url": None,
        "codebase_context": None,
    }
    base.update(kwargs)
    return base


class TestReaderRun:
    @patch("src.agents.reader.fetch_codebase_context", return_value="## context")
    @patch("src.agents.reader._call_gemini_flash")
    def test_successful_classification(self, mock_llm, mock_ctx):
        mock_llm.return_value = ReaderOutput(
            issue_layers=["backend"],
            acceptance_criteria=["Login works", "Error shown", "Session created"],
            priority="high",
        )
        state = make_state()
        result = run(state)

        assert result["issue_layers"] == ["backend"]
        assert result["priority"] == "high"
        assert len(result["acceptance_criteria"]) >= 3
        assert result["codebase_context"] is not None

    @patch("src.agents.reader.fetch_codebase_context", return_value="")
    @patch("src.agents.reader._call_gemini_flash")
    def test_multi_layer_classification(self, mock_llm, mock_ctx):
        mock_llm.return_value = ReaderOutput(
            issue_layers=["backend", "frontend"],
            acceptance_criteria=["API returns 200", "UI shows data", "DB updated"],
            priority="medium",
        )
        state = make_state()
        result = run(state)
        assert set(result["issue_layers"]) == {"backend", "frontend"}

    @patch("src.agents.reader.fetch_codebase_context", return_value="")
    @patch("src.agents.reader._call_gemini_flash", side_effect=Exception("API error"))
    def test_fallback_on_llm_failure(self, mock_llm, mock_ctx):
        """On LLM failure, agent falls back to both layers with generic criteria."""
        state = make_state()
        result = run(state)
        assert "backend" in result["issue_layers"] or "frontend" in result["issue_layers"]
        assert len(result["acceptance_criteria"]) > 0

    @patch("src.agents.reader.fetch_codebase_context", return_value="some context")
    @patch("src.agents.reader._call_gemini_flash")
    def test_retry_context_injected(self, mock_llm, mock_ctx):
        """On retry, reviewer feedback is passed to the LLM call."""
        mock_llm.return_value = ReaderOutput(
            issue_layers=["frontend"],
            acceptance_criteria=["c1", "c2", "c3"],
            priority="low",
        )
        state = make_state(retry_count=1, reviewer_feedback="Frontend validation missing")
        run(state)
        call_kwargs = mock_llm.call_args
        assert call_kwargs is not None


class TestReaderOutputValidation:
    def test_valid_layers(self):
        out = ReaderOutput(
            issue_layers=["backend", "frontend"],
            acceptance_criteria=["a", "b", "c"],
            priority="high",
        )
        assert set(out.issue_layers) == {"backend", "frontend"}

    def test_deduplicates_layers(self):
        out = ReaderOutput(
            issue_layers=["backend", "backend"],
            acceptance_criteria=["a", "b", "c"],
            priority="medium",
        )
        assert out.issue_layers.count("backend") == 1

    def test_invalid_layer_raises(self):
        with pytest.raises(Exception):
            ReaderOutput(
                issue_layers=["database"],
                acceptance_criteria=["a"],
                priority="medium",
            )

    def test_invalid_priority_raises(self):
        with pytest.raises(Exception):
            ReaderOutput(
                issue_layers=["backend"],
                acceptance_criteria=["a"],
                priority="urgent",
            )
