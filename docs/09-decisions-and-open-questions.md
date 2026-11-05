# 09 — Decisions & Open Questions

## Decisions (ADR log)

| # | Decision | Status | Rationale / revisit when |
|---|----------|--------|--------------------------|
| ADR-001 | Modular monolith: one Django project (API + Channels) + Celery worker | Accepted | Revisit when one app needs independent scaling or its own team |
| ADR-002 | **Python 3.13, Django 5.2 LTS + DRF**; React frontends | **Accepted** (founder decision, 2026-11-04) | |
| ADR-003 | Supabase for Postgres/Auth/Storage; Data API locked down; RLS deny-all | Accepted | |
| ADR-004 | Render (Singapore) for web + worker; Vercel for React; Supabase in Singapore | Accepted | Free tiers for dev/staging only; paid before launch (doc 01 §8). Revisit AWS Mumbai at scale |
| ADR-005 | Razorpay Route with on-hold transfers for escrow | **Pending Razorpay confirmation** | Regulatory |
| ADR-006 | Money as integer paise; versioned fee configs snapshotted per order | Accepted | |
| ADR-007 | Transactional outbox → Celery for all side effects | Accepted | |
| ADR-008 | Django Channels with single-use ticket auth (no JWT in WS URL) | Accepted | |
| ADR-009 | UUIDv7 ids; slugs for display only | Accepted | |
| ADR-010 | Response envelope `{success,data,error,meta}` | Proposed | Frontends must adapt if the original spec returns bare objects |
| ADR-011 | v1 ops panel = hardened Django admin (2FA, audit) instead of a React admin + `/admin/*` APIs | Proposed | Saves weeks; build a React admin when ops needs grow |
| ADR-012 | ~~Supabase Auth~~ → **Django-native OTP login**: phone OTP via 2Factor.in, email OTP via Resend (SMTP fallback); `simplejwt` access (15 min) + rotating refresh (7 d, blacklisted on rotation/logout). Staff use Django auth + TOTP. | **Accepted** (2026-11-04) | Matches the provider keys the founder supplied; one auth system, fully under our control |
| ADR-014 | Supabase project in **Mumbai (ap-south-1)**. Django tables stay in `public`, protected by **deny-all RLS enabled automatically after every `migrate`** (`apps/core/db.py`) | Accepted | The transaction pooler doesn't support a custom `search_path`. Disable the Data API in Supabase settings as a second layer |
| ADR-015 | New backend gets a **fresh Supabase project**; the old project (36 legacy tables) stays untouched as reference | Accepted | Founder decision, 2026-11-04 |
| ADR-013 | Celery + Redis for jobs; Celery beat for cron | Accepted | Needs a paid Render worker (no free workers) |

## Open questions (need answers from Rohith / business)

### Blocking Phase 1
1. **Original spec:** please share the brand-side spec (discovery, campaigns, orders, lists, dashboard, deliverables, reporting, cart, auth: about 46 endpoints) and the brand app repo.
2. **One person, two roles?** Can a user be both a brand and a creator?
3. **Brand teams:** single login per brand, or multiple members with roles?
4. **Creator approval:** can an unapproved creator browse campaigns? Bid? Receive offers?
5. **Messaging rule:** can brands message any creator, or only after a bid/offer/order exists?
6. ~~Auth methods~~: **decided**, phone + email OTP (ADR-012). Google sign-in can be added later.
6b. **New Supabase project:** create it (Mumbai), put its `DATABASE_URL` (transaction pooler, port 6543) in `backend/.env`, then run `uv run python manage.py migrate`.
6c. **Fee defaults in use until the CA confirms:** 10% commission paid by the brand, 18% GST on the fee, TDS 0% (`TDS_BPS` env). All overridable by env without code changes.
7. **Admin panel:** is the Django admin OK for v1 (ADR-011)?
8. **Budget:** OK with a small monthly spend (Render worker) from Phase 1, and the full paid setup before launch?

### Blocking Phase 2
9. **Commission bearer:** does the brand pay commission on top, or is it deducted from the creator? Rate?
10. **Razorpay:** have you spoken to Razorpay about Route / Escrow for this model? Business KYC done?
11. **CA sign-off:** TDS section (194J vs 194-O vs other) and rate, GST on fee, GST TCS, creator GST invoicing.
12. **Policies:** payment window (default 48h), auto-approve window (default 72h), max revisions (default 2), cancellation after the first draft, dispute SLA.
13. **Payout timing:** release on approval, or after a buffer (e.g. T+2)?

### Later
14. Usage rights: anything beyond recording the agreed terms?
15. Must-have emails at launch?
16. Launch date and team size?
