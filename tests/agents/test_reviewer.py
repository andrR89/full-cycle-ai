import pytest
from unittest.mock import patch, MagicMock
from src.agents.reviewer import run, ReviewerOutput


def make_state(**kwargs):
    base = {
        "issue_number": 1,
        "issue_title": "Add pagination",
        "issue_body": "Need pagination",
        "issue_url": "https://github.com/test/repo/issues/1",
        "repo_name": "test/repo",
        "issue_layers": ["backend"],
        "acceptance_criteria": ["Paginated results", "Total count", "Default size 20"],
        "backend_output": {
            "files": [{"path": "src/routes/users.js", "content": "...", "description": ""}],
            "package_delta": [],
            "prisma_changes": None,
            "summary": "Added pagination",
            "test_instructions": "jest",
        },
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


class TestReviewerRun:
    @patch("src.agents.reviewer._call_gemini_pro")
    def test_approved_result(self, mock_llm):
        mock_llm.return_value = ReviewerOutput(
            layer_reviews={"backend": "APPROVED"},
            global_status="approved",
            reviewer_feedback="All good",
            checklist={},
        )
        state = make_state()
        result = run(state)
        assert result["global_status"] == "approved"
        assert result["retry_count"] == 0  # no increment on approval

    @patch("src.agents.reviewer._call_gemini_pro")
    def test_rejected_increments_retry(self, mock_llm):
        mock_llm.return_value = ReviewerOutput(
            layer_reviews={"backend": "REJECTED: missing tests"},
            global_status="rejected",
            reviewer_feedback="Tests missing",
            checklist={},
        )
        state = make_state(retry_count=0)
        result = run(state)
        assert result["global_status"] == "rejected"
        assert result["retry_count"] == 1

    def test_auto_rejects_when_no_outputs(self):
        state = make_state(backend_output=None, frontend_output=None)
        result = run(state)
        assert result["global_status"] == "rejected"

    @patch("src.agents.reviewer._call_gemini_pro", side_effect=Exception("API error"))
    def test_rejects_on_llm_failure(self, mock_llm):
        state = make_state()
        result = run(state)
        assert result["global_status"] == "rejected"


class TestReviewerOutputValidation:
    def test_valid_approved(self):
        out = ReviewerOutput(
            layer_reviews={"backend": "APPROVED"},
            global_status="approved",
            reviewer_feedback="Good",
            checklist={},
        )
        assert out.global_status == "approved"

    def test_valid_rejected(self):
        out = ReviewerOutput(
            layer_reviews={"backend": "REJECTED: missing tests"},
            global_status="rejected",
            reviewer_feedback="Add tests",
            checklist={},
        )
        assert out.global_status == "rejected"

    def test_invalid_global_status(self):
        with pytest.raises(Exception):
            ReviewerOutput(
                layer_reviews={"backend": "APPROVED"},
                global_status="pending",
                reviewer_feedback="",
                checklist={},
            )

    def test_invalid_layer_review_format(self):
        with pytest.raises(Exception):
            ReviewerOutput(
                layer_reviews={"backend": "MAYBE"},
                global_status="approved",
                reviewer_feedback="",
                checklist={},
            )
