# API Reference (v1): what's built

Live base URL: **https://collab-api-ewju.onrender.com**

Interactive docs: run the server and open **`/v1/docs/`** (Swagger, dev only). Raw schema: `/v1/schema/`.
React apps should generate types from the schema: `npx openapi-typescript http://localhost:8000/v1/schema/ -o src/api/schema.ts`.

## Changes on 2026-11-12 (security fixes) — frontend action needed
- **Forgot password now uses a reset code.** Request it with `POST /v1/auth/otp/request/` `{channel, destination, purpose: "password_reset"}`, then `POST /v1/auth/password/reset/` `{challengeId, code, password, confirmPassword}` → `{access, refresh, user}`. Codes only work for the purpose they were requested for: a sign-in code (`purpose` omitted or `"login"`) is rejected by `/password/reset/` with `400 OTP_INVALID`, and a reset code is rejected by `/otp/verify/`. The SMS/email text is the same for both.
- **`/password/change/` without `currentPassword` is only for accounts with no password yet** (`hasPassword: false`), within 10 minutes of a code or Google sign-in. An account that has a password must send `currentPassword`, or use the reset flow above; otherwise `403 REAUTH_REQUIRED`. ("Sign in with a code, then set a new password" no longer replaces an existing password, so a phished sign-in code can't take over an account.)
- **After any password change or reset, older sessions stop working immediately** (`401 AUTH_INVALID`, "Your password was changed. Please sign in again."), not after 15 minutes. Store the tokens the change/reset returns. Setting a first password at signup doesn't sign out the session doing it.
- **Lockout:** 10 wrong passwords per account per IP, and 50 per account across all IPs, per 15 minutes → `429 RATE_LIMITED`. Someone guessing from elsewhere can't lock the owner out. If Redis is unavailable, limits are counted per server process until it recovers (deliberate: sign-in keeps working).
- Password rules on reset now also reject passwords too similar to the account's email, as signup does.

## Changes on 2026-11-17 (backend audit)
- **OTP sign-in sends `purpose: "login"`.** Omitting `purpose` means sign-up: `409 USER_EXISTS` for an existing account. Limits: 5 codes per phone/email per hour (`429 RATE_LIMITED`), 5 requests and 10 verifies per minute per IP.
- **`/signup/complete/` never saves `phone`** (the field is accepted and ignored). A number joins an account only by signing in with an SMS code for it.
- **`POST /v1/checkout/`**: `usageRights` (same object as campaigns) is required with `packageId` or `cart`; `currency` must be `INR`. The same package can be bought any number of times. Checking out an order again returns its open session.
- **Payments**: a payment for an order cancelled meanwhile returns a receipt with `status: "needs_refund"`.
- **Withdrawals**: `POST /v1/creators/me/withdrawals/` returns `403 PAYOUTS_NOT_LIVE` until payouts launch.
- **Creators**: `dob` is only on `/v1/creators/me/` (no longer public). `avatarUrl` must be `https://…`, max 500; `null` or `""` removes the photo. `location` max 80. Sending only `city` keeps `region` (and vice versa); `""` clears it. Removing follower stats clears `followerCount`. `?category=` matches whole categories, case-insensitive.
- **Campaigns**: once a creator has bid, `brief`, `deliverables`, `platform` and `usageRights` can't change (`409 CAMPAIGN_TERMS_LOCKED`). `closesAt` edits must be in the future (`422 INVALID_CLOSES_AT`).
- **Reviews**: allowed once the brand has approved the work (`approved` or `completed`).
- **Conversations**: the list is ordered by last message, and opening a thread no longer moves it.
- **Admin API** (`/v1/admin/*`) needs the token from the admin sign-in with TOTP; app-login tokens get 403.

## Changes on 2026-11-12 (frontend issues report)
**This file is the reference for what's live.** The "API Reference v1" PDF describes an unmerged branch: `/v1/auth/email/*`, `/v1/creators/me/avatar/`, `/v1/checkout/`, `/v1/payments/verify/`, `/v1/creators/me/wallet/`, `/v1/creators/me/withdrawals/` and `/v1/reports/*` are **not live**; don't build on them. Everything in the tables below is live.

- **Outage fixed (#2):** the 500s on every database-backed call were Redis refusing connections ("max number of clients reached"); the cache opened a new pool per request. Now one bounded pool per worker, and a Redis hiccup no longer fails requests. Database connections are pooled too, which removes ~0.3 s from every request.
- **My bids (#1):** `GET /v1/creators/me/bids/` (old path `/v1/creators/me/campaigns/` now returns the same) → the signed-in creator's bids, newest first, each `{...bid, campaign: {...campaign}}`. `401 AUTH_REQUIRED` signed out, `403` for brands. `/v1/creators/me/campaigns/` no longer returns the public campaign list.
- **Campaigns (#5, #6):** every campaign has `bidCount` (bids not withdrawn). `brand` stays `null` for creators and guests **on purpose (blind bidding)**: show something neutral like "Brand on Linkloom"; the brand is revealed on the order. `GET /v1/campaigns/{id}/` is now public for open campaigns, with the same guest view as the list (`brief`/`usageRights` `null` unless you're an approved creator).
- **Filters (#7):** `GET /v1/campaigns/` accepts `platform`, `budgetMin__gte`, `budgetMax__lte` (paise), `status` (brands only; ignored for others), `limit`, `cursor`. Anything else, including `category` (campaigns have no category), is `400 VALIDATION_ERROR` with the parameter in `error.details`. `limit` above 50 is capped at 50.
- **Passwords (#3):** *(Superseded by "security fixes" above for the reset flow and when `currentPassword` is needed.)* `user.hasPassword` on `/auth/me/` and every auth response. `POST /v1/auth/password/change/` `{currentPassword?, password, confirmPassword}` → `{access, refresh, user}`: `currentPassword` may be omitted within **10 minutes** of signing in with a code or Google, otherwise `403 REAUTH_REQUIRED`; a wrong one is `400` with `error.details.currentPassword`. So "Forgot password?" = sign in with a code, then call this (or use `/password/reset/` with the code directly). A login for an account without a password returns the same `401 AUTH_INVALID` as a wrong password (on purpose, so the endpoint can't reveal which numbers have accounts); its message now says "If you signed up with a code, sign in with a code and set a password." Use `hasPassword: false` after a code sign-in to prompt for one.
- **#4:** `otp/verify` for a number with no account returns `needsOnboarding: true, role: null, hasPassword: false` (covered by a test). If someone skipped the password step, their number already had an account.
- **#8 field names:** messages: `text` (+ `attachmentUploadId`); the PDF's `content` is wrong. Deliverables: `contentUrl` **or** `fileUploadId` (exactly one) + `note?`. Bids: `expectedViews` is accepted (optional). Offers and `PATCH /creators/me/` as the site sends them. All request money is integer paise.
- **#9 dates:** `closesAt`, `createdAt` on campaigns/bids and `deadline` on offers are dates (`YYYY-MM-DD`) **by design**: bidding closes at the end of that day, IST. Orders (`createdAt`, `fundedAt`, `approvedAt`) and messages (`sentAt`) are full ISO timestamps.
- **#10:** unknown routes return the JSON envelope (`404 NOT_FOUND`). The trailing slash is optional: `/v1/campaigns` works like `/v1/campaigns/` (no redirect).
- **#12 test logins:** on for `8688815998`, `9381946904`, `6302624130`. They take a fixed code (shared privately, not in this file), send no SMS and skip the hourly limit. Anyone with the code can sign in to, or set a password on, these accounts, so keep them throwaway. They'll be turned off before launch.
- **#13:** Google sign-in accepts client `591280470035-mamos86blk66gpr7bufpv48ln9dntsef.apps.googleusercontent.com` (and the old one).
- **#15:** `422 INVALID_PHONE` for a bad mobile number on `otp/request/`. WebSocket: `wss://<host>/ws/?ticket=<ticket>` (the `path` field from `POST /v1/realtime/ticket/`); a plain GET there is a 404, as expected. CORS is open for now and will be limited to the site's origins before launch.

**Status values**
| Object | Values |
|---|---|
| Campaign | `draft`, `open`, `closed`, `cancelled` |
| Bid | `pending`, `selected`, `rejected`, `withdrawn` |
| Offer | `pending`, `accepted`, `rejected`, `withdrawn`, `expired` |
| Order | `awaiting_payment`, `funded`, `draft_submitted`, `revision_requested`, `approved`, `completed`, `cancelled`, `refund_pending`, `refunded` |
| Dispute | `open`, `resolved` |
| KYC | `not_started`, `in_progress`, `pending`, `verified`, `rejected` |
| Creator approval | `pending`, `approved`, `rejected` |
| Upload | `pending`, `ready`, `rejected` |
| User | `active`, `suspended`, `deactivated` |

Deliverables have no status of their own; the order status says where the work is.

## Changes on 2026-11-12 (passwords)
- **Password sign-in:** `POST /v1/auth/login/` `{identifier, password}` → `{access, refresh, user}`. `identifier` is an email (any casing) or a mobile number (`9876543210`, `+91 98765 43210`, `098765-43210`). A wrong password, an unknown account, an account that never set a password, and a suspended account all return the same `401 AUTH_INVALID`. After 10 wrong attempts on one identifier it returns `429 RATE_LIMITED` for 15 minutes; suggest signing in with a code.
- **Password at signup:** `/v1/auth/signup/complete/` accepts optional `password` + `confirmPassword`. Rules: 8+ characters, not all digits, not a common password, not too close to the name/email. Errors come back as `400 VALIDATION_ERROR` with `error.details.password` or `error.details.confirmPassword` (lists of messages) so the form can show them under the field. Signup without a password still works; that account signs in with a code or Google.
- **Forgot / change password:** *(Superseded by "security fixes" above for the reset flow and when `currentPassword` is needed.)* there's no emailed reset link. Request a code with `/v1/auth/otp/request/` as usual, then `POST /v1/auth/password/reset/` `{challengeId, code, password, confirmPassword}` → `{access, refresh, user}`. Same password rules and error shape as signup; a bad code is `400 OTP_INVALID` (password errors are checked first, so a typo doesn't burn the code). This also lets a code-only account add a password, and it clears a password lockout. **Every other session is signed out (their access tokens lapse within 15 minutes); store the returned tokens.** A signed-in token alone can't change the password, so a leaked token can't lock the owner out.

## Changes on 2026-11-09 (launch requests)
- **Guest view:** `GET /v1/creators/`, `GET /v1/creators/{id}/` and `GET /v1/campaigns/` work without a token. Creator cards never include contact, payout or approval details. Guests (and users mid-signup or awaiting creator approval) get open campaigns with `brief: null` and `usageRights: null`. Campaign detail, bids and every write still need a signed-in user.
- **Google sign-in:** `POST /v1/auth/google/` `{idToken, role?}` → `{access, refresh, user}`, the same shape as OTP verify. `idToken` is the Google Identity Services credential for client `1042168589486-k7e30v1tao2ooepsb5lp339m7anlvq44.apps.googleusercontent.com`. Accounts match on the Google-verified email, so an existing OTP account signs in to the same account. `role` (`brand`/`creator`) only applies to an account with no role yet; otherwise read `user.role`. Errors: `401 GOOGLE_TOKEN_INVALID`, `401 GOOGLE_EMAIL_UNVERIFIED`.
- **KYC:** `user.kycStatus` on `/v1/auth/me/` (and every auth response) is `not_started | in_progress | pending | verified | rejected`. Steps are under `/v1/kyc/` (below). **Creators:** placing a bid and accepting an offer return `403 KYC_REQUIRED` until KYC is `verified`, and orders need the creator verified (`422 COUNTERPARTY_KYC_PENDING` otherwise). Declining an offer doesn't need KYC. **Brands are never blocked by KYC**: brand KYC is voluntary (refunds and compliance) and uses `/v1/brands/me/kyc/` (below).
- **Platform stats:** `PATCH /v1/creators/me/` accepts platform stats either top-level (`instagram`, `youtube`) or inside `platforms: {instagram|youtube: {handle?, followersCount?, engagementRate?, avgViews?} | null}`. Handles are normalized: leading `@` is stripped, pasted profile URLs (e.g. `https://www.instagram.com/democreator/`) become bare handles, and `url` is exposed per platform. Unknown nested fields (e.g. `followers`, `er`) return `400 VALIDATION_ERROR` naming the invalid field. `engagementRate` is a percentage (`0.0` - `100.0`). Top-level `followerCount` is kept consistent with primary platform's `followersCount`. Flat `instagram` / `youtube` string handles are also returned and kept in sync with `platforms.<platform>.handle`. Every platform currently returns `verified: false` until social account OAuth is connected.
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
- Lists: cursor pagination with `?limit=` (values above 50 are capped at 50) and `?cursor=` from `meta.nextCursor`.
- Validation errors are `400 VALIDATION_ERROR`; a few domain checks use `422` (e.g. `INVALID_PHONE`). Unknown routes: `404 NOT_FOUND` in the same envelope. Trailing slash optional.
- Unknown request fields are rejected with `400` (mass-assignment protection).
- If something isn't yours, you get `404`, not `403`.

## Auth: phone/email OTP (2Factor.in SMS, Resend email)
| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/v1/auth/otp/request/` | anyone | `{channel: "sms"\|"email", destination, purpose?: "login"\|"password_reset"}` → `{challengeId, expiresIn}`. 5/min per IP, 5/hour per destination. The code only works for its `purpose` (default `login`) |
| POST | `/v1/auth/otp/verify/` | anyone | `{challengeId, code}` → `{access, refresh, user}`. 5 attempts per code; single use |
| POST | `/v1/auth/google/` | anyone | `{idToken, role?}` → `{access, refresh, user}`. `role` only used on first sign-in |
| POST | `/v1/auth/login/` | anyone | `{identifier, password}` → `{access, refresh, user}`. 10/min per IP; 10 wrong passwords per account+IP and 50 per account → 15-min lock |
| POST | `/v1/auth/password/change/` | user | `{currentPassword?, password, confirmPassword}` → `{access, refresh, user}`. `currentPassword` required if the account has a password; without one, allowed within 10 min of a code/Google sign-in |
| POST | `/v1/auth/password/reset/` | anyone | `{challengeId, code, password, confirmPassword}` → `{access, refresh, user}`. Code from `/otp/request/` with `purpose: "password_reset"`; signs out all other sessions immediately |
| POST | `/v1/auth/token/refresh/` | anyone | `{refresh}` → `{access, refresh}` (rotates; the old token is blacklisted) |
| POST | `/v1/auth/logout/` | user | `{refresh}` → 204 |
| POST | `/v1/auth/logout-all/` | user | revokes every refresh token |
| GET | `/v1/auth/me/` | user | includes `needsOnboarding`, `profileId`, `kycStatus`, `hasPassword` |
| POST | `/v1/auth/signup/complete/` | user | `{role: "brand"\|"creator", fullName, brandName?, displayName?, password?, confirmPassword?}`, once only. `admin` is impossible here |

## Creators
| Method | Path | Who |
|---|---|---|
| GET/PATCH | `/v1/creators/me/` | creator (`displayName, bio, categories[], location, primaryPlatform, repliesWithin, instagram, youtube, platforms`) |
| GET/POST | `/v1/creators/me/packages/` | creator (`title, platform, deliverables, price, deliveryDays`) |
| PATCH/DELETE | `/v1/creators/me/packages/{id}/` | creator (DELETE = deactivate) |
| GET | `/v1/creators/?q=&category=&location=&platform=&verified=&minPrice=&maxPrice=&minFollowers=` | **public**: approved creators only |
| GET | `/v1/creators/{id}/` | **public**: profile + active packages |
| GET | `/v1/creators/me/earnings/` | creator: `{inEscrow, earned, tdsDeducted, ordersInEscrow, ordersEarned, payoutsEnabled}` |
| GET | `/v1/creators/{id}/reviews/` | brand/creator |

### Creator Profile Body Shapes

#### `PATCH /v1/creators/me/` Request Body
```json
{
  "displayName": "Ananya Iyer",
  "bio": "Honest skincare for humid weather.",
  "categories": ["Skincare", "Beauty"],
  "languages": ["English", "Telugu"],
  "city": "Hyderabad",
  "region": "Telangana",
  "primaryPlatform": "instagram",
  "dob": "1998-04-12",
  "gender": "Woman",
  "instagram": {
    "handle": "@ananyamakes",
    "followersCount": 30000,
    "avgViews": 8000,
    "engagementRate": 4.2
  },
  "youtube": {
    "handle": "https://www.youtube.com/@AnanyaCooks",
    "followersCount": 20000,
    "avgViews": 15000,
    "engagementRate": 2.8
  }
}
```
*Notes on PATCH:*
- `instagram` and `youtube` accept:
  - An object `{handle?, followersCount?, avgViews?, engagementRate?}`.
  - Or a bare string handle (e.g. `"ananyamakes"`).
  - Or a pasted profile URL (e.g. `"https://www.instagram.com/ananyamakes/"`), which is automatically normalized to `"ananyamakes"`.
  - Or `null` to remove the platform.
- Or use the `platforms: {instagram: {...}, youtube: {...}}` wrapper.
- Unknown nested fields (e.g. `followers`, `er`, `likes`) trigger `400 VALIDATION_ERROR` naming the field.
- `followerCount` is computed/derived from the primary platform's `followersCount` and kept in sync on the creator profile.

#### `GET /v1/creators/me/`, `GET /v1/creators/{id}/`, `GET /v1/creators/` Response Data
```json
{
  "id": "01a1e371-f127-7780-98d0-4ae902676a98",
  "slug": "ananya-iyer-67c664",
  "displayName": "Ananya Iyer",
  "bio": "Honest skincare for humid weather.",
  "categories": ["Skincare", "Beauty"],
  "location": "Hyderabad, Telangana",
  "city": "Hyderabad",
  "region": "Telangana",
  "primaryPlatform": "instagram",
  "repliesWithin": "24h",
  "verified": false,
  "followerCount": 30000,
  "avatarUrl": "https://...",
  "rating": null,
  "reviewCount": 0,
  "startingPrice": { "amount": 500000, "currency": "INR" },
  "dob": "1998-04-12",
  "gender": "Woman",
  "languages": ["English", "Telugu"],
  "instagram": "ananyamakes",
  "youtube": "AnanyaCooks",
  "platforms": {
    "instagram": {
      "handle": "ananyamakes",
      "url": "https://www.instagram.com/ananyamakes/",
      "followersCount": 30000,
      "avgViews": 8000,
      "engagementRate": 4.2,
      "verified": false
    },
    "youtube": {
      "handle": "AnanyaCooks",
      "url": "https://www.youtube.com/@AnanyaCooks",
      "followersCount": 20000,
      "avgViews": 15000,
      "engagementRate": 2.8,
      "verified": false
    }
  }
}
```

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
| GET | `/v1/campaigns/?platform=&budgetMin__gte=&budgetMax__lte=` | brand: its own (`?status=`); approved creator: open ones; **public** (anyone else): open ones without `brief`/`usageRights`. Each has `bidCount` |
| POST | `/v1/campaigns/` | brand (`title, brief, platform, deliverables, budgetMin, budgetMax, closesAt, status?, usageRights`) |
| GET | `/v1/campaigns/{id}/` | **public** for open campaigns (same guest view as the list); brand owner in any status |
| PATCH | `/v1/campaigns/{id}/` | brand owner |
| GET | `/v1/creators/me/bids/` (alias `/v1/creators/me/campaigns/`) | creator: own bids, each with `campaign` |
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
1. `POST /v1/uploads/` with `{kind, contentType, size}` (also accepts `mimeType`, `sizeBytes` / `fileSize`, and optional `filename`) → `{upload, uploadUrl, method: "PUT", headers}`
2. `PUT` the file to `uploadUrl` with those headers (no auth header). The URL works once.
3. `POST /v1/uploads/{id}/complete/` → `{id, kind, status, contentType, size, url}`

| Kind | Who | Types | Max | Visibility |
|---|---|---|---|---|
| `avatar` | creator / brand | jpeg, png, webp | 10 MB | public; re-encoded to WebP; sets `avatarUrl` (and `logoUrl` for brands) |
| `gallery` | creator | jpeg, png, webp | 10 MB (12 images) | public; shows as `gallery` on the profile |
| `brand_logo` | brand | jpeg, png, webp | 10 MB | public; sets `logoUrl` and `avatarUrl` |
| `deliverable` | creator | images, pdf, mp4, mov | 50 MB | private; attach with `fileUploadId` on `/orders/{id}/deliverables/` |
| `chat_attachment` | brand/creator | images, pdf | 10 MB | private; attach with `attachmentUploadId` on a message |
| `kyc_document` | brand/creator | jpeg, png, webp, pdf | 10 MB | private (owner + ops only); attach with `uploadId` on `PUT /v1/kyc/id/` |

> Profile picture updates: After completion, avatars are auto-attached to the profile. You can also link an uploaded image explicitly via `PATCH /v1/creators/me/` (`{avatarUrl}` or `{avatarUploadId}`) and `PATCH /v1/brands/me/` (`{logoUrl}`, `{avatarUrl}`, or `{logoUploadId}`). `GET /v1/auth/me/` always returns `avatarUrl` for the logged-in user.

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

## Brand KYC (voluntary)
Company PAN + bank account. Never blocks a brand action. Each save puts it on the admin verification desk.

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/v1/brands/me/kyc/` | brand | `{status: pending\|submitted\|verified\|rejected, panVerified, bankVerified, rejectionReason}`. Never 404: nothing saved reads as `pending`. |
| POST | `/v1/brands/me/kyc/pan/` | brand | `{pan}` (uppercased; `ABCDE1234F` format) → same shape, `status: submitted`. |
| POST | `/v1/brands/me/kyc/bank/` | brand | `{accountNumber (9-18 digits), ifsc, accountName}` → same shape, `status: submitted`. |

`panVerified` / `bankVerified` turn true only when an admin approves on the desk (only the fields that were submitted). Changing a detail later resets its flag and sends it back for review; rejection clears both and fills `rejectionReason`. Full PAN and account number are stored encrypted and never returned.

## KYC (creators)
Upload the ID document first: `POST /v1/uploads/` with `kind: "kyc_document"` (JPEG/PNG/WebP/PDF, 10 MB, private), PUT the file, then `POST /v1/uploads/{id}/complete/`. Every KYC response has the same shape:
```json
{
  "status": "not_started" | "in_progress" | "pending" | "action_needed" | "verified" | "rejected",
  "rejectionReason": "...",
  "submittedAt": "2026-11-17T12:00:00Z",
  "steps": {
    "pan": { "done": true, "nameOnPan": "...", "panLast4": "1234" },
    "id": { "done": true, "idType": "aadhaar" },
    "bank": { "done": true, "accountHolderName": "...", "accountLast4": "5678", "ifsc": "HDFC0001234" }
  },
  "review": {
    "reason": "...",
    "requestedItems": ["id", "pan"],
    "message": "Please re-upload a clearer copy of your Aadhaar card",
    "decidedAt": "2026-11-17T12:30:00Z"
  }
}
```
Full PAN and account numbers are never returned.

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/v1/kyc/` | brand/creator | status + masked steps + review feedback |
| PUT | `/v1/kyc/pan/` | brand/creator | `{pan, nameOnPan}`, PAN like `ABCDE1234F` |
| PUT | `/v1/kyc/id/` | brand/creator | `{idType: aadhaar\|passport\|driving_licence\|voter_id, uploadId}` |
| PUT | `/v1/kyc/bank/` | brand/creator | `{accountHolderName, accountNumber (9-18 digits), ifsc}` |
| POST | `/v1/kyc/submit/` | brand/creator | → `pending`. Runs automated checks (`age_18_plus`, `name_match`, `gstin_pan_match`, `business_email_domain`, `bank_name_match`), computes SLA deadline (+24h creator, +48h brand), creates admin verification record, and notifies ops. `422 KYC_INCOMPLETE` if a step is missing |

Steps can be edited while `not_started`, `in_progress`, `action_needed`, or `rejected`; otherwise `409 KYC_LOCKED`. The user receives in-app/push notifications (`kyc_verified`, `kyc_rejected`, `kyc_info_requested`).

## Super Admin API (/v1/admin/)
Custom management portal endpoints with role-based access control (`super_admin`, `trust_safety`, `finance`), mandatory 2FA TOTP verification, and SHA-256 tamper-evident audit logging. Non-admin users receive `403 FORBIDDEN`.

### Admin Auth Endpoints
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| POST | `/v1/admin/auth/login/` | anyone | `{email, password}` → `{mfaRequired: true, mfaToken, user}`. Validates admin status and prompts for MFA. |
| POST | `/v1/admin/auth/mfa/verify/` | anyone (MFA session) | `{code, mfaToken}` → `{access, refresh, user}`. Verifies TOTP and issues JWT with `aal: "aal2"` and admin claims. |
| POST | `/v1/admin/auth/logout/` | Admin (`IsAdminUser`) | `{refresh}` → `{success: true, message: "..."}`. Blacklists refresh token and records `admin.logout` in audit log. |

### Verification Desk Endpoints (`trust_safety` & `super_admin`)
Guard: `IsAdminUser` + `RequireAdminRoles("trust_safety")`.

| Method | Path | Auth / Role | Description |
|---|---|---|---|
| GET | `/v1/admin/verifications/` | `trust_safety` | Query params: `kind` (`creator`\|`brand`), `status` (`pending`\|`approved`\|`rejected`\|`info_requested`), `limit` (default 20, max 100), `cursor`. Sorted by SLA urgency. Returns list with `meta.counts` (`total`, `pending`, `approved`, `rejected`, `info_requested`, `creator`, `brand`). |
| GET | `/v1/admin/verifications/{id}/` | `trust_safety` | Full verification detail with user summary, profile, masked KYC details (PAN, Aadhaar/ID signed download URL, bank), automated checks list, and previous decision history. |
| POST | `/v1/admin/verifications/{id}/approve/` | `trust_safety` | Approves KYC (`verified`) and creator approval / brand registration. **Refused with `409 VERIFICATION_BLOCKED`** if any automated check has `blocking: true`. Emits audit log (`verification.approve`) and user notification (`kyc_verified`). |
| POST | `/v1/admin/verifications/{id}/reject/` | `trust_safety` | Body: `{reason: "...", note?: "..."}` (`reason` min 4 chars). Rejects KYC (`rejected`), leaves profile resubmittable, logs audit (`verification.reject`), and notifies user (`kyc_rejected`). |
| POST | `/v1/admin/verifications/{id}/request-info/` | `trust_safety` | Body: `{items: ["id", "pan", "bank"], message: "..."}`. Moves verification to `info_requested` and KYC to `action_needed`, logs audit (`verification.request_info`), and notifies user (`kyc_info_requested`). |

*Note: Calling `approve`, `reject`, or `request-info` on an already-decided verification request returns `409 ALREADY_DECIDED`.*

### Campaign Moderation Endpoints (`trust_safety` & `super_admin`)
Guard: `IsAdminUser` + `RequireAdminRoles("trust_safety")`.

| Method | Path | Auth / Role | Description |
|---|---|---|---|
| GET | `/v1/admin/campaigns/` | `trust_safety` | Query params: `filter` (`pending` [default], `all`, `live`, `hidden`, `rejected`, `removed`, `flagged`), `brandId`, `limit` (default 20, max 100), `cursor`. Pending/flagged sorted oldest-first. Returns list with `meta.counts` (`pending`, `live`, `hidden`, `rejected`, `removed`, `flagged`, `total`). |
| GET | `/v1/admin/campaigns/{id}/` | `trust_safety` | Detailed campaign data, brand profile, brief signals check (`brand_verified`, `business_email`, `budget_sanity`, `solicitation_check`), `bidsSummary` (`total`, `pending`, `held`, `selected`, `rejected`), and `moderation` history. |
| POST | `/v1/admin/campaigns/{id}/approve/` | `trust_safety` | Approves pending campaign (`live`), unfreezes any `held` bids back to `pending`, logs audit (`campaign.approve`), and notifies brand owner (`campaign_approved`). Only valid from `pending_review` (else `409 INVALID_STATUS_TRANSITION`). |
| POST | `/v1/admin/campaigns/{id}/reject/` | `trust_safety` | Body: `{reason: "...", note?: "...", ruleBroken?: "..."}` (`reason` min 4 chars). Rejects campaign (`rejected`), logs audit (`campaign.reject`), and notifies brand owner (`campaign_rejected`). Only valid from `pending_review` (else `409`). |
| POST | `/v1/admin/campaigns/{id}/hide/` | `trust_safety` | Body: `{reason: "..."}`. Hides a live campaign from public search and freezes open bids to `held`, logs audit (`campaign.hide`), and notifies brand owner (`campaign_hidden`). Only valid from `live` (else `409`). |
| POST | `/v1/admin/campaigns/{id}/remove/` | `trust_safety` | Body: `{reason: "..."}`. Permanently removes live/hidden campaign (`removed`), declines open/held bids with reason (`rejected`), logs audit (`campaign.remove`), and notifies brand owner and bidders. Valid from `live` or `hidden` (else `409`). |
| POST | `/v1/admin/campaigns/{id}/restore/` | `trust_safety` | Body: `{reason: "..."}`. Restores a hidden campaign back to `live`, unfreezes `held` bids back to `pending`, logs audit (`campaign.restore`), and notifies brand owner (`campaign_restored`). Only valid from `hidden` (else `409`). |

#### Campaign Moderation Lifecycle Rules
- **Submission:** New campaigns created via `POST /v1/campaigns/` are saved in `pending_review` with `submitted_at = now()`, notifying admins (`campaign_submitted`).
- **Resubmission:** If an owning brand edits a `live` campaign's reviewed fields (`title`, `brief`, `deliverables`, `budgetMin`, `budgetMax`, `usageRights`, `platform`) via `PATCH /v1/campaigns/{id}/`, the campaign re-enters `pending_review`, records `changed_fields`, freezes open bids (`held`), and re-notifies admins. If editing a `rejected` campaign, it moves back to `pending_review`.
- **Visibility:** Creators and unauthenticated guests can only view and bid on campaigns in `live` status. Owning brands can view their campaigns in any moderation status along with a `moderation` object (`{ status, reason, changedFields }`).
- **Bidding / Selection Guard:** Bidding on or selecting bids for non-live campaigns is rejected with `409 CAMPAIGN_NOT_LIVE` or `409 BID_NOT_PENDING`.

### User Accounts & Curation (`/v1/admin/users/`)
Guard: `IsAdminUser` + `RequireAdminRoles("trust_safety")`.

#### User Accounts Management
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| POST | `/v1/admin/users/{userId}/suspend/` | `trust_safety` | Body: `{reason: "...", note?: "..."}`. Sets user `status="suspended"`, revokes active sessions/tokens, clears creator `featured`, freezes open bids for brand campaigns, logs `account.suspend`, and notifies user. |
| POST | `/v1/admin/users/{userId}/reactivate/` | `trust_safety` | Body: `{reason: "..."}`. Sets user `status="active"`, logs `account.reactivate`, and notifies user. |
| POST | `/v1/admin/users/{userId}/password-reset/` | `trust_safety` | Generates a 30-min password reset OTP/link challenge without exposing password, logs `account.password_reset`, and dispatches notification. |
| GET | `/v1/admin/users/{userId}/sessions/` | `trust_safety` | Lists active outstanding JWT sessions (`id`, `createdAt`, `expiresAt`, `ipAddress`, `device`). |
| POST | `/v1/admin/users/{userId}/sessions/revoke/` | `trust_safety` | Blacklists all active sessions for the user, increments password version to invalidate access tokens immediately, logs `account.sessions_invalidate`, and notifies user. |

#### Creator Curation & Detail
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| GET | `/v1/admin/users/creators/` | `trust_safety` | Filters: `q`, `status`, `kyc`, `badge`, `featured`, `hasReports`, `limit`, `cursor`. Returns creator cards with user status, kyc, badges, and `meta.total`, `meta.suspended`. |
| GET | `/v1/admin/users/creators/{id}/` | `trust_safety` | Returns full creator profile, platform stats, order fulfillment metrics (`completedOrders`, `inProgressOrders`, `disputedOrders`, `completionRate`, `gmvPaise`), recent orders, abuse reports, and `adminHistory` audit log. |
| PATCH | `/v1/admin/users/creators/{id}/` | `trust_safety` | Body: `{badges?: [...], tags?: [...], featured?: bool}`. Tags capped at 4 from categories; `Verified Pro` requires verified KYC. Logs granular audit actions (`creator.badge_add`, `creator.badge_remove`, `creator.tags_edit`, `creator.feature`, `creator.unfeature`). |

#### Brand Accounts & Activity
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| GET | `/v1/admin/users/brands/` | `trust_safety` | Filters: `q`, `status`, `verified`, `postingSpike`, `hasReports`, `limit`, `cursor`. Identifies `postingSpike: true` when >= 5 campaigns in last 30 days and > 2x monthly average. |
| GET | `/v1/admin/users/brands/{id}/` | `trust_safety` | Returns full brand profile, statutory details (`companyPan`, `cin`), campaign/order metrics, recent orders, abuse reports, and `adminHistory`. |

#### Categories & Marketplace Featured Ordering
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| GET | `/v1/admin/users/categories/` | `trust_safety` | Lists categories with creator counts. |
| POST | `/v1/admin/users/categories/` | `trust_safety` | Body: `{name, description?, icon?}`. Creates category and logs `category.add`. |
| PATCH | `/v1/admin/users/categories/{id}/` | `trust_safety` | Renames/updates category and cascades rename to creator profiles; logs `category.rename`. |
| POST | `/v1/admin/users/categories/{id}/merge/` | `trust_safety` | Body: `{targetCategoryId}`. Migrates creator profiles and tags to target category, deactivates source, and logs `category.merge`. |
| GET | `/v1/admin/users/featured-creators/` | `trust_safety` | Returns ordered list of featured creators followed by eligible unfeatured candidates. |
| PUT | `/v1/admin/users/featured-creators/order/` | `trust_safety` | Body: `{creatorIds: [...]}`. Updates creator `featured_order` ranks and logs `featured.reorder`. |

### Moderation Endpoints (`/v1/admin/moderation/`)
Guard: `IsAdminUser` + `RequireAdminRoles("trust_safety")`.

#### Review Moderation
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| GET | `/v1/admin/moderation/reviews/` | `trust_safety` | Query params: `status` (`visible`\|`flagged`\|`removed`), `limit`, `cursor`. Auto-flags reviews created without a completed order. |
| POST | `/v1/admin/moderation/reviews/{id}/delete/` | `trust_safety` | Body: `{reason: "..."}`. Sets `status="removed"`, recalculates creator rating sum/count from visible reviews, logs `review.delete`, and notifies reviewer. |
| POST | `/v1/admin/moderation/reviews/{id}/keep/` | `trust_safety` | Body: `{note?: "..."}`. Clears flag, marks `status="visible"`, adds review stars to creator rating, and logs `review.keep`. |

#### Abuse Reports & Bid Anomaly Detection
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| POST | `/v1/abuse-reports/` | anyone (authenticated) | Body: `{targetType: "creator"\|"brand"\|"campaign"\|"review"\|"message", targetId, reason, detail?}`. One active report per user per target. |
| GET | `/v1/admin/moderation/abuse-reports/` | `trust_safety` | Lists abuse reports with filter by `status` and `targetType`. |
| GET | `/v1/admin/moderation/abuse-reports/{id}/` | `trust_safety` | Detailed report view with target entity context and reporter summary. |
| POST | `/v1/admin/moderation/abuse-reports/{id}/remove/` | `trust_safety` | Removes/hides target entity, closes report as `action_taken`, logs audit, and notifies reporter. |
| POST | `/v1/admin/moderation/abuse-reports/{id}/warn/` | `trust_safety` | Sends policy warning to target owner, marks report `action_taken`, logs audit, and notifies reporter. |
| POST | `/v1/admin/moderation/abuse-reports/{id}/suspend-account/` | `trust_safety` | Suspends target user's account, invalidates sessions, marks report `action_taken`, and notifies parties. |
| POST | `/v1/admin/moderation/abuse-reports/{id}/dismiss/` | `trust_safety` | Dismisses report with reason and logs audit. |
| GET | `/v1/admin/moderation/bid-alerts/` | `trust_safety` | Lists anomaly bid alerts (`spike_bids`, `rapid_submission`, `identical_text`, `coordinated_accounts`). |
| POST | `/v1/admin/moderation/bid-alerts/{id}/freeze-bids/` | `trust_safety` | Freezes suspicious campaign bids to `held`, logs audit (`bid_alert.freeze_bids`). |
| POST | `/v1/admin/moderation/bid-alerts/{id}/suspend-accounts/` | `trust_safety` | Suspends involved bidder accounts, logs audit (`bid_alert.suspend_accounts`). |
| POST | `/v1/admin/moderation/bid-alerts/{id}/warn/` | `trust_safety` | Sends warning notifications to creators/accounts, logs audit. |
| POST | `/v1/admin/moderation/bid-alerts/{id}/dismiss/` | `trust_safety` | Dismisses alert with note and logs audit. |

### Finance & Escrow Controls (`/v1/admin/finance/`)

#### Order Pipeline & Escrow Overrides
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| GET | `/v1/admin/finance/orders/` | `finance`, `super_admin` | Lists orders with filters by `status`, `search`, pagination. |
| GET | `/v1/admin/finance/orders/pipeline/` | `finance`, `super_admin` | Order counts and amounts grouped by status, plus `heldTotal` and `disputedTotal` (all in integer paise). |
| GET | `/v1/admin/finance/orders/{id}/` | `finance`, `super_admin` | Detailed order view with parties, 5-line breakdown (`price`, `commission`, `gateway`, `creatorGets`, `linkloomNet`), payout status, and timeline with audit hashes. |
| POST | `/v1/admin/finance/orders/{id}/force-release/` | `super_admin` only | Requires `Idempotency-Key` header and body `{reason: "..."}` (min 12 chars). Releases escrow to creator wallet, marks order `completed`, resolves open dispute, logs `escrow.force_release`, notifies parties. Replays with identical key return original result safely. |
| POST | `/v1/admin/finance/orders/{id}/refund/` | `super_admin` only | Requires `Idempotency-Key` header and body `{reason: "..."}` (min 12 chars). Refunds order escrow to brand, marks order `refunded`, cancels unpaid payouts, resolves open dispute, logs `escrow.refund`, notifies parties. Replays with identical key return original result safely. |

#### Disputes & Mediation
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| POST | `/v1/orders/{id}/disputes/` | Brand or Creator on order | Body: `{reason, kind?: "dispute"\|"payment_complaint", evidenceUploadIds?: [...]}`. Transitions order to `disputed`, preserves `status_before_dispute`, sets SLA deadline (+72h / +24h), notifies admins. |
| POST | `/v1/orders/{id}/disputes/{disputeId}/response/` | Counterparty only | Body: `{statement, evidenceUploadIds?: [...]}`. Submits counterparty statement and evidence, notifies parties and admins. |
| GET | `/v1/admin/finance/disputes/` | `trust_safety`, `super_admin`, `finance` | Lists disputes (`status=open\|resolved`), sorted open-first by SLA due date. |
| GET | `/v1/admin/finance/disputes/{id}/` | `trust_safety`, `super_admin`, `finance` | Full dispute detail including counterparty statement, evidence files, decision metadata, and audit entry. |
| POST | `/v1/admin/finance/disputes/{id}/resolve/` | `trust_safety`, `super_admin` | Body: `{reason}` (min 4 chars). Restores order to `status_before_dispute` without moving money, marks dispute resolved, logs `dispute.resolve`, notifies both parties. |

#### Platform Rules & Financial Reporting
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| GET | `/v1/admin/finance/rules/` | `finance`, `super_admin`, `trust_safety` | Returns active platform commission & payment gateway percentages `{commissionPct, gatewayPct}`. |
| GET | `/v1/admin/finance/summary/` | `finance`, `super_admin` | Financial KPIs and monthly trailing summaries (`?months=3\|6`): GMV, commission earned, payouts settled, escrow held, refund rate. |
| GET | `/v1/admin/finance/payouts/` | `finance`, `super_admin` | Payout/withdrawal list (`?status=pending\|scheduled\|paid\|rejected`), with masked account numbers and UPI IDs. |
| GET | `/v1/admin/finance/payouts/export/` | `finance`, `super_admin` | Exports payout ledger as CSV with masked account credentials. Logs `payouts.export` audit entry. |

### System & Command Dashboard (`/v1/admin/system/`)

#### Command Dashboard & Admin Notifications
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| GET | `/v1/admin/system/dashboard/` | Any admin | Real-time command dashboard: account stats, orders & escrow held, verifications pending/over-SLA, pending campaigns & oldest submission, open abuse reports, open disputes & next SLA, active alerts, recent 5 audit entries, and cryptographic audit chain status `{ok, count}`. |
| GET | `/v1/admin/system/counts/` | Any admin | Lightweight badge counts for sidebar badges (`verifications`, `campaigns`, `reports`, `disputes`, `alerts`, `unreadNotifications`). |
| GET | `/v1/admin/system/notifications/` | Any admin | Paginated admin notification feed with unread filter. |
| GET | `/v1/admin/system/notifications/unread-count/` | Any admin | Admin unread notification count. |
| POST | `/v1/admin/system/notifications/{id}/read/` | Any admin | Mark single admin notification as read. |
| POST | `/v1/admin/system/notifications/read-all/` | Any admin | Mark all admin notifications as read. |

#### Hash-Chained Audit Log & Append-Only Verification
| Method | Path | Auth / Role | Description |
|---|---|---|---|
| GET | `/v1/admin/system/audit-log/` | Any admin | Paginated audit log feed with filters by `action`, `adminId`, `targetType`, `dateFrom`, `dateTo`. |
| GET | `/v1/admin/system/audit-log/verify/` | Any admin | Traverses and cryptographically recalculates the entire SHA-256 hash chain from genesis to tail (`{ok, count, brokenAtId?}`). Records `audit.verify` audit entry. |
| GET | `/v1/admin/system/audit-log/export/` | `super_admin` only | Exports complete audit trail as CSV. Records `audit.export` audit entry. |
| GET | `/v1/uploads/{id}/download-url/` | Admins & Asset Owners | Admins accessing signed media URLs automatically record an `upload.view` audit entry. |

### Admin Roles
- `super_admin`: Full access across all admin modules (creator moderation, dispute resolutions, financial overrides, user management).
- `trust_safety`: Creator approvals, KYC verification, dispute mediation, and abuse reports.
- `finance`: Escrow inspection, payout batches, refund approvals, and TDS/GST reconciliation.

### Audit Logging
Every sensitive state change and auth lifecycle action is executed within `transaction.atomic()` via `record_audit(admin, action, target, detail)` (or model save), creating an append-only cryptographic SHA-256 hash chain linked to the preceding entry (`prev_hash`). Audit records cannot be deleted (`AUDIT_LOG_IMMUTABLE`). Integrity can be verified on-demand via `GET /v1/admin/audit-log/verify/`.

## Live updates (WebSocket)
`POST /v1/realtime/ticket/` → `{ticket}` (valid 30 s, single use), then connect to `wss://collab-api-ewju.onrender.com/ws/?ticket=<ticket>`.
Events: `{event: "connected"}`, `{event: "new_message", conversationId, message}`, `{event: "notification", notification}`. Send `{"type":"ping"}` → `{"type":"pong"}`. On disconnect, get a new ticket and reconnect.

## Ops (Django admin, not an API)
At `/<DJANGO_ADMIN_URL>`, with TOTP 2FA. Setup: `uv run python manage.py grant_admin you@company.com`, then `uv run python manage.py add_totp you@company.com`. Approve/reject creators via the admin actions. KYC review: **Kyc profiles** → open the ID document (signed link, 5 min) → run *Verify*, or type a *Rejection reason*, save, then run *Reject*. Orders are read-only there.

**Test logins (QA / app-store review):** Render env `OTP_TEST_ACCOUNTS` (comma-separated `+91XXXXXXXXXX` or lower-case emails) and `OTP_TEST_CODE` (6 digits, required when the list is set). Those destinations get no SMS/email; the code is always `OTP_TEST_CODE`, and the hourly limit doesn't apply (the per-IP limit still does). The normal OTP flow is unchanged. **Empty both before public launch.**

## Not built yet (next phases)
Razorpay payments/escrow/payouts/refunds + webhook, GST invoice / TDS statement PDFs, web push + email notifications, Instagram/YouTube connect, brand dashboard/reporting, Celery jobs (auto-approve, payment expiry).
