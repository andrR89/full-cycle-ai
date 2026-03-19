# full-cycle-ai

Pipeline multi-agente que resolve issues do GitHub **do início ao fim, sem interação humana**. Marque uma issue com `ai-solve`, e o pipeline classifica o problema, gera código backend e/ou frontend, revisa sua própria saída, abre um pull request, aguarda o CI e faz o merge automaticamente.

---

## Como funciona

```
Issue labeled `ai-solve`
        │
        ▼
[Agent 1 — Reader]       Gemini Flash
  Classifica layers (backend/frontend)
  Extrai acceptance criteria
  Busca contexto do codebase
        │
   ┌────┴────┐  paralelo
   ▼         ▼
[Agent 2]  [Agent 3]     Claude Sonnet 4.6
Backend    Frontend
Node/      React/TS/MUI
Express/
Prisma
   └────┬────┘
        ▼
[Agent 4 — Reviewer]     Gemini Flash
  Avalia código vs acceptance criteria
  Aprova → Deployer
  Rejeita → retry (máx 2x) → fecha issue com feedback
        │
        ▼
[Agent 5 — Deployer]     PyGithub (sem LLM)
  Cria branch
  Commit único (Git Tree API)
  Cria PR com descrição rica
  Aguarda CI
  Auto-merge se passa
        │
   ┌────┴────┐
   ▼         ▼
Koyeb     GitHub Pages
Backend   Frontend
```

---

## Agentes

| # | Agente | Modelo | Responsabilidade |
|---|--------|--------|-----------------|
| 1 | Reader | Gemini 2.0 Flash | Classifica layers, extrai critérios, busca contexto do repo |
| 2 | Backend Specialist | Claude Sonnet 4.6 | Gera código Node.js / Express / Prisma (nunca frontend) |
| 3 | Frontend Specialist | Claude Sonnet 4.6 | Gera código React / TypeScript / MUI (nunca backend) |
| 4 | Reviewer | Gemini 2.0 Flash | Review cross-layer contra acceptance criteria; aprova ou rejeita com feedback acionável |
| 5 | Deployer | PyGithub | Cria branch, commit único, PR, CI polling, auto-merge |

---

## Stack de hosting (gratuito, sem cartão)

| Camada | Plataforma | Deploy |
|--------|-----------|--------|
| Backend (Node.js) | [Koyeb](https://koyeb.com) | Auto-deploy no push para main via Dockerfile |
| Frontend (React) | GitHub Pages | Deploy via Actions após merge |
| Banco de dados | [Supabase](https://supabase.com) | PostgreSQL gerenciado, `DATABASE_URL` no Koyeb |

---

## Setup

### 1. Secrets no repo do pipeline (`full-cycle-ai`)

Settings → Secrets and variables → Actions:

| Secret | Descrição |
|--------|-----------|
| `ANTHROPIC_API_KEY` | Chave Anthropic (Claude Sonnet) |
| `GEMINI_API_KEY` | Chave Google Gemini |
| `GH_PAT` | GitHub PAT com escopos `repo` e `workflow` |

### 2. Dependências Python

```bash
pip install -r requirements.txt
```

Requer Python 3.11+. Dependências principais: `langgraph`, `anthropic`, `google-genai`, `PyGithub`, `pydantic`, `tenacity`.

### 3. Configuração local

```bash
cp .env.example .env  # preencha as variáveis
```

---

## Uso: resolver issues de outro repositório

### Passo 1 — Copiar o workflow trigger para o repo-alvo

```bash
cp templates/trigger-ai-pipeline.yml .github/workflows/trigger-ai-pipeline.yml
cp templates/ci.yml                  .github/workflows/ci.yml
cp templates/deploy-staging.yml      .github/workflows/deploy-staging.yml
```

### Passo 2 — Secrets no repo-alvo

| Secret | Descrição |
|--------|-----------|
| `PIPELINE_PAT` | PAT com acesso ao repo `full-cycle-ai` (dispara o pipeline) |
| `KOYEB_BACKEND_URL` | URL do backend no Koyeb (para o notify do deploy) |

### Passo 3 — Configurar Koyeb e Supabase

Ver instruções em [`templates/koyeb.yaml`](templates/koyeb.yaml).

### Passo 4 — Habilitar GitHub Pages

Settings → Pages → Source: **GitHub Actions**

### Como funciona

Quando uma issue recebe o label `ai-solve`:

1. `trigger-ai-pipeline.yml` envia um `repository_dispatch` para `andrR89/full-cycle-ai` com o payload da issue
2. O pipeline roda contra o **repo-alvo**
3. O Deployer abre um PR no **repo-alvo**
4. O CI (`ci.yml`) valida o código gerado
5. Após merge, o `deploy-staging.yml` publica o frontend no GitHub Pages; o Koyeb redeploya o backend automaticamente

---

## Guardrails de segurança

Quatro camadas aplicadas antes de qualquer código chegar ao GitHub:

| Guardrail | Função | O que bloqueia |
|-----------|--------|----------------|
| Sanitização de prompt | `sanitize_prompt_input()` | Injeção de prompt, null bytes, trunca entradas > 10k chars |
| Validação de paths | `validate_file_path()` | Path traversal (`../../`), caminhos absolutos, arquivos sensíveis (`.env`, `.pem`, `id_rsa`) |
| Filtro de arquivos | `validate_and_filter_files()` | Rejeita silenciosamente arquivos fora dos diretórios permitidos |
| Scan de código | `scan_generated_code()` | `eval()`, `exec()`, `os.system()`, `subprocess(shell=True)`, `DROP TABLE`, credenciais hardcoded |

Prefixos de path permitidos:
- **Backend**: `backend/src/`, `backend/prisma/`, `backend/tests/`, `backend/package.json`, `backend/Dockerfile`, `backend/jest.config.*`
- **Frontend**: `frontend/src/`, `frontend/public/`, `frontend/tests/`, `frontend/package.json`, `frontend/index.html`, `frontend/vite.config.*`, `frontend/tsconfig*.json`, `frontend/Dockerfile`

Arquivos de teste são isentos do scan de credenciais (senhas em testes são esperadas).

---

## Desenvolvimento e testes

```bash
# Rodar todos os testes (sem chamadas reais de API — 100% mockado)
pytest

# Com cobertura
pytest --cov=src --cov-report=term-missing

# Arquivo específico
pytest tests/test_guardrails.py -v
```

### Estrutura de testes

```
tests/
├── test_guardrails.py      # 4 funções de guardrail
├── test_graph.py           # layer_router e review_router
├── test_main.py            # build_initial_state, validate_environment
└── agents/
    ├── test_reader.py      # classificação, fallback, output
    ├── test_backend.py     # geração, layer gating, output
    ├── test_reviewer.py    # aprovação/rejeição, retry, output
    └── test_deployer.py    # coleta de arquivos, branch/PR, rejeição
```

### Rodar o pipeline localmente

```bash
export ANTHROPIC_API_KEY=...
export GEMINI_API_KEY=...
export GH_PAT=...
export GITHUB_REPO=owner/repo
export ISSUE_NUMBER=42
export ISSUE_TITLE="Add login page"
export ISSUE_BODY="Users need to authenticate..."
export ISSUE_URL="https://github.com/owner/repo/issues/42"

python src/main.py
```

---

## Estimativa de custo por run

| Agente | Modelo | Custo estimado |
|--------|--------|---------------|
| Reader | Gemini 2.0 Flash | ~$0.001 |
| Backend | Claude Sonnet 4.6 | ~$0.075 |
| Frontend | Claude Sonnet 4.6 | ~$0.075 |
| Reviewer | Gemini 2.0 Flash | ~$0.002 |
| **Total (1 tentativa)** | | **~$0.15** |
| **Total (2 retries, pior caso)** | | **~$0.45** |
