"""
Multi-agent system for automated issue solving.

Agents:
    0 - Issue Creator (Agent 0): REST API intermediator via FastAPI + Gemini Flash
    1 - Reader       (Agent 1): Classifies layers via Gemini Flash
    2 - Backend      (Agent 2): Generates backend code via Claude Sonnet
    3 - Frontend     (Agent 3): Generates frontend code via Claude Sonnet
    4 - Reviewer     (Agent 4): Cross-layer review via Gemini Pro
    5 - Deployer     (Agent 5): Deterministic GitHub operations via PyGithub
"""
