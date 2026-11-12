# API Reference (v1): what's built

Live base URL: **https://collab-api-ewju.onrender.com**

Interactive docs: run the server and open **`/v1/docs/`** (Swagger, dev only). Raw schema: `/v1/schema/`.
React apps should generate types from the schema: `npx openapi-typescript http://localhost:8000/v1/schema/ -o src/api/schema.ts`.

## Changes on 2026-11-12 (passwords)
- **Password sign-in:** `POST /v1/auth/login/` `{identifier, password}` → `{access, refresh, user}`. `identifier` is an email (any casing) or a mobile number (`9876543210`, `+91 98765 43210`, `098765-43210`). A wrong password, an unknown account, an account that never set a password, and a suspended account all return the same `401 AUTH_INVALID`. After 10 wrong attempts on one identifier it returns `429 RATE_LIMITED` for 15 minutes; suggest signing in with a code.
- **Password at signup:** `/v1/auth/signup/complete/` accepts optional `password` + `confirmPassword`. Rules: 8+ characters, not all digits, not a common password, not too close to the name/email. Errors come back as `400 VALIDATION_ERROR` with `error.details.password` or `error.details.confirmPassword` (lists of messages) so the form can show them under the field. Signup without a password still works; that account signs in with a code or Google.
- **Forgot / change password:** there's no emailed reset link. Request a code with `/v1/auth/otp/request/` as usual, then `POST /v1/auth/password/reset/` `{challengeId, code, password, confirmPassword}` → `{access, refresh, user}`. Same password rules and error shape as signup; a bad code is `400 OTP_INVALID` (password errors are checked first, so a typo doesn't burn the code). This also lets a code-only account add a password, and it clears a password lockout. **Every other session is signed out (their access tokens lapse within 15 minutes); store the returned tokens.** A signed-in token alone can't change the password, so a leaked token can't lock the owner out.

## Changes on 2026-11-09 (launch requests)
- **Guest view:** `GET /v1/creators/`, `GET /v1/creators/{id}/` and `GET /v1/campaigns/` work without a token. Creator cards never include contact, payout or approval details. Guests (and users mid-signup or awaiting creator approval) get open campaigns with `brief: null` and `usageRights: null`. Campaign detail, bids and every write still need a signed-in user.
- **Google sign-in:** `POST /v1/auth/google/` `{idToken, role?}` → `{access, refresh, user}`, the same shape as OTP verify. `idToken` is the Google Identity Services credential for client `1042168589486-k7e30v1tao2ooepsb5lp339m7anlvq44.apps.googleusercontent.com`. Accounts match on the Google-verified email, so an existing OTP account signs in to the same account. `role` (`brand`/`creator`) only applies to an account with no role yet; otherwise read `user.role`. Errors: `401 GOOGLE_TOKEN_INVALID`, `401 GOOGLE_EMAIL_UNVERIFIED`.
- **KYC:** `user.kycStatus` on `/v1/auth/me/` (and every auth response) is `not_started | in_progress | pending | verified | rejected`. Steps are under `/v1/kyc/` (below). **Creating a campaign, placing a bid, sending an offer, accepting an offer and cart checkout return `403 KYC_REQUIRED` until KYC is `verified`.** Declining an offer doesn't need KYC.
- **Earnings:** `GET /v1/creators/me/earnings/` → `{inEscrow, earned, tdsDeducted, ordersInEscrow, ordersEarned, payoutsEnabled}`. **`payoutsEnabled` is `false` for launch: hide withdraw/payout-history screens.** "Earned" means the brand approved the work. Money doesn't reach a bank until Razorpay payouts are built.
- **Fix:** a bad or expired refresh token now returns `401 AUTH_INVALID` with "Your session has expired or is invalid. Please sign in again." (no Python debug text).

## Changes on 2026-11-05 (frontend feedback)
- **Blind bidding:** creators get `brand: null` on campaigns (`GET /v1/campaigns/`, `/v1/campaigns/{id}/`). The brand is revealed on the order once a bid is selected.
- **Date only:** `closesAt` (campaigns), `createdAt` (campaigns, bids, offers), `deadline` (offers, orders) are `YYYY-MM-DD`. Send `closesAt`/`deadline` as `YYYY-MM-DD`; they mean "end of that day, India time".
- **Both sides see profiles:** `creator` on orders/offers/bids is a full card (`displayName, avatarUrl, bio, categories, location, primaryPlatform, repliesWithin, verified, followerCount, rating, reviewCount`). `brand` on orders/offers is `{id, name, logoUrl, industry, website, city}`. Conversations include `withParty.profile`. Phone and email are never shared.
- `GET /v1/brands/{id}/`: brand profile card. Creators can open it once they have an offer, order or conversation with that brand.
- **Message calendar:** each message has `sentDate` (`YYYY-MM-DD`, IST) and `sentTime` (`HH:MM`, IST). `GET /v1/conversations/{id}/messages/?date=YYYY-MM-DD` loads one day. `GET /v1/conversations/{id}/dates/?month=YYYY-MM` returns `[{date, count}]` for days with messages.

## Conventions
- Auth header: `Authorization: Bearer <access>` (15-minute access token; refresh with `/v1/auth/token/refresh/`).
- Every response: `{ "success", "data", "error", "meta" }`. Errors: `error.code` (stable, switch on it), `error.message`, `error.requestId`, plus `error.details` (field errors) on validation failures.
- Money: integer **paise**, e.g. `{ "amount": 1118000, "currency": "INR" }`. Request fields like `price`/`budget` are paise too.
- Lists: cursor pagination with `?limit=` (max 50) and `?cursor=` from `meta.nextCursor`.
- Unknown request fields are rejected with `400` (mass-assignment protection).
- If something isn't yours, you get `404`, not `403`.

## Auth: phone/email OTP (2Factor.in SMS, Resend email)
| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/v1/auth/otp/request/` | anyone | `{channel: "sms"\|"email", destination}` → `{challengeId, expiresIn}`. 5/min per IP, 5/hour per destination |
| POST | `/v1/auth/otp/verify/` | anyone | `{challengeId, code}` → `{access, refresh, user}`. 5 attempts per code; single use |
| POST | `/v1/auth/google/` | anyone | `{idToken, role?}` → `{access, refresh, user}`. `role` only used on first sign-in |
| POST | `/v1/auth/login/` | anyone | `{identifier, password}` → `{access, refresh, user}`. 10/min per IP; 10 wrong passwords per identifier locks it for 15 min |
| POST | `/v1/auth/password/reset/` | anyone | `{challengeId, code, password, confirmPassword}` → `{access, refresh, user}`. Code from `/otp/request/`; signs out other sessions |
| POST | `/v1/auth/token/refresh/` | anyone | `{refresh}` → `{access, refresh}` (rotates; the old token is blacklisted) |
| POST | `/v1/auth/logout/` | user | `{refresh}` → 204 |
| POST | `/v1/auth/logout-all/` | user | revokes every refresh token |
| GET | `/v1/auth/me/` | user | includes `needsOnboarding`, `profileId`, `kycStatus` |
| POST | `/v1/auth/signup/complete/` | user | `{role: "brand"\|"creator", fullName, brandName?, displayName?, password?, confirmPassword?}`, once only. `admin` is impossible here |

## Creators
| Method | Path | Who |
|---|---|---|
| GET/PATCH | `/v1/creators/me/` | creator (`displayName, bio, categories[], location, primaryPlatform, repliesWithin`) |
| GET/POST | `/v1/creators/me/packages/` | creator (`title, platform, deliverables, price, deliveryDays`) |
| PATCH/DELETE | `/v1/creators/me/packages/{id}/` | creator (DELETE = deactivate) |
| GET | `/v1/creators/?q=&category=&location=&platform=&verified=&minPrice=&maxPrice=&minFollowers=` | **public**: approved creators only |
| GET | `/v1/creators/{id}/` | **public**: profile + active packages |
| GET | `/v1/creators/me/earnings/` | creator: `{inEscrow, earned, tdsDeducted, ordersInEscrow, ordersEarned, payoutsEnabled}` |
| GET | `/v1/creators/{id}/reviews/` | brand/creator |

## Brands, lists, cart
| Method | Path | Who |
|---|---|---|
| GET/PATCH | `/v1/brands/me/` | brand (`name, website, industry, legalName, gstin, stateCode, billingAddress`) |
| GET/POST | `/v1/lists/` | brand |
| GET/PATCH/DELETE | `/v1/lists/{id}/` | brand |
| POST | `/v1/lists/{id}/creators/` `{creatorId}` | brand |
| DELETE | `/v1/lists/{id}/creators/{creatorId}/` | brand |
| GET/POST | `/v1/cart/` `{packageId, brief}` | brand |
| DELETE | `/v1/cart/{itemId}/` | brand |
| POST | `/v1/cart/checkout/` `{usageRights}` | brand → one order per item; prices come from the server |

## Campaigns & bids
| Method | Path | Who |
|---|---|---|
| GET | `/v1/campaigns/` (alias `/v1/creators/me/campaigns/`) | brand: its own; approved creator: open ones; **public** (anyone else): open ones without `brief`/`usageRights` |
| POST | `/v1/campaigns/` | brand (`title, brief, platform, deliverables, budgetMin, budgetMax, closesAt, status?, usageRights`) |
| GET/PATCH | `/v1/campaigns/{id}/` | brand owner (GET also for approved creators if open) |
| POST | `/v1/campaigns/{id}/close/` | brand owner (pending bids → rejected) |
| GET | `/v1/campaigns/{id}/bids/` | brand owner: all; creator: only its own |
| POST | `/v1/campaigns/{id}/bids/` | approved creator (`price, message, expectedViews?, deliveryDays`), one per campaign |
| PATCH | `/v1/campaigns/{id}/bids/{bidId}/` | bid owner, while pending |
| POST | `/v1/campaigns/{id}/bids/{bidId}/withdraw/` | bid owner |
| POST | `/v1/campaigns/{id}/bids/{bidId}/select/` | brand owner → creates an order |

## Offers
| Method | Path | Who |
|---|---|---|
| GET/POST | `/v1/offers/` | brand (`creatorId, deliverable, budget, deadline, message, usageRights`) |
| POST | `/v1/offers/{id}/withdraw/` | brand |
| GET | `/v1/creators/me/offers/?status=` | creator |
| PATCH | `/v1/creators/me/offers/{id}/` `{status: "accepted"\|"rejected"}` | creator → `{offer, order}` |

## Orders
`allowedActions` on each order tells the UI which buttons to show for the current user.

| Method | Path | Who |
|---|---|---|
| GET | `/v1/orders/?status=` (alias `/v1/creators/me/orders/`) | parties |
| GET | `/v1/orders/{id}/` | parties: amounts (subtotal, platformFee, gst, tdsDeducted, creatorPayout, brandTotal), usageRights |
| GET/POST | `/v1/orders/{id}/deliverables/` | parties / creator (`contentUrl` https only, `note`) |
| POST | `/v1/orders/{id}/revisions/` `{feedback}` | brand (max 2) |
| POST | `/v1/orders/{id}/approve/` | brand |
| POST | `/v1/orders/{id}/cancel/` `{reason}` | parties (rules in the state machine) |
| POST | `/v1/orders/{id}/disputes/` `{reason}` | parties |
| POST | `/v1/orders/{id}/review/` `{stars, text}` | brand, once, after `completed` |
| POST | `/v1/dev/orders/{id}/simulate-payment/` | **dev only** (`DEBUG` + `DEV_ENDPOINTS_ENABLED`): marks the order funded until Razorpay is wired in |

Status flow: `awaiting_payment → funded → draft_submitted ⇄ revision_requested → approved → completed`, plus `cancelled`, `refund_pending → refunded`, `disputed`.

## Notifications
| Method | Path |
|---|---|
| GET | `/v1/notifications/?unread=true` |
| GET | `/v1/notifications/unread-count/` |
| PATCH | `/v1/notifications/{id}/read/` |
| POST | `/v1/notifications/read-all/` |

## File uploads
Three steps. Files go from the browser straight to storage; the API never streams them.
1. `POST /v1/uploads/` with `{kind, contentType, size}` → `{upload, uploadUrl, method: "PUT", headers}`
2. `PUT` the file to `uploadUrl` with those headers (no auth header). The URL works once.
3. `POST /v1/uploads/{id}/complete/` → `{id, kind, status, contentType, size, url}`

| Kind | Who | Types | Max | Visibility |
|---|---|---|---|---|
| `avatar` | creator | jpeg, png, webp | 10 MB | public; re-encoded to WebP; sets `avatarUrl` |
| `gallery` | creator | jpeg, png, webp | 10 MB (12 images) | public; shows as `gallery` on the profile |
| `brand_logo` | brand | jpeg, png, webp | 10 MB | public; sets `logoUrl` |
| `deliverable` | creator | images, pdf, mp4, mov | 50 MB | private; attach with `fileUploadId` on `/orders/{id}/deliverables/` |
| `chat_attachment` | brand/creator | images, pdf | 10 MB | private; attach with `attachmentUploadId` on a message |
| `kyc_document` | brand/creator | jpeg, png, webp, pdf | 10 MB | private (owner + ops only); attach with `uploadId` on `PUT /v1/kyc/id/` |

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/uploads/{id}/download-url/` | private files only; 5-minute link; uploader + the other party on that order/conversation |
| DELETE | `/v1/creators/me/media/{id}/` | remove a gallery image |

## Messaging
One conversation per brand–creator pair, allowed once a bid, offer or order exists between them.
| Method | Path | Notes |
|---|---|---|
| GET | `/v1/conversations/` | `withParty`, `lastMessage`, `unread`, `updatedAt` |
| POST | `/v1/conversations/` | brand: `{creatorId}`; creator: `{brandId}`. Returns the existing thread if any |
| GET | `/v1/conversations/{id}/` | |
| GET | `/v1/conversations/{id}/messages/` | newest first, 30 per page |
| POST | `/v1/conversations/{id}/messages/` | `{text?, attachmentUploadId?}`; 4,000 chars max; 30/min |
| POST | `/v1/conversations/{id}/read/` | marks read |

## KYC
Upload the ID document first: `POST /v1/uploads/` with `kind: "kyc_document"` (JPEG/PNG/WebP/PDF, 10 MB, private), PUT the file, then `POST /v1/uploads/{id}/complete/`. Every KYC response has the same shape: `{status, rejectionReason, submittedAt, steps: {pan: {done, nameOnPan, panLast4}, id: {done, idType}, bank: {done, accountHolderName, accountLast4, ifsc}}}`. Full PAN and account numbers are never returned.

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/v1/kyc/` | brand/creator | status + masked steps |
| PUT | `/v1/kyc/pan/` | brand/creator | `{pan, nameOnPan}`, PAN like `ABCDE1234F` |
| PUT | `/v1/kyc/id/` | brand/creator | `{idType: aadhaar\|passport\|driving_licence\|voter_id, uploadId}` |
| PUT | `/v1/kyc/bank/` | brand/creator | `{accountHolderName, accountNumber (9-18 digits), ifsc}` |
| POST | `/v1/kyc/submit/` | brand/creator | → `pending`. `422 KYC_INCOMPLETE` if a step is missing |

Steps can be edited while `not_started`, `in_progress` or `rejected`; otherwise `409 KYC_LOCKED`. Ops verify or reject in the Django admin. The user gets a `kyc_verified` / `kyc_rejected` notification, and on rejection `rejectionReason` says what to fix.

## Live updates (WebSocket)
`POST /v1/realtime/ticket/` → `{ticket}` (valid 30 s, single use), then connect to `wss://collab-api-ewju.onrender.com/ws/?ticket=<ticket>`.
Events: `{event: "connected"}`, `{event: "new_message", conversationId, message}`, `{event: "notification", notification}`. Send `{"type":"ping"}` → `{"type":"pong"}`. On disconnect, get a new ticket and reconnect.

## Ops (Django admin, not an API)
At `/<DJANGO_ADMIN_URL>`, with TOTP 2FA. Setup: `uv run python manage.py grant_admin you@company.com`, then `uv run python manage.py add_totp you@company.com`. Approve/reject creators via the admin actions. KYC review: **Kyc profiles** → open the ID document (signed link, 5 min) → run *Verify*, or type a *Rejection reason*, save, then run *Reject*. Orders are read-only there.

**Test logins (QA / app-store review):** Render env `OTP_TEST_ACCOUNTS` (comma-separated `+91XXXXXXXXXX` or lower-case emails) and `OTP_TEST_CODE` (6 digits, required when the list is set). Those destinations get no SMS/email; the code is always `OTP_TEST_CODE`, and the hourly limit doesn't apply (the per-IP limit still does). The normal OTP flow is unchanged. **Empty both before public launch.**

## Not built yet (next phases)
Razorpay payments/escrow/payouts/refunds + webhook, GST invoice / TDS statement PDFs, web push + email notifications, Instagram/YouTube connect, brand dashboard/reporting, admin APIs for disputes/refunds, Celery jobs (auto-approve, payment expiry).
