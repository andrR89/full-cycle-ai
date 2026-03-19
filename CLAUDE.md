# CLAUDE.md — AI Issue Solver Pipeline

Contexto completo do projeto para retomar o desenvolvimento sem perder estado.

---

## Propósito

Pipeline multi-agente que resolve issues do GitHub de ponta a ponta, **sem interação humana**:

1. Issue é criada e recebe label `ai-solve`
2. Pipeline classifica a issue (backend / frontend / ambos)
3. Gera código backend (Node/Express/Prisma) e/ou frontend (React/TS/MUI) em paralelo
4. Reviewer avalia o código; rejeita com feedback ou aprova
5. Deployer cria branch → commit único → PR → aguarda CI → auto-merge
6. Backend redeploya automaticamente no Koyeb; frontend é publicado no GitHub Pages

Repo pipeline: `andrR89/full-cycle-ai`
Repo de teste: `andrR89/test-ai-full-cycle`

---

## Stack de Hosting (free, sem cartão)

| Camada | Plataforma | Observação |
|--------|-----------|------------|
| Backend (Node.js) | Koyeb | Auto-deploy no push para main via Dockerfile |
| Frontend (React) | GitHub Pages | Deploy via `deploy-staging.yml` + Actions |
| Banco de dados | Supabase | PostgreSQL, variável `DATABASE_URL` no Koyeb |

URL backend live: `https://stiff-rook-arrghsoft-b9e8f305.koyeb.app`
URL frontend: `https://andrr89.github.io/test-ai-full-cycle/` (ainda não deployado com sucesso)

---

## Arquitetura de Agentes

```
Issue labeled `ai-solve`
        │
        ▼
[Agent 1 — Reader]         Gemini 2.0 Flash
  Classifica layers, extrai acceptance criteria, busca contexto do repo
        │
   ┌────┴────┐  (paralelo)
   ▼         ▼
[Agent 2]  [Agent 3]       Claude Sonnet 4.6 (max_tokens=16000)
Backend    Frontend
Node/      React/TS/MUI
Express/
Prisma
   └────┬────┘
        ▼
[Agent 4 — Reviewer]       Gemini 2.0 Flash
  Avalia código vs acceptance criteria
  APPROVED → Deployer
  REJECTED → retry (max 2x) → fecha issue com comentário
        │
        ▼
[Agent 5 — Deployer]       PyGithub puro (sem LLM)
  Cria branch
  Commit único via Git Tree API (InputGitTreeElement)
  Cria PR
  Aguarda CI (timeout 90s para aparecer, 5min total)
  Auto-merge se CI passa
```

---

## Arquivos principais

```
src/
├── main.py              # Entrypoint — lê env vars, monta estado, invoca grafo
├── graph.py             # LangGraph — define nós, edges, routers
├── state.py             # AgentState (TypedDict com todos os campos)
├── guardrails.py        # 4 camadas de segurança (injeção, path, código, contexto)
├── api.py               # FastAPI — Agent 0, endpoint POST /issues
└── agents/
    ├── reader.py        # Agent 1
    ├── backend.py       # Agent 2
    ├── frontend.py      # Agent 3
    ├── reviewer.py      # Agent 4
    ├── deployer.py      # Agent 5
    └── issue_creator.py # Agent 0 (usado pela api.py)

templates/               # Arquivos para copiar no repo-alvo
├── ci.yml               # CI: backend tests + frontend build/test
├── deploy-staging.yml   # Deploy: GitHub Pages (frontend) + Koyeb (backend)
├── trigger-ai-pipeline.yml  # Mini workflow que dispara o pipeline via repository_dispatch
└── koyeb.yaml           # Documentação de setup do Koyeb + Supabase

tests/                   # pytest, 100% mockado (sem chamadas reais de API)
```

---

## Secrets necessários

### Em `andrR89/full-cycle-ai` (repo do pipeline):
| Secret | Uso |
|--------|-----|
| `ANTHROPIC_API_KEY` | Backend + Frontend agents (Claude Sonnet) |
| `GEMINI_API_KEY` | Reader + Reviewer agents (Gemini Flash) |
| `GH_PAT` | PyGithub — criar branch/PR/merge no repo-alvo |

### Em `andrR89/test-ai-full-cycle` (repo-alvo):
| Secret | Uso |
|--------|-----|
| `PIPELINE_PAT` | trigger-ai-pipeline.yml → repository_dispatch para full-cycle-ai |
| `KOYEB_BACKEND_URL` | deploy-staging.yml → notify job (URL do backend) |

---

## Guardrails

Definidos em `src/guardrails.py`:

1. **Prompt injection** — trunca, detecta padrões, envolve em delimitadores
2. **Path traversal** — só permite prefixos em `backend/` e `frontend/`
3. **Código perigoso** — `eval`, `exec`, `DROP TABLE`, credenciais hardcoded, `rm -rf`
4. **Token overflow** — trunca contexto em 40k chars (~10k tokens)

Arquivos de teste (`.test.`, `.spec.`, `/tests/`) são isentos do scan de credenciais (senhas em testes são esperadas).

---

## Decisões arquiteturais importantes

- **Commit único via Git Tree API**: o deployer usa `InputGitTreeElement` + `create_git_tree` para fazer um único commit com todos os arquivos. Evita disparar N runs de CI.
- **Reviewer retorna string**: Gemini às vezes retorna `{}` no campo `reviewer_feedback`. O reviewer coerce para string antes de criar `ReviewerOutput`.
- **max_tokens=16000** nos agentes backend/frontend para evitar truncamento de JSON.
- **Dockerfile injetado automaticamente**: o deployer adiciona `backend/Dockerfile` e `frontend/Dockerfile` se não foram gerados, para que o Koyeb construa sem `package-lock.json`.
- **tsconfig exclui test files**: o prompt do frontend agent inclui um tsconfig exato que exclui `*.test.ts/tsx` do `tsc`, evitando falha no `vite build`.
- **Vitest, não Jest**: todos os testes frontend usam `vi.fn()`, `vi.mock()` — nunca `jest.*`.

---

## Flow de trigger (multi-repo)

```
test-ai-full-cycle issue labeled `ai-solve`
        │
        ▼ trigger-ai-pipeline.yml
repository_dispatch → full-cycle-ai (ai-issue-solver.yml)
        │
        ▼ python src/main.py
Pipeline roda com GITHUB_REPO=andrR89/test-ai-full-cycle
        │
        ▼
PR criado em test-ai-full-cycle
CI roda (ci.yml) → testes backend + build+testes frontend
Auto-merge se CI passa
        │
        ├── Backend → Koyeb redeploya automaticamente (watch main branch)
        └── Frontend → deploy-staging.yml → GitHub Pages
```

Para disparar manualmente:
```bash
gh api repos/andrR89/test-ai-full-cycle/dispatches \
  -f event_type=ai-issue-labeled \
  -f "client_payload[issue_number]=10" \
  -f "client_payload[repo]=andrR89/test-ai-full-cycle"
```

Para reverter o main do repo de teste e recriar:
```bash
# Revert do commit da IA mantendo os workflow fixes
cd /tmp/test-ai-full-cycle && git pull && git revert <sha-do-merge> --no-edit && git push

# Ou force reset para commit específico:
gh api --method PATCH repos/andrR89/test-ai-full-cycle/git/refs/heads/main \
  -f sha=<sha> -F force=true
```

---

## Estado atual (2026-03-19)

### O que está funcionando
- [x] Pipeline end-to-end: Reader → Backend + Frontend (paralelo) → Reviewer → Deployer
- [x] Commit único via Git Tree API (sem múltiplos CI runs)
- [x] Guardrails: path validation, dangerous code scan, prompt injection
- [x] Reviewer com retry (max 2x) e fechamento automático de issue
- [x] Auto-merge quando CI passa
- [x] Backend no Koyeb rodando (`/healthz` retorna `{"status":"ok"}`)
- [x] ci.yml válido (sem `hashFiles()` em `if:` de job)
- [x] deploy-staging.yml com `enablement: true` no configure-pages

### O que ainda não foi validado completamente
- [ ] **CI passando no PR gerado pela IA** — os fixes de Vitest/tsconfig foram aplicados mas o próximo run ainda está em execução
- [ ] **GitHub Pages publicando** — build passou mas configure-pages ainda falhou (fix do `enablement: true` aplicado, aguardando próximo run)
- [ ] **Teste de cobertura backend** — 80% de cobertura obrigatório (`--coverageThreshold`); depende da qualidade do código gerado

### Próximos passos sugeridos
1. Aguardar resultado do run atual e verificar se CI passa + Pages deploya
2. Se CI ainda falhar: checar logs dos jobs `backend` e `frontend` no repo de teste
3. Se Pages ainda falhar: verificar se o environment `github-pages` foi criado nas Settings do repo
4. Após validação completa: criar novas issues mais complexas para testar robustez
5. Considerar adicionar agente de database (migrations Prisma com conexão real ao Supabase)

---

## Comandos úteis

```bash
# Rodar testes do pipeline localmente
cd /Users/andre/Workspaces/full-cycle-ai
pytest tests/ -v

# Ver últimos runs no repo de teste
gh run list --repo andrR89/test-ai-full-cycle --limit 10

# Ver PRs abertos
gh pr list --repo andrR89/test-ai-full-cycle

# Verificar backend live
curl https://stiff-rook-arrghsoft-b9e8f305.koyeb.app/healthz

# Clonar repo de teste (se não existir localmente)
git clone git@github.com:andrR89/test-ai-full-cycle.git /tmp/test-ai-full-cycle
```
