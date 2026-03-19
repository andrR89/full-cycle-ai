"""
Agent 1 - Issue Reader
Uses Gemini Flash to classify issue layers, extract acceptance criteria,
and fetch relevant codebase context from GitHub.
"""

import os
import json
import logging
from typing import List, Optional

from google import genai
from google.genai import types as genai_types
from github import Github
from pydantic import BaseModel, Field, field_validator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.state import AgentState
from src.guardrails import sanitize_prompt_input, truncate_context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic output model
# ---------------------------------------------------------------------------

class ReaderOutput(BaseModel):
    issue_layers: List[str] = Field(
        description="List of affected layers. Valid values: 'backend', 'frontend'. At least one required.",
        min_length=1,
    )
    acceptance_criteria: List[str] = Field(
        description="Measurable acceptance criteria extracted from the issue.",
        min_length=1,
    )
    priority: str = Field(
        description="Issue priority: 'low', 'medium', or 'high'.",
    )

    @field_validator("issue_layers")
    @classmethod
    def validate_layers(cls, v: List[str]) -> List[str]:
        valid = {"backend", "frontend"}
        cleaned = [layer.lower().strip() for layer in v]
        invalid = [l for l in cleaned if l not in valid]
        if invalid:
            raise ValueError(f"Invalid layers detected: {invalid}. Must be one of {valid}.")
        return list(set(cleaned))  # deduplicate

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        valid = {"low", "medium", "high"}
        normalized = v.lower().strip()
        if normalized not in valid:
            raise ValueError(f"Invalid priority '{v}'. Must be one of {valid}.")
        return normalized


# ---------------------------------------------------------------------------
# Codebase context fetching
# ---------------------------------------------------------------------------

def _fetch_github_file_content(repo, path: str, max_chars: int = 4000) -> Optional[str]:
    """Fetch a single file from GitHub, truncating if too large."""
    try:
        content_file = repo.get_contents(path)
        if isinstance(content_file, list):
            # It's a directory listing — shouldn't happen but handle gracefully
            return None
        decoded = content_file.decoded_content.decode("utf-8", errors="replace")
        if len(decoded) > max_chars:
            decoded = decoded[:max_chars] + f"\n... (truncated, {len(decoded)} total chars)"
        return f"### {path}\n```\n{decoded}\n```"
    except Exception as exc:
        logger.debug("Could not fetch %s: %s", path, exc)
        return None


def _list_dir_files(repo, path: str) -> List[str]:
    """Return file paths inside a directory (non-recursive, first level)."""
    try:
        contents = repo.get_contents(path)
        if not isinstance(contents, list):
            return []
        return [c.path for c in contents if c.type == "file"]
    except Exception as exc:
        logger.debug("Could not list %s: %s", path, exc)
        return []


def fetch_codebase_context(repo_name: str, layers: List[str]) -> str:
    """
    Fetch relevant codebase files from GitHub based on detected layers.
    Backend: src/routes/, prisma/schema.prisma
    Frontend: src/components/, src/pages/
    """
    gh_pat = os.environ.get("GH_PAT")
    if not gh_pat:
        logger.warning("GH_PAT not set — skipping codebase context fetch.")
        return "No codebase context available (GH_PAT not configured)."

    g = Github(gh_pat)
    try:
        repo = g.get_repo(repo_name)
    except Exception as exc:
        logger.warning("Could not access repo %s: %s", repo_name, exc)
        return f"Could not access repository '{repo_name}': {exc}"

    sections: List[str] = []

    if "backend" in layers:
        prisma_content = _fetch_github_file_content(repo, "backend/prisma/schema.prisma")
        if prisma_content:
            sections.append(prisma_content)

        route_files = _list_dir_files(repo, "backend/src/routes")
        for fpath in route_files[:5]:
            content = _fetch_github_file_content(repo, fpath)
            if content:
                sections.append(content)

        middleware_files = _list_dir_files(repo, "backend/src/middleware")
        for fpath in middleware_files[:3]:
            content = _fetch_github_file_content(repo, fpath)
            if content:
                sections.append(content)

    if "frontend" in layers:
        component_files = _list_dir_files(repo, "frontend/src/components")
        for fpath in component_files[:5]:
            content = _fetch_github_file_content(repo, fpath)
            if content:
                sections.append(content)

        page_files = _list_dir_files(repo, "frontend/src/pages")
        for fpath in page_files[:5]:
            content = _fetch_github_file_content(repo, fpath)
            if content:
                sections.append(content)

        hook_files = _list_dir_files(repo, "frontend/src/hooks")
        for fpath in hook_files[:3]:
            content = _fetch_github_file_content(repo, fpath)
            if content:
                sections.append(content)

    if not sections:
        return "No relevant codebase files found for the detected layers."

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# LLM call with retry
# ---------------------------------------------------------------------------

READER_PROMPT_TEMPLATE = """You are an expert software architect analyzing a GitHub issue.

## Issue Title
{title}

## Issue Body
{body}

## Retry Context (if any)
{retry_context}

## Task
Analyze this issue and return a JSON object with exactly these fields:
- issue_layers: list of affected layers (ONLY "backend" and/or "frontend")
- acceptance_criteria: list of measurable acceptance criteria (at least 3 items)
- priority: one of "low", "medium", "high"

Respond ONLY with valid JSON. No markdown fences, no explanation.

Example:
{{"issue_layers": ["backend", "frontend"], "acceptance_criteria": ["API endpoint returns 200 on success", "Frontend shows error on validation failure", "Database record is created"], "priority": "medium"}}
"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ValueError, json.JSONDecodeError)),
    reraise=True,
)
def _call_gemini_flash(title: str, body: str, retry_context: str = "") -> ReaderOutput:
    """Call Gemini Flash and parse output as ReaderOutput."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")

    client = genai.Client(api_key=api_key)

    prompt = READER_PROMPT_TEMPLATE.format(
        title=sanitize_prompt_input(title, "issue_title"),
        body=sanitize_prompt_input(body, "issue_body"),
        retry_context=retry_context if retry_context else "N/A",
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    raw = response.text.strip()
    logger.debug("Reader raw response: %s", raw)

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    data = json.loads(raw)
    return ReaderOutput(**data)


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------

def run(state: AgentState) -> AgentState:
    """
    Agent 1: Read and classify the issue.
    Fetches codebase context from GitHub and classifies layers.
    """
    logger.info(
        "Reader agent starting for issue #%s: %s",
        state.get("issue_number"),
        state.get("issue_title"),
    )

    retry_context = ""
    if state.get("retry_count", 0) > 0 and state.get("reviewer_feedback"):
        retry_context = (
            f"Previous attempt was rejected. Reviewer feedback:\n{state['reviewer_feedback']}\n"
            "Please re-analyze taking this feedback into account."
        )

    try:
        output = _call_gemini_flash(
            title=state["issue_title"],
            body=state["issue_body"],
            retry_context=retry_context,
        )
    except Exception as exc:
        logger.error("Reader agent failed after retries: %s", exc)
        # Fallback: default to both layers with generic criteria
        output = ReaderOutput(
            issue_layers=["backend", "frontend"],
            acceptance_criteria=[
                "Feature is implemented as described in the issue",
                "No existing functionality is broken",
                "Code follows project conventions",
            ],
            priority="medium",
        )

    # Fetch codebase context based on detected layers
    codebase_context = fetch_codebase_context(
        repo_name=state.get("repo_name", ""),
        layers=output.issue_layers,
    )

    logger.info(
        "Reader classified layers=%s priority=%s",
        output.issue_layers,
        output.priority,
    )

    return {
        **state,
        "issue_layers": output.issue_layers,
        "acceptance_criteria": output.acceptance_criteria,
        "priority": output.priority,
        "codebase_context": truncate_context(codebase_context),
    }
