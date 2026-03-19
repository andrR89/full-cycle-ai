"""
Agent 2 - Backend Specialist
Uses Claude Sonnet to generate Node/Express/Prisma backend code changes.
STRICT: Never generates frontend code.
"""

import os
import json
import logging
from typing import Dict, List, Optional

import anthropic
from pydantic import BaseModel, Field, field_validator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.state import AgentState
from src.guardrails import sanitize_prompt_input

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic output model
# ---------------------------------------------------------------------------

class FileChange(BaseModel):
    path: str = Field(description="File path relative to repository root.")
    content: str = Field(description="Complete file content (not a diff).")
    description: str = Field(description="Short description of what was changed and why.")


class BackendOutput(BaseModel):
    files: List[FileChange] = Field(
        description="List of backend files to create or update.",
        min_length=1,
    )
    package_delta: List[str] = Field(
        default_factory=list,
        description="List of npm packages to add (format: 'package@version').",
    )
    prisma_changes: Optional[str] = Field(
        default=None,
        description="Description of Prisma schema changes, if any.",
    )
    summary: str = Field(description="Short summary of the backend implementation.")
    test_instructions: str = Field(description="How to test the backend changes.")

    @field_validator("files")
    @classmethod
    def validate_no_frontend_files(cls, v: List[FileChange]) -> List[FileChange]:
        frontend_patterns = [
            ".tsx", ".jsx", "components/", "pages/", "hooks/",
            "src/app/", "public/", ".css", ".scss", "index.html",
        ]
        for f in v:
            for pattern in frontend_patterns:
                if pattern in f.path.lower():
                    raise ValueError(
                        f"Backend agent tried to generate a frontend file: {f.path}. "
                        "This is not allowed."
                    )
        return v


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

BACKEND_SYSTEM_PROMPT = """You are a senior backend engineer specializing in Node.js, Express, and Prisma.

CRITICAL RULES — NEVER VIOLATE:
1. You ONLY generate backend code: Express routes, Prisma models, middleware, utilities, tests.
2. You NEVER generate frontend code (React, JSX, TSX, CSS, HTML, components, pages, hooks).
3. All generated files must be backend-only.
4. Follow RESTful API design principles.
5. Include input validation, error handling, and proper HTTP status codes.
6. Generate Jest/Supertest unit tests for routes.
7. Use async/await with try/catch error handling.
8. Follow existing code conventions shown in the codebase context.

You respond ONLY with a valid JSON object — no markdown, no explanation.
"""

BACKEND_USER_PROMPT_TEMPLATE = """## Issue to Solve
**Title:** {title}
**Body:** {body}

## Acceptance Criteria
{criteria}

## Current Codebase Context
{codebase_context}

## Reviewer Feedback (if retry)
{reviewer_feedback}

## Instructions
Generate backend implementation for this issue. Return a JSON object with:
- files: array of {{path, content, description}} — complete backend files
- package_delta: npm packages to add (e.g., ["express-validator@7.0.1"])
- prisma_changes: description of schema changes (or null)
- summary: brief summary of the implementation
- test_instructions: how to test the changes

All file paths must be backend-only (src/routes/, src/middleware/, src/utils/, prisma/, tests/).
"""


# ---------------------------------------------------------------------------
# LLM call with retry
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ValueError, json.JSONDecodeError, anthropic.APIError)),
    reraise=True,
)
def _call_claude_sonnet(
    title: str,
    body: str,
    criteria: List[str],
    codebase_context: str,
    reviewer_feedback: Optional[str],
) -> BackendOutput:
    """Call Claude Sonnet and parse output as BackendOutput."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    user_message = BACKEND_USER_PROMPT_TEMPLATE.format(
        title=sanitize_prompt_input(title, "issue_title"),
        body=sanitize_prompt_input(body, "issue_body"),
        criteria="\n".join(f"- {c}" for c in criteria),
        codebase_context=codebase_context or "No codebase context available.",
        reviewer_feedback=sanitize_prompt_input(reviewer_feedback, "reviewer_feedback") if reviewer_feedback else "N/A — first attempt.",
    )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system=BACKEND_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = message.content[0].text.strip()
    logger.debug("Backend raw response length: %d chars", len(raw))

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        # Find first and last fence
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        raw = "\n".join(lines[start:end])

    data = json.loads(raw)
    return BackendOutput(**data)


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------

def run(state: AgentState) -> AgentState:
    """
    Agent 2: Generate backend implementation.
    Only runs when 'backend' is in issue_layers.
    """
    if "backend" not in state.get("issue_layers", []):
        logger.info("Backend agent skipped — 'backend' not in layers.")
        return {**state, "backend_output": None}

    logger.info(
        "Backend agent starting for issue #%s",
        state.get("issue_number"),
    )

    reviewer_feedback = None
    if state.get("retry_count", 0) > 0:
        reviews = state.get("layer_reviews", {})
        backend_review = reviews.get("backend", "")
        reviewer_feedback = state.get("reviewer_feedback", "")
        if backend_review and "REJECTED" in backend_review:
            reviewer_feedback = f"Backend review: {backend_review}\n\nOverall feedback: {reviewer_feedback}"

    try:
        output = _call_claude_sonnet(
            title=state["issue_title"],
            body=state["issue_body"],
            criteria=state.get("acceptance_criteria", []),
            codebase_context=state.get("codebase_context", ""),
            reviewer_feedback=reviewer_feedback,
        )
    except Exception as exc:
        logger.error("Backend agent failed after retries: %s", exc)
        # Return a minimal error output so the graph can continue
        return {
            **state,
            "backend_output": {
                "error": str(exc),
                "files": [],
                "package_delta": [],
                "prisma_changes": None,
                "summary": f"Backend agent failed: {exc}",
                "test_instructions": "N/A",
            },
        }

    logger.info(
        "Backend agent produced %d files: %s",
        len(output.files),
        [f.path for f in output.files],
    )

    return {
        **state,
        "backend_output": output.model_dump(),
    }
