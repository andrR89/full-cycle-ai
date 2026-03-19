from typing import TypedDict, List, Dict, Optional, Annotated
import operator


class AgentState(TypedDict):
    # Issue data
    issue_number: int
    issue_title: str
    issue_body: str
    issue_url: str
    repo_name: str

    # Reader output
    issue_layers: List[str]  # ["backend", "frontend"]
    acceptance_criteria: List[str]
    priority: str

    # Agent outputs
    backend_output: Optional[Dict]
    frontend_output: Optional[Dict]

    # Reviewer
    layer_reviews: Dict[str, str]  # {"backend": "APPROVED", "frontend": "REJECTED: reason"}
    global_status: str  # "approved" | "rejected"
    reviewer_feedback: Optional[str]

    # Retry control
    retry_count: int

    # Deployer
    branch_name: Optional[str]
    pr_url: Optional[str]
    ci_passed: Optional[bool]       # True = merged, False = left open, None = not run yet
    guardrail_rejected: Optional[List[str]]  # paths rejected by guardrail

    # Codebase context (fetched from repo)
    codebase_context: Optional[str]
