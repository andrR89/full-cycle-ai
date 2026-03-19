<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# **Especificação FINAL: 5 Agentes Especializados (Um por Camada Stack)**

**Excelente insight**! Em vez de super-agent genérico, **5 agentes dedicados** — cada um expert na sua camada Node/React/Postgres. Mais preciso, menos alucinação, reviews granulares. Claude implementa assim.[^1][^2]

## 🏗️ **5 Agentes Especializados (LangGraph Supervisor)**

```
Webhook Issue → Supervisor Router
                ├── Agent 1: READER (GitHub context)
                ├── Agent 2: BACKEND (Node/Prisma specialist)
                ├── Agent 3: FRONTEND (React/MUI specialist) 
                ├── Agent 4: REVIEWER (Gemini cross-layer)
                └── Agent 5: DEPLOYER (GitHub + staging)
```


## 🤖 **Agentes e Prompts Especializados**

### **Agent 1: Issue Reader**

```
"Sou GitHub Issue Analyst. Extrair:
- Tech layer: backend/frontend/db/ui
- Priority/labels
- Acceptance criteria
- Related issues/PRs
Output JSON: {{layer: "backend", ac: [], deps: []}}"
```


### **Agent 2: Backend Specialist (Claude Sonnet)**

```
"EXPERT Node/Express/Prisma/Postgres. Stack fixa:
- /src/routes/{domain}.js
- prisma/schema.prisma migrations
- JWT middleware sempre
- Error handling padronizado

Issue layer BACKEND: {issue}
Gere APENAS: {{package_delta: {}, prisma_changes: {}, routes: {}, tests: {}}}

NUNCA frontend/UI."
```


### **Agent 3: Frontend Specialist (Claude Sonnet)**

```
"EXPERT React 18/Vite/MUI. Stack fixa:
- src/components/{Domain}Table.jsx (DataGrid)
- src/pages/{Domain}Page.jsx (Drawer + forms)
- Axios auth interceptors
- ThemeProvider sempre

Issue layer FRONTEND: {issue}
Gere APENAS: {{components: {}, pages: {}, hooks: {}}} 

NUNCA backend/DB."
```


### **Agent 4: Cross-Layer Reviewer (Gemini 1.5 Pro)**

```
"Senior Architect. Checklist por camada:
BACKEND: Prisma migrations/indexes/JWT
FRONTEND: MUI responsive/auth flows
INTEGRAÇÃO: Axios endpoints match

Backend changes: {backend}
Frontend changes: {frontend}

Layer-by-layer: APPROVED_BACKEND | REJECTED_BACKEND: [...]
                 APPROVED_FRONTEND | REJECTED_FRONTEND: [...]
Global: APPROVED | REJECTED"
```


### **Agent 5: Deployer (Determinístico)**

```python
# PyGithub puro - sem LLM
def deploy_node(state):
    repo.create_branch(f"auto-{issue}")
    repo.commit_files(state["code_files"])  # Parsed JSON
    pr = repo.create_pr(merge=True)  # Auto-merge se 2 APPROVED
    deploy_staging(pr)
    close_issue()
```


## 🔄 **LangGraph com Supervisor Router**

```python
def router(state) -> str:
    layers = state["issue_layers"]  # ["backend", "frontend"]
    if "backend" in layers: return "backend_agent"
    if "frontend" in layers: return "frontend_agent"
    return "reviewer"

workflow.add_conditional_edges("reader", router, {
    "backend_agent": "backend_agent",
    "frontend_agent": "frontend_agent", 
    "reviewer": "reviewer"
})
workflow.add_conditional_edges("reviewer", review_router, {...})
```


## 📊 **Vantagens Especialização**

| Agente | LLM | Foco | Custo/call |
| :-- | :-- | :-- | :-- |
| Reader | Gemini Flash | Context | \$0.0001 |
| Backend | Claude Sonnet | Node/DB | \$0.015 |
| Frontend | Claude Sonnet | React/UI | \$0.015 |
| Reviewer | Gemini Pro | Cross-check | \$0.002 |
| Deployer | None | Git ops | \$0 |
| **Total** |  |  | **\$0.032** |

## 🎯 **Exemplo Issue Multi-Layer**

```
Issue #42: "Users dashboard lento + sem paginação"

Reader → layers: ["backend", "frontend"]
↓ Parallel
Backend Agent → Prisma index + paginated API
Frontend Agent → MUI DataGrid pagination
↓
Gemini Reviewer → APPROVED_BACKEND + APPROVED_FRONTEND = DEPLOY ✅
```


## 📋 **State Expandido**

```python
{
  "issue_layers": List[str],     # ["backend", "frontend"]
  "backend_output": Dict,        # Isolado
  "frontend_output": Dict,       # Isolado  
  "layer_reviews": Dict[str, str], # {"backend": "APPROVED"}
  "global_status": "approved|rejected"
}
```

**5 agentes = precisão cirúrgica** por camada. Claude implementa **parallel execution** pros layers independentes.

**Copie essa spec pro Claude** — vai sair perfeito!

Quer nome pros agentes ou mais detalhes?

<div align="center">⁂</div>

[^1]: https://github.com/Brighton94/multi-agent-code-reviewer

[^2]: https://github.com/TheQuantScientist/Multi-Agent

