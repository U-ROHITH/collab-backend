# Backend Design Docs

Backend for a creator marketplace in India. Brands discover creators, run campaigns, and send offers; creators bid, deliver, and get paid through escrow. One API serves the **brand app** and the **creator app**, and the ops team uses a hardened **Django admin**.

**Stack:** Python 3.13 · Django 5.2 LTS + DRF · Django Channels · Celery + Redis · Postgres/Auth/Storage on Supabase · API on Render · React apps on Vercel · Razorpay.

## Reading order

| # | Doc | Read it to learn |
|---|-----|------------------|
| 1 | [01-architecture.md](01-architecture.md) | The big picture: components, tech stack, repo layout, deployment |
| 2 | [02-system-design.md](02-system-design.md) | Modules, core flows, the order state machine, messaging, jobs |
| 3 | [03-security.md](03-security.md) | Threat model and the security rules every engineer must follow |
| 4 | [04-payments-and-tax.md](04-payments-and-tax.md) | Escrow on Razorpay Route, the ledger, money math, GST/TDS |
| 5 | [05-data-model.md](05-data-model.md) | Tables, keys, indexes, constraints |
| 6 | [06-api-guidelines.md](06-api-guidelines.md) | API conventions and the required changes to the original spec |
| 7 | [07-scalability-and-ops.md](07-scalability-and-ops.md) | Scaling, caching, observability, environments, backups |
| 8 | [08-roadmap.md](08-roadmap.md) | Build order, phase by phase, with done criteria |
| 9 | [09-decisions-and-open-questions.md](09-decisions-and-open-questions.md) | Decisions made (ADR log) and questions still open |

## The 10 rules that matter most

If you remember nothing else, remember these:

1. **Never trust the client for money.** The server always computes amounts from the DB. Clients send IDs, never prices.
2. **Every resource query is scoped to the caller.** `/orders/:id` must check that the caller is the order's brand, its creator, or an admin. IDOR is our #1 risk.
3. **Roles are never self-assigned beyond `brand` / `creator`.** `admin` is only granted via an internal CLI, and needs MFA.
4. **Payment state only changes from verified sources:** a signature-verified Razorpay webhook or a server-side fetch from Razorpay, never a client callback alone.
5. **Every money operation is idempotent and recorded in the append-only ledger.**
6. **Order status changes only through the state machine** (`OrderStateMachine.transition`), inside a transaction with a row lock.
7. **Secrets live in env and a secret manager, never in git, chat, or logs.** Each environment has its own keys.
8. **Validate every input with a `StrictSerializer`** (DRF ignores unknown fields by default; ours rejects them) and **whitelist every output field** (never `fields = "__all__"`).
9. **Side effects go through the outbox.** Notifications, emails, WebSocket pushes, and payouts are async, retried, and idempotent.
10. **PII is minimized, encrypted at rest where sensitive, masked in responses, and access is audited.**

## Status

- Docs: v0.1 (2026-11-04). This is the design baseline; the brand-side "original spec" still needs merging in (see open questions).
- Code: not started.
