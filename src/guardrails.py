"""
src/guardrails.py
Centralized guardrail functions for the AI Issue Solver pipeline.

Guards:
1. Prompt injection sanitization
2. File path traversal validation
3. Dangerous code pattern detection
4. Context token overflow prevention
"""

import re
import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Prompt Injection Sanitization
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions?",
    r"disregard\s+(the\s+)?(above|previous|prior)",
    r"forget\s+(all\s+)?(your\s+)?(previous\s+)?instructions?",
    r"you\s+are\s+now\s+",
    r"act\s+as\s+(if\s+you\s+are|a)\s+",
    r"new\s+instructions?:",
    r"system\s*:\s*",
    r"<\s*system\s*>",
    r"\[INST\]",
    r"\[/INST\]",
    r"###\s*instruction",
    r"---\s*system\s*---",
    r"override\s+(all\s+)?safety",
    r"jailbreak",
    r"do\s+anything\s+now",
    r"DAN\s+mode",
]

_INJECTION_RE = re.compile(
    "|".join(_INJECTION_PATTERNS),
    flags=re.IGNORECASE,
)

MAX_INPUT_CHARS = 10_000  # ~2,500 tokens


def sanitize_prompt_input(text: str, field_name: str = "input") -> str:
    """
    Sanitize user-supplied text before injecting into LLM prompts.
    - Truncates to MAX_INPUT_CHARS
    - Detects prompt injection patterns and wraps content in explicit delimiters
    - Strips null bytes
    """
    if not text:
        return text

    # Remove null bytes
    text = text.replace("\x00", "")

    # Truncate
    if len(text) > MAX_INPUT_CHARS:
        logger.warning(
            "Guardrail: %s truncated from %d to %d chars.",
            field_name, len(text), MAX_INPUT_CHARS,
        )
        text = text[:MAX_INPUT_CHARS] + f"\n[{field_name} truncated for safety]"

    # Detect injection patterns — wrap in delimiters to neutralize context escape
    matches = _INJECTION_RE.findall(text)
    if matches:
        logger.warning(
            "Guardrail: Potential prompt injection in %s — patterns: %s",
            field_name, matches,
        )
        text = f"[USER INPUT START]\n{text}\n[USER INPUT END]"

    return text


# ---------------------------------------------------------------------------
# 2. File Path Traversal Validation
# ---------------------------------------------------------------------------

ALLOWED_BACKEND_PREFIXES = (
    "src/routes/",
    "src/middleware/",
    "src/utils/",
    "src/config/",
    "src/services/",
    "prisma/",
    "tests/",
    "test/",
    "__tests__/",
)

ALLOWED_FRONTEND_PREFIXES = (
    "src/components/",
    "src/pages/",
    "src/hooks/",
    "src/contexts/",
    "src/store/",
    "src/styles/",
    "src/types/",
    "src/api/",
)

ALL_ALLOWED_PREFIXES = ALLOWED_BACKEND_PREFIXES + ALLOWED_FRONTEND_PREFIXES

_SENSITIVE_FILENAMES = {
    ".env", ".env.local", ".env.production", ".env.staging",
    "secrets.json", "credentials.json", "id_rsa", "id_ed25519",
}
_SENSITIVE_EXTENSIONS = (".pem", ".key", ".p12", ".pfx", ".cer")


def validate_file_path(path: str) -> str:
    """
    Validate that an LLM-generated file path is safe to commit.
    Returns the normalized path or raises ValueError.
    """
    if not path or not isinstance(path, str):
        raise ValueError("File path must be a non-empty string.")

    normalized = path.replace("\\", "/").strip("/").strip()

    if "\x00" in normalized:
        raise ValueError(f"Null byte in file path: {repr(path)}")

    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise ValueError(f"Absolute path not allowed: {path}")

    parts = normalized.split("/")
    if ".." in parts:
        raise ValueError(f"Path traversal detected: {path}")

    if parts[0].startswith(".") and parts[0] not in (".github",):
        raise ValueError(f"Hidden/dot file at root not allowed: {path}")

    filename = parts[-1].lower()
    if filename in _SENSITIVE_FILENAMES or filename.endswith(_SENSITIVE_EXTENSIONS):
        raise ValueError(f"Sensitive file not allowed: {path}")

    if not any(normalized.startswith(prefix) for prefix in ALL_ALLOWED_PREFIXES):
        raise ValueError(
            f"Path '{path}' is outside allowed directories. "
            f"Allowed prefixes: {ALL_ALLOWED_PREFIXES}"
        )

    return normalized


def validate_and_filter_files(files: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """
    Validate all file paths. Returns (valid_files, rejected_paths).
    """
    valid: List[Dict] = []
    rejected: List[str] = []
    for f in files:
        try:
            safe_path = validate_file_path(f["path"])
            valid.append({**f, "path": safe_path})
        except ValueError as exc:
            logger.error("Guardrail: Rejected path '%s': %s", f.get("path"), exc)
            rejected.append(f.get("path", "unknown"))
    return valid, rejected


# ---------------------------------------------------------------------------
# 3. Dangerous Code Pattern Detection
# ---------------------------------------------------------------------------

_DANGEROUS_CODE_PATTERNS = [
    # Arbitrary code execution
    (r"\beval\s*\(", "eval() usage"),
    (r"\bexec\s*\(", "exec() usage"),
    (r"\b__import__\s*\(", "__import__() usage"),
    # Shell execution — Node.js
    (r"\bchild_process\b", "child_process module"),
    (r"\bexecSync\s*\(", "execSync()"),
    (r"\bspawnSync\s*\(", "spawnSync()"),
    # Shell execution — Python
    (r"\bos\.system\s*\(", "os.system()"),
    (r"\bsubprocess\.(run|call|Popen|check_output)\s*\(.*shell\s*=\s*True", "subprocess with shell=True"),
    # Destructive filesystem
    (r"\brm\s+-rf\b", "rm -rf"),
    (r"\bfs\.rmdirSync\b", "fs.rmdirSync"),
    # Destructive SQL
    (r"\bDROP\s+TABLE\b", "DROP TABLE"),
    (r"\bDROP\s+DATABASE\b", "DROP DATABASE"),
    (r"\bTRUNCATE\s+TABLE\b", "TRUNCATE TABLE"),
    (r"\bDELETE\s+FROM\s+\w+\s*;", "DELETE without WHERE"),
    # Hardcoded credentials
    (r'(?:api[_-]?key|secret|password|passwd|token)\s*[:=]\s*["\'][^"\']{8,}["\']', "Hardcoded credential"),
    (r'["\'][A-Za-z0-9+/]{40,}={0,2}["\']', "Potential hardcoded token"),
]

_DANGEROUS_COMPILED = [
    (re.compile(pat, re.IGNORECASE | re.MULTILINE), desc)
    for pat, desc in _DANGEROUS_CODE_PATTERNS
]


def scan_generated_code(files: List[Dict]) -> List[str]:
    """
    Scan generated files for dangerous patterns.
    Returns a list of warning strings (empty = clean).
    """
    warnings: List[str] = []
    for f in files:
        path = f.get("path", "unknown")
        content = f.get("content", "")
        for pattern, description in _DANGEROUS_COMPILED:
            if pattern.search(content):
                warning = f"[{path}] {description}"
                warnings.append(warning)
                logger.warning("Guardrail: Dangerous pattern in '%s': %s", path, description)
    return warnings


# ---------------------------------------------------------------------------
# 4. Context Token Overflow Prevention
# ---------------------------------------------------------------------------

MAX_CONTEXT_CHARS = 40_000  # ~10,000 tokens


def truncate_context(context: str, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Truncate codebase context to prevent LLM token overflow."""
    if not context or len(context) <= max_chars:
        return context
    logger.warning(
        "Guardrail: Context truncated from %d to %d chars.",
        len(context), max_chars,
    )
    return (
        context[:max_chars]
        + f"\n\n... [Truncated: {len(context) - max_chars} chars omitted to fit token limit]"
    )
