# qone-advisor-api

> advisor bounded context: service API

Part of the **Qampus (Quorum One)** distributed system — team `quorum-one`, Grupo 1.
Governance and documentation live in [`qone-docs`](https://github.com/code-corhuila/qone-docs).

## Purpose

The **AI advisor** of Qampus (course norm 5.1, Annex C): conversations, append-only messages and
the enrollment plans the agent proposes and the student confirms (AD-01..AD-08, contract
`qone-advisor-api.yaml`). The agent answers only from the advisor's own projections of catalog,
enrollment and billing (D-11), with the caller's identity (INV-ADV-002). It has no write tool:
its only side effect is starting the `enroll-student` saga through `qone-workflow` with the
advisor's service token, `origin: AGENT` and the student's confirmation (INV-ADV-001,
INV-ADV-003, INV-ENR-005, D-08). Without an API key, or with the circuit open, a reply is a fixed
message with `degraded: true` (D-02).

Specification: `09-microservices/services/06-advisor-api`, ADR-003, ADR-006, ADR-007, ADR-008.
Stories: `code-corhuila/qone-docs#46` (HU-ADV-002) and `#47` (HU-ADV-003).

## Layout (Annex C, hexagonal)

| Layer | What lives there |
|---|---|
| `src/advisor/domain` | conversation and message, the plan's state rule, the degree audit and the assessment of a section against the enrollment gates, events. No framework |
| `src/advisor/application` | ports and use cases; the agent's tools closed over the caller |
| `src/advisor/adapter` | HTTP (FastAPI), PostgreSQL, the LLM (LangChain and OpenAI), the saga client, the event consumer |
| `apps/` | the composition roots: the API and the consumer |

A test enforces the dependency rule: neither the domain nor the application imports a framework,
a driver or the LLM client.

## How to run it

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
ruff check .
python -m pytest                                   # domain, use cases, tools, LLM adapter, HTTP, contract, architecture
cp .env.example .env                               # JWT_PUBLIC_KEY (or _FILE), ADVISOR_DATABASE_URL, OPENAI_API_KEY, SERVICE_TOKEN
python -m apps.api                                 # http://localhost:8000/api/v1/advisor/health
python -m apps.consumer                            # the event consumer (ADVISOR_DATABASE_URL, RABBITMQ_URL)
```

Without `ADVISOR_DATABASE_URL` the service starts over an empty in-memory store (development
only; it says so in the log). Without `OPENAI_API_KEY` every reply is the fixed degraded message
with `degraded: true` and `201` (D-02); health says `llm: missing`. Without `SERVICE_TOKEN` a
confirmation answers `503`: the saga is started only with the advisor's own token (D-08).

Integration tests against the real schema and seeds run only when `TEST_DATABASE_URL` is set.
**They empty the conversation tables**: point them at a throwaway database, never at the
platform's. `ci.yml` clones `qone-advisor-db`, migrates a fresh PostgreSQL and runs them:

```bash
TEST_DATABASE_URL=postgresql://advisor:password@localhost:5438/advisor python -m pytest tests/test_postgres_integration.py tests/test_projections_integration.py
```

Container image and compose (`deploy/`, Annex G): `docker build -f deploy/Dockerfile -t
qone-advisor-api:dev .`, then `deploy/smoke.sh` against the running container; `ci.yml` builds
and smoke-checks the image. `deploy/compose.yml` is what `qone-infra` includes: the API with
`expose` only after `advisor-db` is healthy, and the consumer as a second process of the image.

Explicit limits (norm 5.3.10), declared in `apps/api/factory.py`: pool 10 connections with a
5 s wait, statement timeout 5 s, keep-alive 60 s, graceful shutdown 20 s, 120 s per reply across
every LLM round, breaker 3 failures / 60 s, 3 attempts to start the saga. Every log line is JSON
with the `traceId`. `scripts/sync-contracts.sh [ref]` refreshes the contract copies in
`contracts/openapi/`.

## Branching

Three permanent branches. **None of them accepts a direct commit** — you enter through a child
branch and leave through a Pull Request.

```
develop  <--PR--  feat/... fix/... chore/...
qa       <--PR--  qa/...
main     <--PR--  release/...  hotfix/...
```

Promotion happens **by re-application** (`git cherry-pick -x`), never by merging one permanent
branch into another: `merge develop -> qa` and `merge qa -> main` do not exist in this model.

`main` requires **1 approval from `ariel5253`**. On `develop` and `qa` the team sets its own review
rule.

Full policy: `00-governance/branching-policy.md` in `qone-docs`.
