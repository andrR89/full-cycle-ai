"""
Agent 3 - Frontend Specialist
Uses Claude Sonnet to generate React/MUI frontend code changes.
STRICT: Never generates backend code.
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


class FrontendOutput(BaseModel):
    files: List[FileChange] = Field(
        description="List of frontend files to create or update.",
        min_length=1,
    )
    package_delta: List[str] = Field(
        default_factory=list,
        description="List of npm packages to add (format: 'package@version').",
    )
    summary: str = Field(description="Short summary of the frontend implementation.")
    test_instructions: str = Field(description="How to test the frontend changes.")
    accessibility_notes: str = Field(
        default="",
        description="Notes on accessibility considerations implemented.",
    )

    @field_validator("files")
    @classmethod
    def validate_no_backend_files(cls, v: List[FileChange]) -> List[FileChange]:
        for f in v:
            if f.path.startswith("backend/"):
                raise ValueError(
                    f"Frontend agent tried to generate a backend file: {f.path}. "
                    "This is not allowed."
                )
        return v


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

FRONTEND_SYSTEM_PROMPT = """You are a senior frontend engineer specializing in React, TypeScript, and Material-UI (MUI).

CRITICAL RULES — NEVER VIOLATE:
1. You ONLY generate frontend code: React components, pages, hooks, styles, tests.
2. You NEVER generate backend code (Express routes, Prisma, middleware, database files).
3. ALL file paths MUST start with "frontend/" (e.g., frontend/src/components/UserTable.tsx, frontend/src/pages/UsersPage.tsx).
4. Use TypeScript with proper type definitions.
5. Use Material-UI (MUI) components for UI elements.
6. Follow React best practices: functional components, hooks, proper state management.
7. Include accessibility attributes (aria-label, role, etc.).
8. Generate React Testing Library unit tests for components.
9. Handle loading states, error states, and empty states.
10. Follow existing code conventions shown in the codebase context.
11. Keep file contents concise — avoid verbose comments, prefer short variable names in tests. Every token counts.

You respond ONLY with a valid JSON object — no markdown, no explanation.
"""

FRONTEND_USER_PROMPT_TEMPLATE = """## Issue to Solve
**Title:** {title}
**Body:** {body}

## Acceptance Criteria
{criteria}

## Current Codebase Context
{codebase_context}

## Reviewer Feedback (if retry)
{reviewer_feedback}

## Instructions
Generate a complete frontend implementation for this issue. Return a JSON object with:
- files: array of {{path, content, description}} — complete frontend files
- package_delta: npm packages to add (e.g., ["@mui/x-date-pickers@7.0.0"])
- summary: brief summary of the implementation
- test_instructions: how to test the changes
- accessibility_notes: notes on accessibility implementations

All file paths must start with frontend/ (e.g., frontend/src/components/, frontend/src/pages/, frontend/src/hooks/, frontend/src/types/, frontend/package.json).
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
) -> FrontendOutput:
    """Call Claude Sonnet and parse output as FrontendOutput."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    user_message = FRONTEND_USER_PROMPT_TEMPLATE.format(
        title=sanitize_prompt_input(title, "issue_title"),
        body=sanitize_prompt_input(body, "issue_body"),
        criteria="\n".join(f"- {c}" for c in criteria),
        codebase_context=codebase_context or "No codebase context available.",
        reviewer_feedback=sanitize_prompt_input(reviewer_feedback, "reviewer_feedback") if reviewer_feedback else "N/A — first attempt.",
    )

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=FRONTEND_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = message.content[0].text.strip()
    logger.debug("Frontend raw response length: %d chars, stop_reason: %s",
                 len(raw), message.stop_reason)

    if message.stop_reason == "max_tokens":
        logger.warning("Frontend response was truncated — raising to trigger retry")
        raise ValueError("Response truncated (max_tokens reached). Retry will request a shorter output.")

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        start = 1
        end = len(lines)
        for i in range(len(lines) - 1, 0, -1):
            if lines[i].strip() == "```":
                end = i
                break
        raw = "\n".join(lines[start:end])

    data = json.loads(raw)
    return FrontendOutput(**data)


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------

def run(state: AgentState) -> AgentState:
    """
    Agent 3: Generate frontend implementation.
    Only runs when 'frontend' is in issue_layers.
    """
    if "frontend" not in state.get("issue_layers", []):
        logger.info("Frontend agent skipped — 'frontend' not in layers.")
        return {"frontend_output": None}

    logger.info(
        "Frontend agent starting for issue #%s",
        state.get("issue_number"),
    )

    reviewer_feedback = None
    if state.get("retry_count", 0) > 0:
        reviews = state.get("layer_reviews", {})
        frontend_review = reviews.get("frontend", "")
        reviewer_feedback = state.get("reviewer_feedback", "")
        if frontend_review and "REJECTED" in frontend_review:
            reviewer_feedback = f"Frontend review: {frontend_review}\n\nOverall feedback: {reviewer_feedback}"

    try:
        output = _call_claude_sonnet(
            title=state["issue_title"],
            body=state["issue_body"],
            criteria=state.get("acceptance_criteria", []),
            codebase_context=state.get("codebase_context", ""),
            reviewer_feedback=reviewer_feedback,
        )
    except Exception as exc:
        logger.error("Frontend agent failed after retries: %s", exc)
        return {
            "frontend_output": {
                "error": str(exc),
                "files": [],
                "package_delta": [],
                "summary": f"Frontend agent failed: {exc}",
                "test_instructions": "N/A",
                "accessibility_notes": "",
            },
        }

    logger.info(
        "Frontend agent produced %d files: %s",
        len(output.files),
        [f.path for f in output.files],
    )

    return {"frontend_output": output.model_dump()}
