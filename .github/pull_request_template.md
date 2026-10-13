## User story

code-corhuila/qone-docs#NN

## What changes and why

## How it was tested

- [ ] `ci.yml` green: ruff, pytest (domain, use cases, HTTP with the in-memory adapters, architecture)
- [ ] Integration tests against PostgreSQL run locally with `TEST_DATABASE_URL`

## Promotion trail

Only towards `qa` or `main`: the re-applied commits, each with its `(cherry picked from commit <sha>)` line.

## Checklist

- [ ] No secrets, no `.env`, no `*.pem`
- [ ] No schema change here: the structure lives in `qone-advisor-db`
- [ ] Contract `07-api/contracts/openapi/qone-advisor-api.yaml` respected
- [ ] `domain/` and `application/` import no framework or driver
