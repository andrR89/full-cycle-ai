"""
AI Issue Solver — Multi-agent LangGraph system.

Agents:
    0 - Issue Creator: REST API (FastAPI + Gemini Flash)
    1 - Reader:        Issue classification (Gemini Flash)
    2 - Backend:       Code generation (Claude Sonnet)
    3 - Frontend:      Code generation (Claude Sonnet)
    4 - Reviewer:      Cross-layer review (Gemini Pro)
    5 - Deployer:      GitHub operations (PyGithub)
"""
