import pytest
from langgraph.types import Send
from src.graph import layer_router, review_router


class TestLayerRouter:
    def test_backend_only(self):
        state = {"issue_layers": ["backend"], "issue_title": "", "issue_body": ""}
        result = layer_router(state)
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], Send)
        assert result[0].node == "backend_agent"

    def test_frontend_only(self):
        state = {"issue_layers": ["frontend"], "issue_title": "", "issue_body": ""}
        result = layer_router(state)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].node == "frontend_agent"

    def test_both_layers_parallel(self):
        state = {"issue_layers": ["backend", "frontend"], "issue_title": "", "issue_body": ""}
        result = layer_router(state)
        assert isinstance(result, list)
        assert len(result) == 2
        nodes = {s.node for s in result}
        assert nodes == {"backend_agent", "frontend_agent"}

    def test_no_layers_routes_to_reviewer(self):
        state = {"issue_layers": [], "issue_title": "", "issue_body": ""}
        result = layer_router(state)
        assert result == "reviewer"

    def test_missing_layers_key_routes_to_reviewer(self):
        state = {}
        result = layer_router(state)
        assert result == "reviewer"


class TestReviewRouter:
    def test_approved_routes_to_deployer(self):
        state = {"global_status": "approved", "retry_count": 0}
        assert review_router(state) == "deployer"

    def test_rejected_first_time_routes_to_retry(self):
        state = {"global_status": "rejected", "retry_count": 1}
        assert review_router(state) == "retry"

    def test_rejected_second_time_routes_to_retry(self):
        state = {"global_status": "rejected", "retry_count": 1}
        assert review_router(state) == "retry"

    def test_rejected_after_max_retries_routes_to_close(self):
        state = {"global_status": "rejected", "retry_count": 2}
        assert review_router(state) == "close_rejected"

    def test_missing_status_defaults_to_rejected(self):
        state = {"retry_count": 3}
        assert review_router(state) == "close_rejected"
