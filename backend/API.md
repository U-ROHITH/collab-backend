# API Reference (v1): what's built

Live base URL: **https://collab-api-ewju.onrender.com**

Interactive docs: run the server and open **`/v1/docs/`** (Swagger, dev only). Raw schema: `/v1/schema/`.
React apps should generate types from the schema: `npx openapi-typescript http://localhost:8000/v1/schema/ -o src/api/schema.ts`.

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
| POST | `/v1/auth/token/refresh/` | anyone | `{refresh}` → `{access, refresh}` (rotates; the old token is blacklisted) |
| POST | `/v1/auth/logout/` | user | `{refresh}` → 204 |
| POST | `/v1/auth/logout-all/` | user | revokes every refresh token |
| GET | `/v1/auth/me/` | user | includes `needsOnboarding`, `profileId` |
| POST | `/v1/auth/signup/complete/` | user | `{role: "brand"\|"creator", fullName, brandName?, displayName?}`, once only. `admin` is impossible here |

## Creators
| Method | Path | Who |
|---|---|---|
| GET/PATCH | `/v1/creators/me/` | creator (`displayName, bio, categories[], location, primaryPlatform, repliesWithin`) |
| GET/POST | `/v1/creators/me/packages/` | creator (`title, platform, deliverables, price, deliveryDays`) |
| PATCH/DELETE | `/v1/creators/me/packages/{id}/` | creator (DELETE = deactivate) |
| GET | `/v1/creators/?q=&category=&location=&platform=&verified=&minPrice=&maxPrice=&minFollowers=` | brand/creator: approved creators only |
| GET | `/v1/creators/{id}/` | brand/creator: profile + active packages |
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
| GET | `/v1/campaigns/` (alias `/v1/creators/me/campaigns/`) | brand: its own; approved creator: open ones |
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

## Live updates (WebSocket)
`POST /v1/realtime/ticket/` → `{ticket}` (valid 30 s, single use), then connect to `wss://collab-api-ewju.onrender.com/ws/?ticket=<ticket>`.
Events: `{event: "connected"}`, `{event: "new_message", conversationId, message}`, `{event: "notification", notification}`. Send `{"type":"ping"}` → `{"type":"pong"}`. On disconnect, get a new ticket and reconnect.

## Ops (Django admin, not an API)
At `/<DJANGO_ADMIN_URL>`, with TOTP 2FA. Setup: `uv run python manage.py grant_admin you@company.com`, then `uv run python manage.py add_totp you@company.com`. Approve/reject creators via the admin actions. Orders are read-only there.

## Not built yet (next phases)
Razorpay payments/escrow/payouts/refunds + webhook, GST invoice / TDS statement PDFs, web push + email notifications, Instagram/YouTube connect, brand dashboard/reporting, admin APIs for disputes/refunds, Celery jobs (auto-approve, payment expiry).
