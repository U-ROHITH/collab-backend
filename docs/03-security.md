# 03 — Security

No system is "unhackable". Our approach is **defense in depth**: several independent layers, so one bug doesn't become a breach, plus monitoring so we notice when something goes wrong. Treat every rule here as **mandatory** in code review.

## 1. What we protect (assets) and from whom (threats)

| Asset | Worst case | Main controls |
|-------|-----------|---------------|
| Money in escrow / payouts | Funds stolen, double payout, payout to the wrong account | Server-side amounts, verified webhooks, state machine, ledger, idempotency, admin MFA, reconciliation |
| Creator PII (PAN, bank, phone, address) | Data leak → DPDP penalties, fraud | Minimize (Razorpay holds bank/KYC), encrypt, mask, audit access |
| Accounts | Takeover, impersonation, fake admin | Supabase Auth, MFA for admin, no self-assigned admin, session revocation |
| Brand/creator business data (briefs, prices, messages) | Competitors read each other's data | Ownership checks on every query (anti-IDOR), authz test matrix |
| Social OAuth tokens | Hijack a creator's Instagram/YouTube | Server-side OAuth with state + PKCE, encrypted at rest, minimal scopes |
| Platform availability | DDoS, abuse, spam | Cloudflare, rate limits, queues, autoscaling |

Threat actors: an opportunistic attacker (scanners, credential stuffing), a malicious user (brand or creator trying to read others' data, manipulate prices, or game payouts), a compromised insider or admin account, and supply-chain attacks (npm).

## 2. Authentication

- **App-user identity is handled by Supabase Auth** (email OTP / Google; phone OTP later). We never store their passwords.
- A DRF authentication class (`SupabaseJWTAuthentication`, using PyJWT's `PyJWKClient`) verifies access tokens on every request: **signature via JWKS**, `iss`, `aud`, `exp`, `nbf`, and `algorithms=["RS256","ES256"]` pinned explicitly. `alg=none` and HS-confusion are rejected. (If the Supabase project still uses the legacy shared HS256 secret, migrate it to asymmetric signing keys first.)
- After verifying, load the **user from our DB** (role, status). The JWT only proves identity; **role and status come from our DB**, so a deactivated user is locked out immediately even if their token hasn't expired.
- Access token TTL ≤ 15 min, refresh rotation on (Supabase setting).
- **Staff/admin accounts** use Django auth for the Django admin, completely separate from app login:
  - `django-otp` TOTP 2FA **mandatory** (`OTPAdminSite`), strong password validators, `django-axes` lockout after repeated failures.
  - Admin lives at a non-default URL (`DJANGO_ADMIN_URL`), protected by **Cloudflare Access** (free for small teams) as a second layer.
  - Admin session: `SESSION_COOKIE_SECURE`, `HttpOnly`, `SameSite=Lax`, 8h max age; `CSRF_COOKIE_SECURE`.
  - Staff accounts get **least-privilege Django groups** (support / finance / superadmin). Only finance can trigger refunds/payouts.
- **Signup role:** `POST /auth/signup/complete` accepts only `role ∈ {brand, creator}` and **only once** (role is immutable after set). Staff can only be created by the `grant_admin` management command, which writes an audit log.

## 3. Authorization (the #1 risk area)

Three layers, all required:

1. **Role permission:** `permission_classes = [IsAuthenticated, IsBrand]` on the view.
2. **Ownership via scoped querysets + policies:** every view's `get_queryset()` starts from the **actor's** data, never `Model.objects.all()`:
   ```python
   # apps/orders/selectors.py
   def orders_visible_to(user) -> QuerySet[Order]:
       if user.role == Role.BRAND:
           return Order.objects.filter(brand__owner=user)
       if user.role == Role.CREATOR:
           return Order.objects.filter(creator__user=user)
       return Order.objects.none()

   # apps/orders/policies.py
   def can_approve(user, order) -> bool:
       return order.brand.owner_id == user.id and order.status == OrderStatus.DRAFT_SUBMITTED
   ```
   `get_object_or_404(orders_visible_to(user), pk=pk)` returns **404 rather than 403** when the actor isn't allowed, so we don't leak that the resource exists. A custom ruff/Semgrep rule flags `Model.objects.get(pk=` inside views.
3. **Authz test matrix:** for every endpoint, an automated test hits it as anonymous, as the wrong brand, as the wrong creator, as the right owner, and as admin, and asserts the expected codes. **CI fails if a new route has no matrix entry.**

Other rules:
- **Mass assignment:** DRF **silently ignores** unknown fields by default. Our `StrictSerializer` base raises `400` on any unknown key. Input serializers list writable fields explicitly. **Never use `fields = "__all__"`** (CI grep check). `PATCH /creators/me` can never set `approval_status`, `rating`, `verified`, `user`, and so on.
- **Separate input and output serializers.** Output serializers whitelist fields. A creator's public profile never includes phone, email, PAN, or payout info.
- **IDs:** UUIDv7 everywhere (non-guessable, sortable). Slugs are for display only, never for authorization.

## 4. Payments security

(Details in [04-payments-and-tax.md](04-payments-and-tax.md).)

- Amounts are **computed on the server** from the order row. The client never sends an amount.
- Payment signature verification: `HMAC_SHA256(razorpay_order_id + "|" + razorpay_payment_id, key_secret)`, compared with `hmac.compare_digest` (or the SDK's `client.utility.verify_payment_signature`).
- Webhook: a plain Django view (`@csrf_exempt`, no DRF auth, no parsers) verifies `X-Razorpay-Signature` over **`request.body` (raw bytes)** with the webhook secret. Reject if invalid. Dedupe by `x-razorpay-event-id` (unique constraint). Acknowledge 200 quickly, then process async.
- **The client callback (`/pay`) is only a UX hint.** It may move the order to "verifying", but `funded` is set only after a webhook or a server-side `GET /payments/:id` from Razorpay confirms `captured` and the amount and order id match.
- Payouts go to Razorpay **linked accounts** (KYC done by Razorpay). Bank-account changes require re-verification plus a **cooling period (e.g. 48h) before the next payout**, and send an alert email to the creator. This defends against account-takeover payout redirection.
- Admin refund/payout actions: MFA, reason required, audit logged, and amounts above a threshold need **a second admin to approve** (four-eyes).

## 5. Data access and Supabase-specific hardening

- **Clients never talk to Postgres directly.** Supabase auto-exposes tables through its Data API (PostgREST). Lock it down: put Django's tables in an unexposed schema `app` (`OPTIONS: {"options": "-c search_path=app"}`) **and** disable the Data API in project settings. In addition, **enable RLS on every table with no policies** (deny-all) via a migration, as a safety net.
- The React apps use Supabase **only for login** (anon/publishable key). The `service_role` key, DB passwords, and S3 keys are **never** shipped to any frontend.
- Django connects with a dedicated DB role with DML on `app` only. Migrations run with a separate role/connection.
- Storage buckets: `public-media` (profile/gallery, read via CDN) and `private` (deliverables, invoices, KYC docs, chat attachments). Private objects are accessed only via **signed URLs (≤ 5 min)** that the API issues after a policy check.
- All SQL goes through the ORM or parameterized `cursor.execute(sql, params)`. **`.extra()`, `RawSQL` with formatted strings, and f-strings in SQL are banned** (Bandit + Semgrep rules).

## 6. Input, output, and web hardening

- **Validation:** strict DRF serializers on every body, plus query-param serializers. Enforced `max_length`, `ChoiceField`s, numeric ranges (e.g. `price` 100–10,000,000 INR). URLs must be `https:` (custom `HttpsURLField`).
- **XSS:** the API stores text as-is; React escapes by default. **`dangerouslySetInnerHTML` is banned** unless the content goes through DOMPurify. Set CSP on Vercel (`vercel.json` headers).
- **SSRF:** the server never fetches user-supplied URLs. If we must later (link previews), do it through an egress proxy with a host allowlist that blocks private IP ranges.
- **Uploads:** presigned PUT with **content-type + max size pinned in the signature** (images 10 MB, docs 25 MB, video drafts 500 MB). After upload, a Celery task checks magic bytes, re-encodes images with **Pillow** (removes EXIF/GPS and polyglots; `Image.MAX_IMAGE_PIXELS` set against decompression bombs), and rejects anything else. SVG and HTML are never served from our domain.
- **CORS:** `django-cors-headers` with `CORS_ALLOWED_ORIGINS` set to the exact Vercel production domains. **Never `CORS_ALLOW_ALL_ORIGINS`**, and no wildcard `*.vercel.app` regex (anyone can deploy to vercel.app). Preview deployments point at staging only.
- **Django production settings:** `DEBUG=False`, `SECRET_KEY` from env, `ALLOWED_HOSTS` exact, `SECURE_PROXY_SSL_HEADER` (Render terminates TLS), `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS=31536000` + subdomains + preload, `SECURE_CONTENT_TYPE_NOSNIFF`, `X_FRAME_OPTIONS="DENY"`, `SECURE_REFERRER_POLICY`. `manage.py check --deploy` must pass in CI.
- **CSRF:** the API uses `Authorization: Bearer` headers (not cookies) for app users. Make sure DRF's `SessionAuthentication` is **not** in the default authentication classes for API views (it's only for the Django admin, which keeps CSRF on).
- **Errors:** generic client messages with an error `code` + `requestId` from a custom DRF exception handler. Stack traces, SQL errors, and provider errors go to logs and Sentry only. Django's debug 404/500 pages are never exposed.

## 7. Rate limiting and abuse

| Scope | Limit (start values, tune with data) |
|-------|------|
| Global per IP (Cloudflare) | 600 req/min |
| Auth/OTP (Supabase + Cloudflare) | 5/min per IP, 10/hour per phone/email |
| Per user, default | 120 req/min |
| Bids | 30/hour per creator |
| Offers | 50/day per brand |
| Messages | 30/min per user |
| Uploads (presign) | 60/hour per user |
| Admin money actions | 30/hour per admin |

Implemented with **DRF throttling** (`UserRateThrottle`, `AnonRateThrottle`, `ScopedRateThrottle`) backed by the Redis cache, plus Cloudflare rules at the edge. Behind Render's proxy, configure `NUM_PROXIES` so throttles see the real client IP, not the proxy. Responses: `429` + `Retry-After`. New accounts get lower limits for the first 7 days (anti-spam).

## 8. Secrets management

**Never paste real secret keys into chat, tickets, docs, or commits.** Anything pasted into a chat or an AI tool should be treated as exposed. Instead:

- **Local dev:** each developer keeps a `.env` (gitignored) created from `.env.example`. Use **test-mode / sandbox keys only** (Razorpay test keys, a dev Supabase project). If an AI assistant needs keys to run things, put them in the local `.env` file; the assistant can use them from there without anyone copying them into a conversation.
- **Staging/prod:** secrets live in **Render environment variables / environment groups** (backend) and **Vercel env vars** (frontend, publishable values only). Prod keys are created only for prod, and only 1–2 named owners can see them in the dashboards.
- **Frontend rule:** anything in a React bundle (`VITE_*` / `NEXT_PUBLIC_*`) is public. Only the Supabase URL + anon/publishable key, the Razorpay **key id**, the API URL, and the VAPID **public** key may go there.
- The app **validates all required env vars at startup** (`django-environ` + explicit checks in `production.py`) and refuses to boot if any are missing or malformed.
- **Prevent leaks:** `gitleaks` pre-commit hook + CI scan, GitHub secret scanning + push protection, and a structlog processor that redacts keys such as `authorization`, `token`, `secret`, `password`, `pan`, `account_number`. Sentry `send_default_pii=False`.
- **Rotation:** rotate immediately if a secret is ever exposed (chat, log, repo). Otherwise rotate on staff offboarding and at least yearly. Token encryption keys use versioned envelope encryption, so rotation doesn't require re-encrypting everything at once.

## 9. Privacy & compliance (DPDP Act 2023)

- Data stored in Singapore (Render/Supabase) for v1. This is currently allowed under DPDP; re-confirm with counsel and plan a move to India-region hosting if rules or customers require it.
- Collect the minimum: bank and KYC go to **Razorpay** (we store only the linked account id, PAN last 4, KYC status). If full PAN is required for TDS filing, store it encrypted (AES-256-GCM, app-level) and audit every read.
- Consent captured at signup (terms + privacy version stored). Users can export or delete their account. Financial and tax records are retained as legally required (typically 8 years), and everything else is anonymized on deletion.
- `audit_logs` (append-only): every admin action, every PII read by an admin, every payout/refund, every role change.

## 10. Supply chain and SDLC

- `uv.lock` committed; `pip-audit` (backend) and `npm audit` (React repos) + Dependabot; CI fails on high/critical issues.
- Bandit + Semgrep (Django ruleset) in CI. If we deploy with Docker: slim base image, non-root user, Trivy scan.
- Branch protection on `main`: PR review required, CI must pass, no force push.
- Pre-production: an OWASP ASVS L2 self-review, then an **external penetration test before launching real payments**.

## 11. Detection and response

- Alerts: spikes in 401/403/404 per user (IDOR probing), webhook signature failures, payout/refund anomalies, rate-limit storms, any admin login from a new location, reconciliation mismatches (page immediately).
- Incident runbook in `docs/runbooks/` (to be written in Phase 4): who to call, how to rotate keys, how to freeze payouts (`PAYOUTS_FROZEN=true` kill switch), how to revoke all sessions.

## 12. Security checklist for every PR

- [ ] New view has permission classes + scoped `get_queryset()` / policy check + authz matrix test
- [ ] Input serializer is a `StrictSerializer` with explicit fields; output serializer whitelists fields (no `__all__`)
- [ ] No amounts or prices trusted from the client
- [ ] State changes go through the state machine; side effects through the outbox
- [ ] No secrets, PII, or tokens in logs
- [ ] Migrations reviewed (constraints, indexes, no destructive change without a plan)
- [ ] Rate limit considered for any write endpoint
