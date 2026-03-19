"""
Main entrypoint for the AI Issue Solver pipeline.
Called by GitHub Actions with issue data from environment variables.

Usage:
    python src/main.py

Required environment variables:
    ANTHROPIC_API_KEY   - Claude Sonnet API key
    GEMINI_API_KEY      - Google Gemini API key
    GH_PAT              - GitHub Personal Access Token
    GITHUB_REPO         - Repository in 'owner/repo' format
    ISSUE_NUMBER        - GitHub issue number
    ISSUE_TITLE         - GitHub issue title
    ISSUE_BODY          - GitHub issue body
    ISSUE_URL           - GitHub issue HTML URL
"""

import os
import sys
import logging
import json

from dotenv import load_dotenv

# Load .env file if present (local development)
load_dotenv()

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

REQUIRED_ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GH_PAT",
    "GITHUB_REPO",
    "ISSUE_NUMBER",
    "ISSUE_TITLE",
    "ISSUE_BODY",
    "ISSUE_URL",
]


def validate_environment() -> None:
    """Ensure all required environment variables are set."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        logger.error("Missing required environment variables: %s", missing)
        sys.exit(1)


def build_initial_state() -> dict:
    """Build initial AgentState from environment variables."""
    issue_number_raw = os.environ.get("ISSUE_NUMBER", "0")
    try:
        issue_number = int(issue_number_raw)
    except ValueError:
        logger.error("ISSUE_NUMBER must be an integer, got: %s", issue_number_raw)
        sys.exit(1)

    return {
        "issue_number": issue_number,
        "issue_title": os.environ.get("ISSUE_TITLE", ""),
        "issue_body": os.environ.get("ISSUE_BODY", ""),
        "issue_url": os.environ.get("ISSUE_URL", ""),
        "repo_name": os.environ.get("GITHUB_REPO", ""),
        # Reader will populate these
        "issue_layers": [],
        "acceptance_criteria": [],
        "priority": "medium",
        # Agent outputs (populated by respective agents)
        "backend_output": None,
        "frontend_output": None,
        # Reviewer outputs
        "layer_reviews": {},
        "global_status": "pending",
        "reviewer_feedback": None,
        # Retry control
        "retry_count": 0,
        # Deployer outputs
        "branch_name": None,
        "pr_url": None,
        # Codebase context
        "codebase_context": None,
    }


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the full AI Issue Solver pipeline."""
    logger.info("=" * 60)
    logger.info("AI Issue Solver Pipeline Starting")
    logger.info("=" * 60)

    validate_environment()

    initial_state = build_initial_state()

    logger.info(
        "Processing issue #%d: %s",
        initial_state["issue_number"],
        initial_state["issue_title"],
    )
    logger.info("Repository: %s", initial_state["repo_name"])

    # Import here to avoid module-level import issues during env validation
    from src.graph import graph

    try:
        # Run the LangGraph pipeline
        final_state = graph.invoke(
            initial_state,
            config={
                "recursion_limit": 50,  # Prevent infinite loops
            },
        )

        logger.info("=" * 60)
        logger.info("Pipeline completed successfully.")
        logger.info("Final state summary:")
        logger.info("  Issue #%d", final_state.get("issue_number"))
        logger.info("  Layers: %s", final_state.get("issue_layers"))
        logger.info("  Global status: %s", final_state.get("global_status"))
        logger.info("  Retry count: %d", final_state.get("retry_count", 0))
        logger.info("  PR URL: %s", final_state.get("pr_url") or "N/A (rejected or no files)")
        logger.info("  Branch: %s", final_state.get("branch_name") or "N/A")
        logger.info("=" * 60)

        # Write summary to GitHub Actions step summary if available
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            _write_github_summary(final_state, summary_path)

        # Exit with appropriate code
        if final_state.get("global_status") == "rejected":
            logger.info("Pipeline completed: issue was rejected and closed.")
            sys.exit(0)  # Not a pipeline error — expected outcome
        elif final_state.get("pr_url"):
            logger.info("Pipeline completed: PR created at %s", final_state["pr_url"])
            sys.exit(0)
        else:
            logger.warning("Pipeline completed but no PR was created.")
            sys.exit(0)

    except Exception as exc:
        logger.exception("Pipeline failed with unexpected error: %s", exc)
        sys.exit(1)


def _write_github_summary(state: dict, summary_path: str) -> None:
    """Write a Markdown summary to GitHub Actions step summary."""
    try:
        global_status = state.get("global_status", "unknown")
        pr_url = state.get("pr_url")
        layers = state.get("issue_layers", [])
        retry_count = state.get("retry_count", 0)
        layer_reviews = state.get("layer_reviews", {})

        status_emoji = "✅" if global_status == "approved" else "❌"

        lines = [
            f"## AI Issue Solver — Issue #{state.get('issue_number')}",
            f"",
            f"**Status:** {status_emoji} {global_status.upper()}",
            f"**Layers processed:** {', '.join(layers) or 'None'}",
            f"**Retries:** {retry_count}",
            f"",
        ]

        if pr_url:
            lines.extend([
                f"**PR Created:** [{pr_url}]({pr_url})",
                f"",
            ])

        if layer_reviews:
            lines.append("### Layer Reviews")
            for layer, result in layer_reviews.items():
                emoji = "✅" if result == "APPROVED" else "❌"
                lines.append(f"- {emoji} **{layer.capitalize()}**: {result}")
            lines.append("")

        feedback = state.get("reviewer_feedback")
        if feedback and global_status == "rejected":
            lines.extend([
                "### Rejection Reason",
                feedback,
                "",
            ])

        with open(summary_path, "w") as f:
            f.write("\n".join(lines))

        logger.info("GitHub Actions summary written to %s", summary_path)
    except Exception as exc:
        logger.warning("Could not write GitHub summary: %s", exc)


if __name__ == "__main__":
    main()
