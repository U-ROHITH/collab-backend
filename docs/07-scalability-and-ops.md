# 07 — Scalability & Operations

## 1. Capacity assumptions (Year 1, design for 10× headroom)

| Metric | Year-1 estimate | Design headroom |
|--------|-----------------|-----------------|
| Creators | 50k | 500k |
| Brands | 5k | 50k |
| Peak API RPS | ~100 | ~1,000 |
| Concurrent WS connections | ~2k | ~20k |
| Messages/day | ~50k | ~500k |
| Orders/day | ~500 | ~5k |

These are modest numbers for Django + Postgres. The design keeps us from **painting ourselves into a corner**, not from handling large load on day one.

## 2. How each layer scales

| Layer | Strategy | When to act |
|-------|----------|-------------|
| Django web | Stateless; add Render instances; tune gunicorn workers (≈ 2×CPU+1) | p95 > 300 ms or CPU > 60% |
| WebSockets | Channels + Redis channel layer works across instances; split into a separate `realtime` service when needed | > ~3k connections per instance, or WS load slows HTTP |
| Celery | Separate queues (`default`, `payments`, `notifications`, `media`, `sync`) and separate worker processes for `payments` so they're never stuck behind bulk email | Queue age > 1 min |
| Postgres | Indexes first → Supabase connection pooler (Supavisor) → read replica (paid) for discovery/reporting → partition `messages`/`notifications`/`audit_logs` by month | p95 query > 50 ms, connections near the limit, rows > 50M |
| Search | Postgres FTS (`django.contrib.postgres`) → Typesense/Meilisearch | Catalogue > 200k or relevance needs |
| Reporting | Materialized views refreshed by Celery beat → warehouse later | Dashboards slow the primary |
| Files | Direct-to-storage uploads, CDN for public media | Already scalable |
| Hosting | Render → AWS (ECS/RDS, Mumbai) if cost or latency demands it | Revisit at ~$500/month spend or Indian latency complaints |

### Django + Supabase connection notes
- The web service uses the **transaction-mode pooler** (port 6543). Set `DISABLE_SERVER_SIDE_CURSORS = True` and `CONN_MAX_AGE = 0` (or small with health checks).
- **Migrations use the direct/session connection** (port 5432) via a separate `DATABASE_MIGRATION_URL`.
- `select_for_update()` works with the transaction pooler because it runs inside `atomic()`.

## 3. Caching

| What | Where | TTL | Invalidation |
|------|-------|-----|--------------|
| Public creator profile | Redis | 5 min | `creator.updated` event |
| Discovery result pages | Redis (key = normalized filters) | 60 s | TTL only |
| Supabase JWKS | Process memory | 10 min | refetch on unknown `kid` |
| User role/status by auth id | Redis | 60 s | on role/status change |
| Public images | Supabase CDN / Cloudflare | long, content-hashed keys | new key per upload |

**Never cache:** anything money-related, order state, authz decisions beyond the 60s user status.

## 4. Performance rules

- **No N+1:** every list selector uses `select_related` / `prefetch_related`. Tests use `django_assert_max_num_queries` on list endpoints.
- Every list endpoint uses cursor pagination with a hard cap. No unbounded querysets.
- Long work (PDFs, external APIs, emails, image processing) **never** runs in the request path. Use Celery.
- DB `statement_timeout` = 5 s for the web role (set in connection `OPTIONS`), longer for the worker.
- New queries on big tables need an `EXPLAIN ANALYZE` in the PR.
- Async views only where they help (e.g. calling external APIs). Default to sync DRF views; don't mix sync ORM calls into async code without `sync_to_async`.

## 5. Observability

- **Logs:** `structlog` JSON to stdout (Render captures it): `request_id`, `user_id`, `route`, `status`, `latency_ms`. PII and secrets redacted by a processor. Optional: ship to Better Stack (free tier).
- **Errors:** Sentry (Django + Celery integrations), `send_default_pii=False`, release tags.
- **Uptime:** Better Stack / UptimeRobot (free) on `/healthz` and `/readyz`.
- **Business metrics:** a staff dashboard (Django admin page) showing the payment funnel, stuck orders, queue lengths, failed webhooks, reconciliation status.
- **Alerts (to email/Slack):** 5xx spikes, webhook signature failures, reconciliation mismatch, payout/refund failure, Celery dead tasks, DB storage > 80%.

## 6. Environments & config

| Env | Hosting | Data | Payments |
|-----|---------|------|----------|
| local | `docker compose` (Postgres, Redis) + `runserver`/`uvicorn` + Celery; or a dev Supabase project | seed data | Razorpay **test** |
| staging | Render free/starter + Supabase free + Vercel preview | synthetic only, **never prod copies** | Razorpay **test** |
| production | Render paid + Supabase Pro + Vercel Pro | real | Razorpay **live** |

- Settings split: `config/settings/{base,local,test,production}.py`. Env read with **`django-environ`**. Production refuses to boot if required vars are missing, and asserts `DEBUG=False`, a strong `SECRET_KEY`, and that `ALLOWED_HOSTS` is set.
- Run `python manage.py check --deploy` in CI against production settings.
- Feature flags / kill switches as env vars: `PAYOUTS_FROZEN`, `MESSAGING_ENABLED`, `SOCIAL_SYNC_ENABLED`.

## 7. CI/CD (GitHub Actions)

```
PR:      uv sync --frozen → ruff → mypy → import-linter → makemigrations --check
         → pytest (Postgres + Redis service containers) incl. authz matrix → coverage ≥ 80%
         → bandit → semgrep → pip-audit → gitleaks → manage.py check --deploy
main:    Render auto-deploys staging (pre-deploy: migrate) → smoke tests → Playwright E2E
release: manual promote to production (Render deploy hook) → smoke → rollback on failure
```

Migrations follow **expand → migrate → contract**: never drop or rename a column in the same release that stops using it.

## 8. Backups & disaster recovery

- **Supabase free has no automatic backups.** Until on Pro: a nightly GitHub Actions job runs `pg_dump`, encrypts it with `age`/GPG, and uploads it to a private bucket in a **different** provider. Test a restore monthly.
- Supabase Pro: daily backups; add the PITR add-on before payments go live.
- Storage: keep invoices/deliverables in the private bucket; mirror invoices in the nightly backup.
- Targets (on Pro): RPO ≤ 24 h → ≤ 5 min with PITR, RTO ≤ 4 h.
- Razorpay is the system of record for money movement; payment state can be rebuilt from its API.

## 9. Testing strategy

| Level | Tooling | Must cover |
|-------|---------|-----------|
| Unit | pytest | `apps/money` (100%), state machine (every row of the transition table), policies |
| Integration | pytest-django + DRF `APIClient` + factory_boy | every endpoint: happy path, validation, **authz matrix**, idempotency, query counts |
| Contract | drf-spectacular schema diff in CI | no accidental breaking changes for the React apps |
| E2E | Playwright (in the frontend repos) against staging | signup → campaign → bid → select → pay (test) → deliver → approve → payout → review |
| Webhooks | Recorded Razorpay payload fixtures | duplicates, out-of-order, bad signatures |
| Load | k6 or Locust before launch | discovery, messaging, checkout at 3× expected peak |
| Security | Bandit, Semgrep, ZAP baseline on staging, external pentest pre-launch | |
