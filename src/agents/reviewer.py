"""
Agent 4 - Cross-Layer Reviewer
Uses Gemini Pro to perform a thorough cross-layer code review.
Reviews backend and frontend outputs against acceptance criteria.
"""

import os
import json
import logging
from typing import Dict, List, Optional

import google.generativeai as genai
from pydantic import BaseModel, Field, field_validator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic output model
# ---------------------------------------------------------------------------

class ReviewerOutput(BaseModel):
    layer_reviews: Dict[str, str] = Field(
        description=(
            "Per-layer review results. Keys are layer names ('backend', 'frontend'). "
            "Values are 'APPROVED' or 'REJECTED: <specific reason>'."
        )
    )
    global_status: str = Field(
        description="Overall status: 'approved' if ALL reviewed layers pass, otherwise 'rejected'."
    )
    reviewer_feedback: str = Field(
        description=(
            "Consolidated actionable feedback for all agents. "
            "Be specific: list exact files, functions, or criteria that failed."
        )
    )
    checklist: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Per-layer checklist of items reviewed (key: layer, value: list of checked items).",
    )

    @field_validator("global_status")
    @classmethod
    def validate_global_status(cls, v: str) -> str:
        valid = {"approved", "rejected"}
        normalized = v.lower().strip()
        if normalized not in valid:
            raise ValueError(f"global_status must be 'approved' or 'rejected', got '{v}'.")
        return normalized

    @field_validator("layer_reviews")
    @classmethod
    def validate_layer_reviews(cls, v: Dict[str, str]) -> Dict[str, str]:
        for layer, result in v.items():
            if not (result == "APPROVED" or result.startswith("REJECTED:")):
                raise ValueError(
                    f"layer_reviews['{layer}'] must be 'APPROVED' or 'REJECTED: <reason>', got '{result}'."
                )
        return v


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

REVIEWER_SYSTEM_PROMPT = """You are a principal engineer performing a rigorous cross-layer code review.

Your job:
1. Review each layer (backend, frontend) independently against the acceptance criteria.
2. Check for cross-layer consistency: API contracts, data shapes, error handling alignment.
3. Be strict but fair. Only approve if the implementation genuinely meets all criteria.
4. If rejecting, provide specific, actionable feedback that the implementing agent can act on.

Review checklist per layer:
BACKEND:
  - All acceptance criteria addressed
  - RESTful API design followed
  - Input validation present
  - Error handling with proper HTTP status codes
  - Prisma models correctly defined (if schema changes)
  - Unit tests included
  - No frontend code present
  - Security considerations (auth, injection, etc.)

FRONTEND:
  - All acceptance criteria addressed
  - MUI components used correctly
  - TypeScript types defined
  - Loading/error/empty states handled
  - Accessibility attributes present
  - React Testing Library tests included
  - No backend code present
  - API integration matches backend contract

You respond ONLY with a valid JSON object — no markdown, no explanation.
"""

REVIEWER_USER_PROMPT_TEMPLATE = """## Issue
**Title:** {title}
**Body:** {body}

## Acceptance Criteria
{criteria}

## Backend Implementation
{backend_output}

## Frontend Implementation
{frontend_output}

## Review Instructions
Perform a layer-by-layer review. Return a JSON object with:
- layer_reviews: dict mapping each reviewed layer to "APPROVED" or "REJECTED: <reason>"
- global_status: "approved" if ALL layers pass, "rejected" otherwise
- reviewer_feedback: consolidated actionable feedback (specific file/function references)
- checklist: dict mapping each layer to a list of checked items

Only include layers that were actually submitted (non-null outputs).
"""


# ---------------------------------------------------------------------------
# LLM call with retry
# ---------------------------------------------------------------------------

def _format_agent_output(output: Optional[Dict], layer: str) -> str:
    """Format agent output for the review prompt."""
    if not output:
        return f"No {layer} implementation submitted (layer not in scope)."

    if "error" in output:
        return f"ERROR: {layer} agent failed — {output['error']}"

    files = output.get("files", [])
    if not files:
        return f"No files generated for {layer}."

    lines = [f"**Summary:** {output.get('summary', 'N/A')}"]
    lines.append(f"**Files ({len(files)}):**")
    for f in files:
        lines.append(f"\n### {f['path']}")
        lines.append(f"Description: {f['description']}")
        content = f.get("content", "")
        if len(content) > 3000:
            content = content[:3000] + "\n... (truncated)"
        lines.append(f"```\n{content}\n```")

    if output.get("package_delta"):
        lines.append(f"\n**Package additions:** {', '.join(output['package_delta'])}")

    if output.get("prisma_changes"):
        lines.append(f"\n**Prisma changes:** {output['prisma_changes']}")

    return "\n".join(lines)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ValueError, json.JSONDecodeError)),
    reraise=True,
)
def _call_gemini_pro(
    title: str,
    body: str,
    criteria: List[str],
    backend_output: Optional[Dict],
    frontend_output: Optional[Dict],
) -> ReviewerOutput:
    """Call Gemini Pro and parse output as ReviewerOutput."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        "gemini-1.5-pro",
        system_instruction=REVIEWER_SYSTEM_PROMPT,
    )

    user_message = REVIEWER_USER_PROMPT_TEMPLATE.format(
        title=title,
        body=body,
        criteria="\n".join(f"- {c}" for c in criteria),
        backend_output=_format_agent_output(backend_output, "backend"),
        frontend_output=_format_agent_output(frontend_output, "frontend"),
    )

    response = model.generate_content(
        user_message,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    raw = response.text.strip()
    logger.debug("Reviewer raw response length: %d chars", len(raw))

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
    return ReviewerOutput(**data)


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------

def run(state: AgentState) -> AgentState:
    """
    Agent 4: Cross-layer code review.
    Reviews all submitted agent outputs against acceptance criteria.
    """
    logger.info(
        "Reviewer agent starting for issue #%s (retry_count=%d)",
        state.get("issue_number"),
        state.get("retry_count", 0),
    )

    backend_output = state.get("backend_output")
    frontend_output = state.get("frontend_output")

    # If both outputs are None/missing, auto-reject
    if backend_output is None and frontend_output is None:
        logger.warning("Reviewer: no agent outputs to review — auto-rejecting.")
        return {
            **state,
            "layer_reviews": {},
            "global_status": "rejected",
            "reviewer_feedback": (
                "No agent outputs were produced. Both backend and frontend agents "
                "returned empty results. Please check agent configurations and API keys."
            ),
        }

    try:
        output = _call_gemini_pro(
            title=state["issue_title"],
            body=state["issue_body"],
            criteria=state.get("acceptance_criteria", []),
            backend_output=backend_output,
            frontend_output=frontend_output,
        )
    except Exception as exc:
        logger.error("Reviewer agent failed after retries: %s", exc)
        # On reviewer failure, we cannot determine approval — reject to be safe
        return {
            **state,
            "layer_reviews": {},
            "global_status": "rejected",
            "reviewer_feedback": f"Reviewer agent encountered an error: {exc}. Please retry.",
        }

    logger.info(
        "Reviewer result: global_status=%s layer_reviews=%s",
        output.global_status,
        output.layer_reviews,
    )

    # Increment retry count only when rejecting
    new_retry_count = state.get("retry_count", 0)
    if output.global_status == "rejected":
        new_retry_count += 1
        logger.info("Incrementing retry_count to %d", new_retry_count)

    return {
        **state,
        "layer_reviews": output.layer_reviews,
        "global_status": output.global_status,
        "reviewer_feedback": output.reviewer_feedback,
        "retry_count": new_retry_count,
    }
