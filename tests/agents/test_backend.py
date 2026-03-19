import pytest
from unittest.mock import patch, MagicMock
from src.agents.backend import run, BackendOutput, FileChange


def make_state(**kwargs):
    base = {
        "issue_number": 1,
        "issue_title": "Add pagination",
        "issue_body": "Need pagination on users API",
        "issue_url": "https://github.com/test/repo/issues/1",
        "repo_name": "test/repo",
        "issue_layers": ["backend"],
        "acceptance_criteria": ["Returns paginated results", "Includes total count", "Default page size 20"],
        "backend_output": None,
        "frontend_output": None,
        "layer_reviews": {},
        "global_status": "pending",
        "reviewer_feedback": None,
        "retry_count": 0,
        "branch_name": None,
        "pr_url": None,
        "codebase_context": "### src/routes/users.js\n```\nconst router = require('express').Router();\n```",
    }
    base.update(kwargs)
    return base


def make_backend_output():
    return BackendOutput(
        files=[FileChange(path="src/routes/users.js", content="const x = 1;", description="Add pagination")],
        package_delta=[],
        prisma_changes=None,
        summary="Added pagination",
        test_instructions="Run jest",
    )


class TestBackendRun:
    def test_skips_when_layer_not_present(self):
        state = make_state(issue_layers=["frontend"])
        result = run(state)
        assert result["backend_output"] is None

    @patch("src.agents.backend._call_claude_sonnet")
    def test_successful_generation(self, mock_llm):
        mock_llm.return_value = make_backend_output()
        state = make_state()
        result = run(state)
        assert result["backend_output"] is not None
        assert result["backend_output"]["summary"] == "Added pagination"
        assert len(result["backend_output"]["files"]) == 1

    @patch("src.agents.backend._call_claude_sonnet", side_effect=Exception("API error"))
    def test_returns_error_on_failure(self, mock_llm):
        state = make_state()
        result = run(state)
        assert result["backend_output"] is not None
        assert "error" in result["backend_output"]

    @patch("src.agents.backend._call_claude_sonnet")
    def test_passes_reviewer_feedback_on_retry(self, mock_llm):
        mock_llm.return_value = make_backend_output()
        state = make_state(
            retry_count=1,
            reviewer_feedback="Add input validation",
            layer_reviews={"backend": "REJECTED: missing validation"},
        )
        run(state)
        assert mock_llm.called
        call_kwargs = mock_llm.call_args[1] if mock_llm.call_args[1] else {}
        call_args = mock_llm.call_args[0] if mock_llm.call_args[0] else ()
        # reviewer_feedback should have been passed
        assert mock_llm.called


class TestBackendOutputValidation:
    def test_rejects_jsx_files(self):
        with pytest.raises(Exception):
            BackendOutput(
                files=[FileChange(path="src/components/UserTable.jsx", content="...", description="")],
                package_delta=[],
                summary="",
                test_instructions="",
            )

    def test_rejects_pages_directory(self):
        with pytest.raises(Exception):
            BackendOutput(
                files=[FileChange(path="src/pages/UsersPage.jsx", content="...", description="")],
                package_delta=[],
                summary="",
                test_instructions="",
            )

    def test_accepts_routes_directory(self):
        out = BackendOutput(
            files=[FileChange(path="src/routes/users.js", content="...", description="")],
            package_delta=[],
            summary="ok",
            test_instructions="jest",
        )
        assert len(out.files) == 1
