"""
LangGraph graph definition for the multi-agent issue solver pipeline.

Flow:
  reader
    ↓ (Send API — parallel fan-out)
  backend_agent  frontend_agent
    ↓               ↓
       reviewer
         ↓
    (approved) → deployer → END
    (rejected, retry < 2) → reader (retry with feedback)
    (rejected, retry >= 2) → close_rejected → END
"""

import logging
from typing import List, Union

from langgraph.graph import StateGraph, END
from langgraph.types import Send

from src.state import AgentState
from src.agents import reader, backend, frontend, reviewer, deployer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Router functions
# ---------------------------------------------------------------------------

def layer_router(state: AgentState) -> Union[List[Send], str]:
    """
    Fan-out to parallel agents based on detected layers.
    Uses LangGraph Send API for true parallel execution.
    Falls through to reviewer if no layers detected.
    """
    layers = state.get("issue_layers", [])
    logger.debug("layer_router: layers=%s", layers)

    sends: List[Send] = []
    for layer in layers:
        if layer == "backend":
            sends.append(Send("backend_agent", state))
        elif layer == "frontend":
            sends.append(Send("frontend_agent", state))

    if not sends:
        logger.warning("layer_router: no layers detected, routing directly to reviewer.")
        return "reviewer"

    return sends


def review_router(state: AgentState) -> str:
    """
    Route after reviewer decision.
    - approved → deployer
    - rejected + retries remaining → reader (retry with feedback)
    - rejected + no retries → close_rejected
    """
    global_status = state.get("global_status", "rejected")
    retry_count = state.get("retry_count", 0)

    logger.debug(
        "review_router: global_status=%s retry_count=%d",
        global_status,
        retry_count,
    )

    if global_status == "approved":
        logger.info("Reviewer approved — routing to deployer.")
        return "deployer"
    elif retry_count < 2:
        logger.info(
            "Reviewer rejected (retry_count=%d < 2) — routing back to reader for retry.",
            retry_count,
        )
        return "retry"
    else:
        logger.info(
            "Reviewer rejected after %d retries — routing to close_rejected.",
            retry_count,
        )
        return "close_rejected"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """Build and return the compiled LangGraph graph."""
    builder = StateGraph(AgentState)

    # Register nodes
    builder.add_node("reader", reader.run)
    builder.add_node("backend_agent", backend.run)
    builder.add_node("frontend_agent", frontend.run)
    builder.add_node("reviewer", reviewer.run)
    builder.add_node("deployer", deployer.run)
    builder.add_node("close_rejected", deployer.close_as_rejected)

    # Entry point
    builder.set_entry_point("reader")

    # reader → parallel fan-out via Send API
    builder.add_conditional_edges(
        "reader",
        layer_router,
        # Mapping is needed when using Send API for multiple possible destinations
        {
            "reviewer": "reviewer",  # direct path when no layers detected
        },
    )

    # Parallel agents → reviewer (both converge)
    builder.add_edge("backend_agent", "reviewer")
    builder.add_edge("frontend_agent", "reviewer")

    # reviewer → conditional routing
    builder.add_conditional_edges(
        "reviewer",
        review_router,
        {
            "deployer": "deployer",
            "retry": "reader",
            "close_rejected": "close_rejected",
        },
    )

    # Terminal edges
    builder.add_edge("deployer", END)
    builder.add_edge("close_rejected", END)

    return builder.compile()


# Module-level compiled graph instance
graph = build_graph()
