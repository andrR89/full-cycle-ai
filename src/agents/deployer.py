"""
Agent 5 - Deployer
Pure PyGithub implementation — no LLM calls.
Handles:
  - run(): create branch → commit files → create PR
  - close_as_rejected(): comment on issue + close it
"""

import os
import base64
import logging
import time
from typing import Dict, List, Optional, Tuple

from github import Github, GithubException
from github.Repository import Repository
from github.GithubObject import NotSet

from src.state import AgentState
from src.guardrails import validate_and_filter_files, scan_generated_code

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _get_repo(repo_name: str) -> Tuple[Github, Repository]:
    """Return authenticated Github client and repository."""
    gh_pat = os.environ.get("GH_PAT")
    if not gh_pat:
        raise EnvironmentError("GH_PAT environment variable is not set.")
    g = Github(gh_pat)
    repo = g.get_repo(repo_name)
    return g, repo


def _collect_files(state: AgentState) -> List[Dict[str, str]]:
    """
    Collect all files to commit from agent outputs.
    Returns list of {"path": ..., "content": ...} dicts.
    """
    all_files: List[Dict[str, str]] = []

    backend_output = state.get("backend_output")
    if backend_output and not backend_output.get("error"):
        for f in backend_output.get("files", []):
            all_files.append({"path": f["path"], "content": f["content"]})

    frontend_output = state.get("frontend_output")
    if frontend_output and not frontend_output.get("error"):
        for f in frontend_output.get("files", []):
            all_files.append({"path": f["path"], "content": f["content"]})

    return all_files


def _create_branch(repo: Repository, branch_name: str, base_branch: str = "main") -> str:
    """Create a new branch from base_branch. Returns the branch ref sha."""
    try:
        base_ref = repo.get_branch(base_branch)
    except GithubException:
        # Try 'master' as fallback
        base_ref = repo.get_branch("master")
        base_branch = "master"

    sha = base_ref.commit.sha
    try:
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=sha)
        logger.info("Created branch '%s' from '%s' @ %s", branch_name, base_branch, sha[:7])
    except GithubException as exc:
        if exc.status == 422:  # branch already exists
            logger.warning("Branch '%s' already exists, continuing.", branch_name)
        else:
            raise
    return sha


def _commit_files(
    repo: Repository,
    branch_name: str,
    files: List[Dict[str, str]],
    commit_message: str,
) -> None:
    """Create or update files on the given branch."""
    for file_info in files:
        path = file_info["path"]
        content = file_info["content"]

        try:
            # Check if file exists
            existing = repo.get_contents(path, ref=branch_name)
            repo.update_file(
                path=path,
                message=commit_message,
                content=content,
                sha=existing.sha,
                branch=branch_name,
            )
            logger.info("Updated file: %s", path)
        except GithubException as exc:
            if exc.status == 404:
                # File does not exist, create it
                repo.create_file(
                    path=path,
                    message=commit_message,
                    content=content,
                    branch=branch_name,
                )
                logger.info("Created file: %s", path)
            else:
                logger.error("Failed to commit file %s: %s", path, exc)
                raise


def _build_pr_body(state: AgentState) -> str:
    """Build a rich PR description from the agent outputs."""
    issue_url = state.get("issue_url", "")
    issue_number = state.get("issue_number", "")
    acceptance_criteria = state.get("acceptance_criteria", [])
    backend_output = state.get("backend_output") or {}
    frontend_output = state.get("frontend_output") or {}

    lines = [
        f"## Automated PR — Issue #{issue_number}",
        f"",
        f"Closes #{issue_number}",
        f"Issue: {issue_url}",
        f"",
        "## Summary",
    ]

    if backend_output and not backend_output.get("error"):
        lines.append(f"**Backend:** {backend_output.get('summary', 'N/A')}")
        if backend_output.get("prisma_changes"):
            lines.append(f"**Prisma changes:** {backend_output['prisma_changes']}")
        if backend_output.get("package_delta"):
            lines.append(f"**New backend packages:** {', '.join(backend_output['package_delta'])}")

    if frontend_output and not frontend_output.get("error"):
        lines.append(f"**Frontend:** {frontend_output.get('summary', 'N/A')}")
        if frontend_output.get("package_delta"):
            lines.append(f"**New frontend packages:** {', '.join(frontend_output['package_delta'])}")
        if frontend_output.get("accessibility_notes"):
            lines.append(f"**Accessibility:** {frontend_output['accessibility_notes']}")

    if acceptance_criteria:
        lines.append("")
        lines.append("## Acceptance Criteria")
        for criterion in acceptance_criteria:
            lines.append(f"- [ ] {criterion}")

    layers = state.get("issue_layers", [])
    if layers:
        lines.append("")
        lines.append("## Changed Layers")
        for layer in layers:
            lines.append(f"- {layer.capitalize()}")

    lines.extend([
        "",
        "## Testing",
    ])
    if backend_output.get("test_instructions"):
        lines.append(f"**Backend:** {backend_output['test_instructions']}")
    if frontend_output.get("test_instructions"):
        lines.append(f"**Frontend:** {frontend_output['test_instructions']}")

    lines.extend([
        "",
        "---",
        "_Generated by AI Issue Solver pipeline_",
    ])

    return "\n".join(lines)


def _wait_for_ci(repo: Repository, branch_name: str, pr_number: int, timeout: int = 300) -> bool:
    """
    Poll GitHub to check if CI checks on the PR branch have passed.
    Returns True if all required checks pass, False otherwise.
    timeout: max seconds to wait (default 5 minutes).
    """
    logger.info("Waiting for CI checks on branch '%s' (timeout=%ds)...", branch_name, timeout)
    start = time.time()
    poll_interval = 15  # seconds

    while time.time() - start < timeout:
        try:
            commit = repo.get_branch(branch_name).commit
            check_runs = commit.get_check_runs()
            runs = list(check_runs)

            if not runs:
                logger.debug("No CI checks found yet, waiting...")
                time.sleep(poll_interval)
                continue

            statuses = [(r.name, r.status, r.conclusion) for r in runs]
            logger.debug("CI check statuses: %s", statuses)

            # Check if all runs are completed
            all_completed = all(r.status == "completed" for r in runs)
            if not all_completed:
                pending = [r.name for r in runs if r.status != "completed"]
                logger.debug("Pending checks: %s", pending)
                time.sleep(poll_interval)
                continue

            # All completed — check conclusions
            failed = [r for r in runs if r.conclusion not in ("success", "skipped", "neutral")]
            if failed:
                logger.warning(
                    "CI checks FAILED: %s",
                    [(r.name, r.conclusion) for r in failed],
                )
                return False

            logger.info("All CI checks passed.")
            return True

        except GithubException as exc:
            logger.warning("Error polling CI checks: %s", exc)
            time.sleep(poll_interval)

    logger.warning("CI check timeout after %ds — treating as failed.", timeout)
    return False


# ---------------------------------------------------------------------------
# Main agent entry points
# ---------------------------------------------------------------------------

def run(state: AgentState) -> AgentState:
    """
    Agent 5: Deployer.
    Creates a branch, commits all generated files, creates a PR,
    and auto-merges if CI passes.
    """
    logger.info(
        "Deployer agent starting for issue #%s",
        state.get("issue_number"),
    )

    repo_name = state.get("repo_name", "")
    issue_number = state.get("issue_number", 0)

    if not repo_name:
        raise ValueError("repo_name is required for deployer.")

    _, repo = _get_repo(repo_name)

    # Build branch name
    title_slug = (
        state.get("issue_title", "issue")
        .lower()
        .replace(" ", "-")
        .replace("/", "-")
        [:50]
    )
    # Remove non-alphanumeric except hyphens
    import re
    title_slug = re.sub(r"[^a-z0-9\-]", "", title_slug).strip("-")
    branch_name = f"ai-solve/issue-{issue_number}-{title_slug}"

    # Collect files to commit
    files = _collect_files(state)
    if not files:
        logger.error("Deployer: no files to commit. Aborting PR creation.")
        return {
            **state,
            "branch_name": None,
            "pr_url": None,
        }

    # Guardrail: validate file paths (blocks traversal attacks)
    files, rejected_paths = validate_and_filter_files(files)
    if rejected_paths:
        logger.error("Deployer: %d file(s) rejected by path guardrail: %s", len(rejected_paths), rejected_paths)
    if not files:
        logger.error("Deployer: all files rejected by guardrail. Aborting.")
        return {**state, "branch_name": None, "pr_url": None, "ci_passed": None, "guardrail_rejected": rejected_paths}

    # Guardrail: scan for dangerous code patterns
    security_warnings = scan_generated_code(files)

    # Create branch
    _create_branch(repo, branch_name)

    # Commit files
    commit_message = f"feat: AI-generated solution for issue #{issue_number}\n\nAutomated implementation by AI Issue Solver pipeline."
    _commit_files(repo, branch_name, files, commit_message)

    # Create Pull Request
    pr_title = f"[AI] {state.get('issue_title', f'Issue #{issue_number}')}"
    pr_body = _build_pr_body(state)

    if rejected_paths:
        rejected_block = "\n".join(f"- `{p}`" for p in rejected_paths)
        pr_body += (
            f"\n\n## ⚠️ Files Skipped by Safety Guardrail\n"
            f"The following files were generated but **not committed** because they fall outside "
            f"the allowed directory structure. Manual intervention may be required:\n"
            f"{rejected_block}"
        )

    if security_warnings:
        warning_block = "\n".join(f"- {w}" for w in security_warnings)
        pr_body += (
            f"\n\n## ⚠️ Security Warnings (requires human review)\n"
            f"The following patterns were detected in generated code and need manual verification:\n"
            f"{warning_block}"
        )
        logger.warning("Deployer: PR created with %d security warning(s).", len(security_warnings))

    try:
        pr = repo.create_pull(
            title=pr_title,
            body=pr_body,
            head=branch_name,
            base="main",
            draft=False,
        )
        logger.info("Created PR #%d: %s", pr.number, pr.html_url)
    except GithubException as exc:
        if "base" in str(exc).lower() or exc.status == 422:
            # Try master as base
            pr = repo.create_pull(
                title=pr_title,
                body=pr_body,
                head=branch_name,
                base="master",
                draft=False,
            )
            logger.info("Created PR #%d (master base): %s", pr.number, pr.html_url)
        else:
            raise

    # Add labels to PR
    try:
        issue = repo.get_issue(issue_number)
        issue.create_comment(
            f"AI pipeline has created a PR for this issue: {pr.html_url}\n\n"
            "The PR will be auto-merged once CI checks pass."
        )
    except Exception as exc:
        logger.warning("Could not comment on issue: %s", exc)

    # Wait for CI and auto-merge if passed
    ci_passed = _wait_for_ci(repo, branch_name, pr.number, timeout=300)

    if ci_passed:
        try:
            pr.merge(
                commit_title=f"AI: Resolve issue #{issue_number}",
                commit_message=f"Automated merge after CI passed.\n\nCloses #{issue_number}",
                merge_method="squash",
            )
            logger.info("PR #%d merged successfully.", pr.number)
        except GithubException as exc:
            logger.error("Auto-merge failed: %s", exc)
            # PR stays open for manual merge
    else:
        logger.warning(
            "CI did not pass — PR #%d left open for manual review.",
            pr.number,
        )
        try:
            pr.create_review(
                body=(
                    "CI checks did not pass. This PR requires manual review before merging. "
                    "Please fix the failing checks and update the PR."
                ),
                event="COMMENT",
            )
        except Exception as exc:
            logger.warning("Could not add review comment: %s", exc)

    return {
        **state,
        "branch_name": branch_name,
        "pr_url": pr.html_url,
        "ci_passed": ci_passed,
        "guardrail_rejected": rejected_paths if rejected_paths else [],
    }


def close_as_rejected(state: AgentState) -> AgentState:
    """
    Agent 5 (rejection path): Comment on the issue explaining why it was rejected
    and close the issue without creating a PR.
    """
    logger.info(
        "Deployer close_as_rejected for issue #%s",
        state.get("issue_number"),
    )

    repo_name = state.get("repo_name", "")
    issue_number = state.get("issue_number", 0)

    if not repo_name or not issue_number:
        logger.error("Missing repo_name or issue_number — cannot close issue.")
        return state

    _, repo = _get_repo(repo_name)

    # Build rejection comment
    layer_reviews = state.get("layer_reviews", {})
    reviewer_feedback = state.get("reviewer_feedback", "No feedback provided.")
    retry_count = state.get("retry_count", 0)

    review_lines = []
    for layer, result in layer_reviews.items():
        emoji = "✅" if result == "APPROVED" else "❌"
        review_lines.append(f"- {emoji} **{layer.capitalize()}**: {result}")

    comment_body = (
        f"## AI Pipeline — Issue Closed (Auto-Rejected)\n\n"
        f"After {retry_count} attempt(s), the AI pipeline was unable to generate "
        f"an implementation that passes the code review.\n\n"
        f"### Review Results\n"
        + ("\n".join(review_lines) if review_lines else "No layer-specific reviews available.")
        + f"\n\n### Reviewer Feedback\n{reviewer_feedback}\n\n"
        f"### What to do next\n"
        f"1. Review the feedback above.\n"
        f"2. Update the issue with more specific requirements or acceptance criteria.\n"
        f"3. Re-open the issue and add the `ai-solve` label to trigger a new pipeline run.\n\n"
        f"---\n_Automated by AI Issue Solver pipeline_"
    )

    try:
        issue = repo.get_issue(issue_number)
        issue.create_comment(comment_body)
        issue.edit(state="closed")
        logger.info("Issue #%d closed with rejection comment.", issue_number)
    except GithubException as exc:
        logger.error("Failed to close issue #%d: %s", issue_number, exc)
        raise

    return {
        **state,
        "branch_name": None,
        "pr_url": None,
    }
