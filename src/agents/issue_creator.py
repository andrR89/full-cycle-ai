"""
Agent 0 - Issue Creator
FastAPI REST API that accepts natural language from clients,
uses Gemini Flash to structure it into a proper GitHub issue,
and creates the issue via PyGithub.
"""

import os
import json
import logging
from typing import List, Optional

import google.generativeai as genai
from github import Github, GithubException
from pydantic import BaseModel, Field, field_validator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CreateIssueRequest(BaseModel):
    """Incoming request from the client."""
    text: str = Field(
        description="Natural language description of the issue or feature request.",
        min_length=10,
    )
    repo_name: str = Field(
        description="GitHub repository in 'owner/repo' format.",
        pattern=r"^[^/]+/[^/]+$",
    )
    auto_label: bool = Field(
        default=True,
        description="If True, automatically adds the 'ai-solve' label to trigger the pipeline.",
    )


class StructuredIssue(BaseModel):
    """Gemini Flash output — structured GitHub issue."""
    title: str = Field(description="Concise issue title (max 100 chars).")
    body: str = Field(description="Full issue body in GitHub Markdown format.")
    labels: List[str] = Field(
        default_factory=list,
        description="List of label names to apply.",
    )
    layers: List[str] = Field(
        description="Detected layers: 'backend' and/or 'frontend'.",
        min_length=1,
    )
    priority: str = Field(description="Priority: 'low', 'medium', or 'high'.")

    @field_validator("layers")
    @classmethod
    def validate_layers(cls, v: List[str]) -> List[str]:
        valid = {"backend", "frontend"}
        cleaned = [l.lower().strip() for l in v]
        invalid = [l for l in cleaned if l not in valid]
        if invalid:
            raise ValueError(f"Invalid layers: {invalid}.")
        return list(set(cleaned))

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        valid = {"low", "medium", "high"}
        normalized = v.lower().strip()
        if normalized not in valid:
            raise ValueError(f"Invalid priority '{v}'.")
        return normalized

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if len(v) > 100:
            return v[:100]
        return v


class CreateIssueResponse(BaseModel):
    """Response returned to the client."""
    issue_number: int
    issue_url: str
    issue_title: str
    layers: List[str]
    priority: str
    labels_applied: List[str]
    pipeline_triggered: bool


# ---------------------------------------------------------------------------
# Gemini Flash prompt
# ---------------------------------------------------------------------------

STRUCTURER_PROMPT_TEMPLATE = """You are a technical product manager converting user requests into well-structured GitHub issues.

## User Request
{text}

## Instructions
Convert the above into a structured GitHub issue. Return a JSON object with:
- title: concise title (max 100 chars, no prefixes like "Feature:" or "Bug:")
- body: full issue body in GitHub Markdown with these sections:
  ## Description
  ## Problem / Motivation
  ## Proposed Solution
  ## Acceptance Criteria
  ## Additional Context
- labels: list of appropriate label names (e.g., "enhancement", "bug", "documentation")
- layers: list of affected layers — ONLY "backend" and/or "frontend"
- priority: "low", "medium", or "high"

Acceptance criteria must be measurable (at least 3 items).
Respond ONLY with valid JSON. No markdown fences, no explanation.
"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ValueError, json.JSONDecodeError)),
    reraise=True,
)
def _structure_issue_with_gemini(text: str) -> StructuredIssue:
    """Use Gemini Flash to convert natural language into a structured issue."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = STRUCTURER_PROMPT_TEMPLATE.format(text=text)

    response = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    raw = response.text.strip()
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
    return StructuredIssue(**data)


# ---------------------------------------------------------------------------
# GitHub issue creation
# ---------------------------------------------------------------------------

def _ensure_label_exists(repo, label_name: str, color: str = "0075ca") -> None:
    """Create label if it doesn't exist."""
    try:
        repo.get_label(label_name)
    except GithubException as exc:
        if exc.status == 404:
            try:
                repo.create_label(name=label_name, color=color)
                logger.info("Created label '%s'", label_name)
            except GithubException as create_exc:
                logger.warning("Could not create label '%s': %s", label_name, create_exc)
        else:
            logger.warning("Could not check label '%s': %s", label_name, exc)


def create_github_issue(
    repo_name: str,
    structured: StructuredIssue,
    auto_label: bool = True,
) -> dict:
    """Create a GitHub issue and return its metadata."""
    gh_pat = os.environ.get("GH_PAT")
    if not gh_pat:
        raise EnvironmentError("GH_PAT environment variable is not set.")

    g = Github(gh_pat)
    repo = g.get_repo(repo_name)

    # Determine labels
    labels_to_apply = list(structured.labels)
    if auto_label and "ai-solve" not in labels_to_apply:
        labels_to_apply.append("ai-solve")

    # Add priority label
    priority_label = f"priority:{structured.priority}"
    labels_to_apply.append(priority_label)

    # Ensure all labels exist
    label_colors = {
        "ai-solve": "e4e669",
        "priority:high": "d93f0b",
        "priority:medium": "fbca04",
        "priority:low": "0e8a16",
        "enhancement": "84b6eb",
        "bug": "d73a4a",
    }
    for label in labels_to_apply:
        color = label_colors.get(label, "ededed")
        _ensure_label_exists(repo, label, color)

    # Create issue
    issue = repo.create_issue(
        title=structured.title,
        body=structured.body,
        labels=labels_to_apply,
    )

    logger.info("Created issue #%d: %s", issue.number, issue.html_url)

    return {
        "issue_number": issue.number,
        "issue_url": issue.html_url,
        "issue_title": issue.title,
        "layers": structured.layers,
        "priority": structured.priority,
        "labels_applied": labels_to_apply,
        "pipeline_triggered": auto_label,
    }


# ---------------------------------------------------------------------------
# Standalone function for programmatic use (no FastAPI dependency)
# ---------------------------------------------------------------------------

def create_issue_from_text(
    text: str,
    repo_name: str,
    auto_label: bool = True,
) -> CreateIssueResponse:
    """
    Full pipeline: structure text with Gemini → create GitHub issue.
    Can be called programmatically without starting the FastAPI server.
    """
    structured = _structure_issue_with_gemini(text)
    result = create_github_issue(repo_name, structured, auto_label)
    return CreateIssueResponse(**result)
