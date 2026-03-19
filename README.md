# full-cycle-ai

A multi-agent LangGraph pipeline that solves GitHub issues automatically. Point it at any repository, label an issue `ai-solve`, and the pipeline classifies the problem, generates backend and/or frontend code, reviews its own output, and opens a pull request — all without human intervention.

---

## Architecture

```
                        ┌─────────────────────────────────────────┐
                        │          GitHub Actions Trigger          │
                        │  (issues: labeled  OR  repository_dispatch) │
                        └────────────────────┬────────────────────┘
                                             │
                              ┌──────────────▼──────────────┐
                              │     Agent 0 — Issue Creator │  ← REST API (optional)
                              │  POST /issues  (FastAPI)     │
                              └──────────────┬──────────────┘
                                             │ (creates issue + label)
                              ┌──────────────▼──────────────┐
                              │   Agent 1 — Reader           │
                              │   Gemini Flash               │
                              │   - Classifies layers        │
                              │   - Extracts acceptance      │
                              │     criteria                 │
                              │   - Fetches codebase context │
                              └──────┬──────────────┬───────┘
                                     │              │
                     ┌───────────────▼──┐        ┌──▼───────────────────┐
                     │ Agent 2 — Backend│        │ Agent 3 — Frontend   │
                     │ Claude Sonnet    │        │ Claude Sonnet         │
                     │ Node/Express/    │        │ React/TypeScript/MUI  │
                     │ Prisma code      │        │ component code        │
                     └───────────┬──────┘        └───────┬───────────────┘
                                 │     (parallel)        │
                                 └──────────┬────────────┘
                                            │
                              ┌─────────────▼─────────────┐
                              │   Agent 4 — Reviewer       │
                              │   Gemini Pro               │
                              │   - Cross-layer review     │
                              │   - Acceptance criteria    │
                              │     validation             │
                              └──────┬──────────────┬──────┘
                                     │              │
                               approved          rejected
                                     │              │
                                     │         retry < 2 → back to Reader
                                     │         retry >= 2 → close issue
                                     │
                              ┌──────▼──────────────────────┐
                              │   Agent 5 — Deployer         │
                              │   PyGithub (no LLM)          │
                              │   - Creates branch           │
                              │   - Commits files            │
                              │   - Opens pull request       │
                              │   - Auto-merges if CI passes │
                              └──────────────────────────────┘
```

---

## Agents

| # | Agent | Model | Responsibility |
|---|-------|-------|----------------|
| 0 | Issue Creator | Gemini Flash | REST API — converts natural language into structured GitHub issues |
| 1 | Reader | Gemini Flash | Classifies layers (`backend`/`frontend`), extracts acceptance criteria, fetches codebase context |
| 2 | Backend Specialist | Claude Sonnet | Generates Node.js / Express / Prisma code (never frontend) |
| 3 | Frontend Specialist | Claude Sonnet | Generates React / TypeScript / MUI code (never backend) |
| 4 | Reviewer | Gemini Pro | Cross-layer code review against acceptance criteria; approves or rejects with actionable feedback |
| 5 | Deployer | PyGithub | Creates branch, commits files, opens PR, polls CI, auto-merges on success |

---

## Setup

### 1. GitHub Secrets

The following secrets must be configured in the `full-cycle-ai` repository under **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key (Claude Sonnet) |
| `GEMINI_API_KEY` | Google Gemini API key |
| `GH_PAT` | GitHub Personal Access Token with `repo` and `workflow` scopes |

### 2. Python requirements

```bash
pip install -r requirements.txt
```

Python 3.11 is required. Key dependencies: `langgraph`, `anthropic`, `google-generativeai`, `PyGithub`, `fastapi`, `pydantic`, `tenacity`, `slowapi`.

### 3. Local environment

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

---

## Usage: Central pipeline (self-hosted issues)

Label any issue in the `full-cycle-ai` repository with `ai-solve`. The workflow in `.github/workflows/ai-issue-solver.yml` fires automatically and runs `python src/main.py`.

---

## Usage: Multi-repo (trigger from any project)

The pipeline can solve issues from **external repositories**. Two steps are required:

### Step 1 — Add the mini workflow to the target repo

Copy `.github/workflows/trigger-ai-pipeline.yml` into the target repository:

```bash
# In the target repo
mkdir -p .github/workflows
cp /path/to/full-cycle-ai/.github/workflows/trigger-ai-pipeline.yml \
   .github/workflows/trigger-ai-pipeline.yml
```

### Step 2 — Add a secret to the target repo

In the target repository, add a secret named `PIPELINE_PAT` — a GitHub PAT that has `repo` and `workflow` access to `andrR89/full-cycle-ai`.

### How it works

When an issue in the target repo is labeled `ai-solve`:

1. `trigger-ai-pipeline.yml` fires and sends a `repository_dispatch` event to `andrR89/full-cycle-ai` with the issue payload (`repo_name`, `issue_number`, `issue_title`, `issue_body`, `issue_url`).
2. `ai-issue-solver.yml` receives the dispatch, reads the payload, and runs the full pipeline against the **target repository**.
3. The Deployer agent opens a PR in the **target repository**.

---

## Usage: Agent 0 REST API

Agent 0 exposes an HTTP API for creating structured GitHub issues from natural language. Start the server:

```bash
uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

### Endpoints

#### `POST /issues`

Convert natural language into a GitHub issue and optionally trigger the AI pipeline.

```bash
curl -X POST http://localhost:8000/issues \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Users cannot reset their password. The reset link expires immediately after being sent.",
    "repo_name": "my-org/my-repo",
    "auto_label": true
  }'
```

Response:

```json
{
  "issue_number": 47,
  "issue_url": "https://github.com/my-org/my-repo/issues/47",
  "issue_title": "Password reset link expires immediately",
  "layers": ["backend"],
  "priority": "high",
  "labels_applied": ["bug", "ai-solve", "priority:high"],
  "pipeline_triggered": true
}
```

#### `POST /issues/batch`

Create up to 10 issues in a single request. Rate limited to 5 requests/minute.

#### `GET /health`

Returns service status and whether API keys are configured.

#### `GET /docs`

Auto-generated Swagger UI (OpenAPI).

### Authentication

Set the `API_KEY` environment variable to require an `X-API-Key` header on all requests. If `API_KEY` is not set, the API is open.

### Rate limiting

- `/issues`: 10 requests/minute per IP
- `/issues/batch`: 5 requests/minute per IP

---

## Guardrails

The pipeline enforces four layers of security before any code reaches GitHub:

| Guardrail | Function | What it blocks |
|-----------|----------|----------------|
| Prompt injection sanitization | `sanitize_prompt_input()` | Injection patterns (`ignore all previous instructions`, `jailbreak`, etc.); null bytes; truncates inputs > 10,000 chars |
| File path validation | `validate_file_path()` | Path traversal (`../../`), absolute paths, hidden root files, sensitive filenames (`.env`, `.pem`, `id_rsa`), paths outside allowed directories |
| File filter | `validate_and_filter_files()` | Applies path validation to all LLM-generated files; silently drops rejected files and logs them |
| Dangerous code scan | `scan_generated_code()` | `eval()`, `exec()`, `os.system()`, `subprocess` with `shell=True`, `rm -rf`, `DROP TABLE`, hardcoded credentials and tokens |

Allowed file path prefixes:

- **Backend**: `src/routes/`, `src/middleware/`, `src/utils/`, `src/config/`, `src/services/`, `prisma/`, `tests/`, `test/`, `__tests__/`
- **Frontend**: `src/components/`, `src/pages/`, `src/hooks/`, `src/contexts/`, `src/store/`, `src/styles/`, `src/types/`, `src/api/`

Security warnings from the code scanner are appended to the PR body and flagged for human review rather than blocking the PR.

---

## Development and testing

### Run the test suite

```bash
# Install dependencies (includes test deps via requirements.txt)
pip install -r requirements.txt
pip install pytest

# Run all tests
pytest

# Run a specific test file
pytest tests/test_guardrails.py -v

# Run with coverage
pip install pytest-cov
pytest --cov=src --cov-report=term-missing
```

All tests are fully isolated — no real API calls are made. External services (Gemini, Claude, PyGithub) are mocked with `unittest.mock`.

### Test structure

```
tests/
├── __init__.py
├── test_guardrails.py      # All 4 guardrail functions
├── test_graph.py           # layer_router and review_router
├── test_main.py            # build_initial_state, validate_environment
└── agents/
    ├── __init__.py
    ├── test_reader.py       # Agent 1 — classification, fallback, output validation
    ├── test_backend.py      # Agent 2 — generation, layer gating, output validation
    ├── test_reviewer.py     # Agent 4 — approval/rejection, retry counter, output validation
    └── test_deployer.py     # Agent 5 — file collection, branch/PR creation, rejection path
```

### Running the pipeline locally

```bash
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
export GH_PAT=...
export GITHUB_REPO=owner/repo
export ISSUE_NUMBER=42
export ISSUE_TITLE="Add pagination to users list"
export ISSUE_BODY="The users endpoint should support page and limit query parameters."
export ISSUE_URL="https://github.com/owner/repo/issues/42"

python src/main.py
```

---

## Cost estimate

Costs are per pipeline run (one issue). Estimates assume typical issue complexity.

| Agent | Model | Input tokens | Output tokens | Estimated cost (USD) |
|-------|-------|-------------|--------------|----------------------|
| Reader | Gemini 1.5 Flash | ~2,000 | ~300 | ~$0.001 |
| Backend | Claude Sonnet | ~6,000 | ~4,000 | ~$0.075 |
| Frontend | Claude Sonnet | ~6,000 | ~4,000 | ~$0.075 |
| Reviewer | Gemini 1.5 Pro | ~8,000 | ~1,000 | ~$0.030 |
| **Total (single attempt)** | | | | **~$0.18** |
| **Total (2 retries, worst case)** | | | | **~$0.54** |

Costs exclude GitHub Actions compute time (ubuntu-latest, typically 2–5 minutes per run).
