# 08 — Implementation Roadmap

Each phase ends with something **deployable to staging and demoable**. Don't start a phase until the previous phase's "done when" is green.

## Phase 0: Foundation (week 1)

- [ ] `backend/` Django 5.2 project with uv, settings split, `django-environ`, `config/celery.py`, `asgi.py` with Channels
- [ ] `apps/core`: UUIDv7 base model, `StrictSerializer`, envelope renderer, exception handler + error codes, cursor pagination, throttles, `Idempotency-Key` decorator, `OutboxEvent` + relay task, AES-GCM helper
- [ ] `apps/identity`: custom `User` model (**must exist before the first migration**), `SupabaseJWTAuthentication`, role permissions, `grant_admin` management command, `AuditLog`
- [ ] Django admin hardened: non-default URL, `django-otp` 2FA, staff-only
- [ ] structlog + Sentry; `/healthz`, `/readyz`
- [ ] drf-spectacular schema; generated TS client job for the React repos
- [ ] pytest setup, factories, **authz-matrix test helper**
- [ ] docker-compose (Postgres + Redis) for local
- [ ] GitHub Actions CI (ruff, mypy, import-linter, migrations check, pytest + coverage, bandit, semgrep, pip-audit, gitleaks, check --deploy)
- [ ] Render staging (web; worker when affordable), Supabase staging project with Data API locked down

**Done when:** `GET /v1/me` with a Supabase token works on staging, and CI blocks a PR that has a failing authz test or a committed secret.

## Phase 1: Marketplace core, no money (weeks 2–4)

- [ ] Signup completion (brand/creator), brand profile, creator profile CRUD, packages
- [ ] Presigned uploads + Pillow processing task
- [ ] Ops (Django admin): creator approval, catalogue creation, deactivate users
- [ ] Discovery: search + filters + creator detail + reviews read
- [ ] Lists, cart
- [ ] Campaigns + usage rights; bids (create/update/withdraw/select)
- [ ] Offers (send/accept/reject/expire)
- [ ] Orders + `OrderStateMachine` (non-payment transitions), deliverables, revisions
- [ ] In-app notifications
- [ ] Merge the brand-side "original spec" endpoints (dashboard, reporting)
- [ ] **Start Meta App Review** for the Instagram Graph API (it takes weeks)

**Done when:** the brand→creator flow works end to end on staging up to `awaiting_payment`, and every endpoint has authz matrix tests.

## Phase 2: Money (weeks 5–7), *blocked on Razorpay + CA confirmation*

- [ ] `apps/money` with fee configs, 100% tests
- [ ] Creator payout onboarding (Route linked accounts, KYC webhooks)
- [ ] Payment intent → checkout → webhook → funded
- [ ] Ledger + webhook events + reconciliation task
- [ ] Approve → release transfer; auto-approve task
- [ ] Cancel/refund + transfer reversal; disputes + admin resolution
- [ ] Admin payout/refund actions with 2FA + four-eyes + audit
- [ ] GST invoice numbering + PDF; TDS records + statement
- [ ] Reviews after completion

**Done when:** a full E2E in Razorpay test mode passes, reconciliation shows zero mismatches over 100 simulated orders including duplicate/out-of-order webhooks, and an internal security review of `apps/payments` is done.

## Phase 3: Engagement (weeks 8–9)

- [ ] Conversations + messages + Channels consumers (ticket auth)
- [ ] Email (Resend templates, preferences) + web push (`pywebpush`, VAPID)
- [ ] Social OAuth (Instagram Graph, YouTube) + daily sync
- [ ] Brand dashboard + campaign reporting

## Phase 4: Hardening & launch (weeks 10–11)

- [ ] **Move production off free tiers** (see doc 01 §8)
- [ ] Load test, query tuning, caching
- [ ] ZAP scan + **external pentest**, fix findings
- [ ] Runbooks (incident, key rotation, payout freeze, restore), alerts
- [ ] Backup restore drill
- [ ] DPDP: privacy policy, consent, export/delete
- [ ] Production cutover: prod secrets in Render env (secret files) only, Razorpay live keys, final checklist

Timelines assume about 2 backend engineers. Adjust once the team is known.
