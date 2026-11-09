# API Reference (v1): what's built

Interactive docs: run the server and open **`/v1/docs/`** (Swagger, dev only). Raw schema: `/v1/schema/`.
React apps should generate types from the schema: `npx openapi-typescript http://localhost:8000/v1/schema/ -o src/api/schema.ts`.

## Conventions
- Auth header: `Authorization: Bearer <access>` (15-minute access token; refresh with `/v1/auth/token/refresh/` using 15-day rotating refresh tokens. Expired tokens return `TOKEN_EXPIRED` with HTTP 401).
- Every response: `{ "success", "data", "error", "meta" }`. Errors: `error.code` (stable, switch on it), `error.message`, `error.requestId`, plus `error.details` (field errors) on validation failures.
- Money: integer **paise**, e.g. `{ "amount": 1118000, "currency": "INR" }`. Request fields like `price`/`budget` are paise too.
- Lists: cursor pagination with `?limit=` (max 50) and `?cursor=` from `meta.nextCursor`.
- Unknown request fields are rejected with `400` (mass-assignment protection).
- If something isn't yours, you get `404`, not `403`.

## Auth: phone/email OTP (2Factor.in SMS, Resend email) & Email Verification
| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/v1/auth/otp/request/` | anyone | `{channel: "sms"\|"email", destination}` → `{challengeId, expiresIn}`. 5/min per IP, 5/hour per destination |
| POST | `/v1/auth/otp/verify/` | anyone | `{challengeId, code}` → `{access, refresh, user}`. 5 attempts per code; single use |
| POST | `/v1/auth/email/request/` | user | `{email}` → `{challengeId, expiresAt, expiresIn}`. Dispatches verification code to email |
| POST | `/v1/auth/email/verify/` | user | `{challengeId, code}` → `{email, emailVerified: true}`. Binds verified email to user |
| POST | `/v1/auth/token/refresh/` | anyone | `{refresh}` → `{access, refresh, user}` (15-day expiry with rotation; old token blacklisted; returns `TOKEN_EXPIRED` (401) on failure) |
| POST | `/v1/auth/logout/` | user | `{refresh}` → 204 |
| POST | `/v1/auth/logout-all/` | user | revokes every refresh token |
| GET | `/v1/auth/me/` | user | includes `email`, `emailVerified`, `role`, `needsOnboarding`, `profileId` |
| PATCH | `/v1/auth/me/` | user | `{email?, fullName?}`. Changing email automatically resets `emailVerified` to `false` |
| POST | `/v1/auth/signup/complete/` | user | `{role: "brand"\|"creator", fullName, brandName?, displayName?}`, once only. `admin` is impossible here |

## Creators & Media Uploads

### Profile & Avatar
| Method | Path | Who | Notes |
|---|---|---|---|
| GET/PATCH | `/v1/creators/me/` | creator | Returns `CreatorSelfOutput` including **all own packages**, `socialAccounts`, and private fields `age`, `gender`, `genderDescription`. Accepts `displayName` / `name`, `bio`, `categories[]`, `location`, `primaryPlatform`, `repliesWithin`, `socialAccounts` (`[{"platform", "handle"}]`), `age`, `gender`, `genderDescription`, `profilePicture` [multipart, max 5MB], `avatarUrl`, `socialMediaUrls` |
| POST | `/v1/uploads/` | authenticated | Multipart file upload (`file`, optional `kind: "avatar"`). 5MB limit, image-only. Auto-binds avatar to creator's profile |
| POST | `/v1/creators/me/avatar/` | creator | Dedicated multipart avatar upload (`avatar` or `file`). Alias of `/v1/uploads/` |

### Public Package / Pricing Tier Management
Creators expose **service tiers** (packages) on their public profile. Each package carries a deliverable scope, price in paise, platform, and delivery timeline.

| Method | Path | Who | Body / Notes |
|---|---|---|---|
| GET | `/v1/creators/me/packages/` | creator | Returns **all** creator's packages (active + inactive) |
| POST | `/v1/creators/me/packages/` | creator | `title` (max 120), `description` (max 2000, optional), `platform` (`instagram`\|`youtube`\|`other`), `deliverables` (max 200), `price` (integer paise, > 0), `deliveryDays` (1–90), `active` (bool, optional) |
| PATCH | `/v1/creators/me/packages/{id}/` | creator (own packages only) | Partial update — same fields as POST, all optional |
| DELETE | `/v1/creators/me/packages/{id}/` | creator (own packages only) | Soft-deletes: sets `active=False`. Record is preserved for order history. Returns 204 |

**Package response shape** (`PackageOutput`):
```json
{
  "id": "uuid",
  "title": "1 Instagram Reel",
  "description": "30-second Reel with original audio.",
  "platform": "instagram",
  "deliverables": "1 Reel (30s), 3 story frames",
  "price": { "amount": 1500000, "currency": "INR" },
  "deliveryDays": 7,
  "active": true
}
```

### Discovery & Public Profile
| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/v1/creators/?q=&category=&location=&platform=&verified=&minPrice=&maxPrice=&minFollowers=` | anyone (`AllowAny`) | Lists approved creators only. Anonymous browsing allowed. `startingPrice` reflects cheapest active package |
| GET | `/v1/creators/{id}/` | anyone (`AllowAny`) | Full profile (`CreatorDetailOutput`) — includes **only active packages** sorted by price ascending. `startingPrice` = cheapest active package. Excludes private fields (`age`, `gender`, `genderDescription`) |
| GET | `/v1/creators/{id}/reviews/` | anyone (`AllowAny`) | Paginated review list |

## Brands, lists, cart
| Method | Path | Who |
|---|---|---|
| GET/PATCH | `/v1/brands/me/` | brand (`name, website, industry` / `category, legalName, gstin, stateCode, billingAddress`) |
| GET/POST | `/v1/lists/` | brand |
| GET/PATCH/DELETE | `/v1/lists/{id}/` | brand |
| POST | `/v1/lists/{id}/creators/` `{creatorId}` | brand |
| DELETE | `/v1/lists/{id}/creators/{creatorId}/` | brand |
| GET/POST | `/v1/cart/` `{packageId, brief}` | brand |
| DELETE | `/v1/cart/{itemId}/` | brand |
| POST | `/v1/cart/checkout/` `{usageRights}` | brand → one order per item; prices come from the server |

## Campaigns & bids
| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/v1/campaigns/?status=&platform=&category=&industry=&city=&territory=&budgetMin__gte=&budgetMax__lte=&search=&sort=&limit=&cursor=` (alias `/v1/creators/me/campaigns/`) | anyone (`AllowAny`) / brand / approved creator | Query, filter, and search open campaigns anonymously with cursor pagination & sorting (`closesAt`, `-closesAt`, `budgetMax`, `-budgetMax`). Brand sees its own campaigns |
| POST | `/v1/campaigns/` | brand | `title, brief, platform, deliverables, budgetMin, budgetMax, closesAt, status?, usageRights` |
| GET/PATCH | `/v1/campaigns/{id}/` | brand owner / approved creator | GET if open; PATCH for brand owner |
| POST | `/v1/campaigns/{id}/close/` | brand owner | pending bids → rejected |
| GET | `/v1/campaigns/{id}/bids/` | brand owner: all; creator: only its own |
| POST | `/v1/campaigns/{id}/bids/` | approved creator | `price, message, expectedViews?, deliveryDays`, one per campaign |
| PATCH | `/v1/campaigns/{id}/bids/{bidId}/` | bid owner | while pending |
| POST | `/v1/campaigns/{id}/bids/{bidId}/withdraw/` | bid owner |
| POST | `/v1/campaigns/{id}/bids/{bidId}/select/` | brand owner | creates an order |

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

## Payments, Checkout & Creator Payouts
Monetary values are calculated and persisted strictly as integer **paise** (1 INR = 100 paise) to prevent floating-point errors.

| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/v1/checkout/` | brand | Initializes a payment session for an order, package, or cart items. Returns Razorpay gateway metadata (`orderId`, `amount`, `key`) |
| POST | `/v1/payments/verify/` | anyone / webhook | Confirms payment success, transitions order from `awaiting_payment` to `funded`, and generates payment receipts |
| GET | `/v1/creators/me/wallet/` (alias `/v1/payouts/`) | creator | Returns wallet balances (`availableBalance`, `pendingBalance`, `totalEarned`, `totalWithdrawn`) and transaction history |
| POST | `/v1/creators/me/withdrawals/` | creator | Requests payout of available balance (minimum ₹500 / 50,000 paise). Supports `upi` and `bank_transfer` |

## Conversations & Messaging
Enforces the **Brand-First Messaging Rule**: A brand cannot initiate or send messages to a creator unless linked by an active order, bid/campaign application, or direct offer/proposal. Violations return HTTP `422` (`CONVERSATION_NOT_ALLOWED`).

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/v1/conversations/` | brand / creator | Lists conversations for the user with participants, `lastMessage`, and `unreadCount` |
| POST | `/v1/conversations/` | brand / creator | Initiate or retrieve a conversation (`creatorId` / `brandId`, optional `content`). Strictly enforces brand-first rule |
| GET | `/v1/conversations/{id}/messages/` | participants | Retrieves messages in chronological order and marks unread messages from the other party as read |
| POST | `/v1/conversations/{id}/messages/` | participants | Sends a message within a conversation (`content`). Enforces brand-first rule |

## Reporting, Analytics & CSV Exports
| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/v1/reports/overview/` (alias `/v1/reports/analytics/`) | authenticated user | Role-based metrics dashboard (brand: total spend, campaigns, orders; creator: total earnings, pending earnings, active/completed orders, rating) |
| GET | `/v1/reports/export/orders/` | authenticated user | Downloads `orders.csv` with order details, statuses, paise & INR amounts |
| GET | `/v1/reports/export/campaigns/` | authenticated user | Downloads `campaigns.csv` with campaigns, platform, deliverables, budget range |
| GET | `/v1/reports/export/transactions/` | authenticated creator | Downloads `transactions.csv` with wallet transactions, types, and amounts |

## Notifications
| Method | Path |
|---|---|
| GET | `/v1/notifications/?unread=true` |
| GET | `/v1/notifications/unread-count/` |
| PATCH | `/v1/notifications/{id}/read/` |
| POST | `/v1/notifications/read-all/` |

## Ops (Django admin, not an API)
At `/<DJANGO_ADMIN_URL>`, with TOTP 2FA. Setup: `uv run python manage.py grant_admin you@company.com`, then `uv run python manage.py add_totp you@company.com`. Approve/reject creators via the admin actions. Orders are read-only there.

## Not built yet (next phases)
Razorpay payments/escrow/payouts/refunds + webhook, GST invoice / TDS statement PDFs, messaging + WebSocket, web push + email notifications, photo uploads (presigned), Instagram/YouTube connect, brand dashboard/reporting, admin APIs for disputes/refunds, Celery jobs (auto-approve, payment expiry).
