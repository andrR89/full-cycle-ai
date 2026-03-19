"""
FastAPI application for Agent 0 - Issue Creator REST API.

Provides an HTTP endpoint that clients can call with natural language text
to automatically create structured GitHub issues and trigger the AI pipeline.

Usage:
    uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    POST /issues          - Create a new GitHub issue from natural language
    GET  /health          - Health check
    GET  /docs            - Auto-generated OpenAPI docs (Swagger UI)
"""

import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, status, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

from src.agents.issue_creator import (
    CreateIssueRequest,
    CreateIssueResponse,
    create_issue_from_text,
)

load_dotenv()

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# Lifespan & app setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("AI Issue Creator API starting up...")
    # Validate required env vars on startup
    required = ["GEMINI_API_KEY", "GH_PAT"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        logger.error("Missing required environment variables: %s", missing)
    yield
    logger.info("AI Issue Creator API shutting down...")


app = FastAPI(
    title="AI Issue Creator API",
    description=(
        "Agent 0: REST API that converts natural language into structured GitHub issues "
        "and triggers the AI Issue Solver pipeline."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware — adjust origins for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Optional API key auth
# ---------------------------------------------------------------------------

API_KEY = os.environ.get("API_KEY")  # Optional: set to require auth


async def verify_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Optional API key verification. Only enforced if API_KEY env var is set."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Provide it via the X-API-Key header.",
        )


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    version: str
    gemini_configured: bool
    github_configured: bool


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check",
)
async def health_check() -> HealthResponse:
    """Return service health status and configuration state."""
    return HealthResponse(
        status="ok",
        version="1.0.0",
        gemini_configured=bool(os.environ.get("GEMINI_API_KEY")),
        github_configured=bool(os.environ.get("GH_PAT")),
    )


@app.post(
    "/issues",
    response_model=CreateIssueResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Issues"],
    summary="Create a GitHub issue from natural language",
    responses={
        201: {"description": "Issue created successfully."},
        400: {"model": ErrorResponse, "description": "Invalid input."},
        401: {"model": ErrorResponse, "description": "Authentication failed."},
        422: {"model": ErrorResponse, "description": "Validation error."},
        429: {"description": "Rate limit exceeded."},
        500: {"model": ErrorResponse, "description": "Internal server error."},
    },
)
@limiter.limit("10/minute")
async def create_issue(
    request: Request,
    body: CreateIssueRequest,
    _: None = Depends(verify_api_key),
) -> CreateIssueResponse:
    """
    Convert natural language text into a structured GitHub issue.

    - Uses Gemini Flash to extract title, body, labels, layers, and acceptance criteria.
    - Creates the issue on GitHub with appropriate labels.
    - Optionally adds the 'ai-solve' label to trigger the AI pipeline via GitHub Actions.

    **Example request:**
    ```json
    {
        "text": "Users can't reset their password via email. The reset link doesn't work.",
        "repo_name": "my-org/my-repo",
        "auto_label": true
    }
    ```
    """
    logger.info(
        "POST /issues — repo=%s auto_label=%s text_len=%d",
        body.repo_name,
        body.auto_label,
        len(body.text),
    )

    try:
        response = create_issue_from_text(
            text=body.text,
            repo_name=body.repo_name,
            auto_label=body.auto_label,
        )
        logger.info(
            "Issue #%d created: %s",
            response.issue_number,
            response.issue_url,
        )
        return response

    except EnvironmentError as exc:
        logger.error("Configuration error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Server configuration error: {exc}",
        )
    except Exception as exc:
        logger.exception("Unexpected error creating issue: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create issue: {str(exc)}",
        )


@app.post(
    "/issues/batch",
    response_model=list[CreateIssueResponse],
    status_code=status.HTTP_201_CREATED,
    tags=["Issues"],
    summary="Create multiple GitHub issues from natural language",
    responses={
        201: {"description": "Issues created successfully."},
        401: {"model": ErrorResponse, "description": "Authentication failed."},
        429: {"description": "Rate limit exceeded."},
        500: {"model": ErrorResponse, "description": "Internal server error."},
    },
)
@limiter.limit("5/minute")
async def create_issues_batch(
    request: Request,
    requests: list[CreateIssueRequest],
    _: None = Depends(verify_api_key),
) -> list[CreateIssueResponse]:
    """
    Create multiple GitHub issues in sequence.
    Each issue is processed independently. Failures are returned with error details.
    Maximum 10 issues per batch request.
    """
    if len(requests) > 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch size exceeds maximum of 10 issues.",
        )

    results = []
    for req in requests:
        try:
            response = create_issue_from_text(
                text=req.text,
                repo_name=req.repo_name,
                auto_label=req.auto_label,
            )
            results.append(response)
        except Exception as exc:
            logger.error("Batch issue creation failed for '%s': %s", req.text[:50], exc)
            # Continue processing remaining requests
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create one or more issues: {str(exc)}",
            )

    return results


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "Validation error", "detail": str(exc)},
    )


@app.exception_handler(EnvironmentError)
async def env_error_handler(request, exc):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Configuration error", "detail": str(exc)},
    )
