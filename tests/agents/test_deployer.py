import pytest
from unittest.mock import patch, MagicMock, call
from github import GithubException
from src.agents.deployer import run, close_as_rejected, _collect_files


def make_state(**kwargs):
    base = {
        "issue_number": 42,
        "issue_title": "Add user pagination",
        "issue_body": "Need pagination on users list",
        "issue_url": "https://github.com/test/repo/issues/42",
        "repo_name": "test/repo",
        "issue_layers": ["backend"],
        "acceptance_criteria": ["Pagination works"],
        "backend_output": {
            "files": [
                {"path": "src/routes/users.js", "content": "const x = 1;", "description": "Add route"},
            ],
            "package_delta": [],
            "prisma_changes": None,
            "summary": "Added pagination endpoint",
            "test_instructions": "Run jest",
        },
        "frontend_output": None,
        "layer_reviews": {"backend": "APPROVED"},
        "global_status": "approved",
        "reviewer_feedback": None,
        "retry_count": 0,
        "branch_name": None,
        "pr_url": None,
        "codebase_context": None,
    }
    base.update(kwargs)
    return base


class TestCollectFiles:
    def test_collects_backend_files(self):
        state = make_state()
        files = _collect_files(state)
        assert len(files) == 1
        assert files[0]["path"] == "src/routes/users.js"

    def test_collects_both_layers(self):
        state = make_state(
            frontend_output={
                "files": [{"path": "src/components/UserTable.jsx", "content": "...", "description": ""}],
                "package_delta": [],
                "summary": "",
                "test_instructions": "",
                "accessibility_notes": "",
            }
        )
        files = _collect_files(state)
        assert len(files) == 2

    def test_ignores_error_outputs(self):
        state = make_state(
            backend_output={"error": "Agent failed", "files": []}
        )
        files = _collect_files(state)
        assert len(files) == 0

    def test_empty_state(self):
        state = make_state(backend_output=None, frontend_output=None)
        files = _collect_files(state)
        assert files == []


class TestDeployerRun:
    @patch("src.agents.deployer._wait_for_ci", return_value=True)
    @patch("src.agents.deployer._get_repo")
    def test_creates_branch_and_pr(self, mock_get_repo, mock_ci):
        mock_g = MagicMock()
        mock_repo = MagicMock()
        mock_get_repo.return_value = (mock_g, mock_repo)

        mock_branch = MagicMock()
        mock_branch.commit.sha = "abc123"
        mock_repo.get_branch.return_value = mock_branch
        # Simulate file not found (404) so _commit_files creates rather than updates
        not_found = GithubException(404, {"message": "Not Found"}, None)
        mock_repo.get_contents.side_effect = not_found

        mock_pr = MagicMock()
        mock_pr.number = 10
        mock_pr.html_url = "https://github.com/test/repo/pull/10"
        mock_repo.create_pull.return_value = mock_pr

        state = make_state()
        result = run(state)

        assert mock_repo.create_git_ref.called
        assert mock_repo.create_pull.called
        assert result["pr_url"] == "https://github.com/test/repo/pull/10"

    @patch("src.agents.deployer._get_repo")
    def test_aborts_when_no_files(self, mock_get_repo):
        mock_g = MagicMock()
        mock_repo = MagicMock()
        mock_get_repo.return_value = (mock_g, mock_repo)

        state = make_state(backend_output=None, frontend_output=None)
        result = run(state)
        assert result["pr_url"] is None
        assert result["branch_name"] is None

    @patch("src.agents.deployer._wait_for_ci", return_value=True)
    @patch("src.agents.deployer._get_repo")
    def test_rejects_path_traversal_files(self, mock_get_repo, mock_ci):
        """Files with traversal paths must be rejected by guardrail."""
        mock_g = MagicMock()
        mock_repo = MagicMock()
        mock_get_repo.return_value = (mock_g, mock_repo)

        state = make_state(
            backend_output={
                "files": [
                    {"path": "../../.env", "content": "SECRET=123", "description": "evil"},
                ],
                "package_delta": [],
                "prisma_changes": None,
                "summary": "",
                "test_instructions": "",
            }
        )
        result = run(state)
        # All files rejected → no PR
        assert result["pr_url"] is None


class TestCloseAsRejected:
    @patch("src.agents.deployer._get_repo")
    def test_closes_issue_with_comment(self, mock_get_repo):
        mock_g = MagicMock()
        mock_repo = MagicMock()
        mock_get_repo.return_value = (mock_g, mock_repo)

        mock_issue = MagicMock()
        mock_repo.get_issue.return_value = mock_issue

        state = make_state(
            global_status="rejected",
            reviewer_feedback="Tests missing",
            layer_reviews={"backend": "REJECTED: no tests"},
            retry_count=2,
        )
        result = close_as_rejected(state)

        mock_issue.create_comment.assert_called_once()
        comment_text = mock_issue.create_comment.call_args[0][0]
        assert "rejected" in comment_text.lower() or "Rejected" in comment_text
        mock_issue.edit.assert_called_once_with(state="closed")
        assert result["pr_url"] is None
