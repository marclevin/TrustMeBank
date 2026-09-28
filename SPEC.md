# TrustMeBank Specification

TrustMeBank is a simulated South African bank built for a university Financial Technology course.
Student teams build third-party fintech applications (TPPs) that integrate with TrustMeBank through
an Open Banking style API: OAuth consent, account information, payment initiation and webhooks.

TrustMeBank never handles real money. Every customer, account and transaction is fake.

This document is the single source of truth for behaviour. `ARCHITECTURE.md` explains how the
system is built and `IMPLEMENTATION_PLAN.md` explains the order in which it was built.

---

## 1. Actors

| Actor | Description | How they interact |
|---|---|---|
| **Customer** | A fake bank customer (for example `alice@example.com`) | Logs in to the TrustMeBank web UI, views accounts, approves consents and payments |
| **TPP (third-party application)** | A student team's fintech app | Calls the JSON API with OAuth access tokens, receives webhooks |
| **Administrator** | The course instructor | Uses the admin UI or CLI to seed data, register applications and inspect activity |

## 2. Scope and non-goals

In scope:

- Fake customers with ZAR accounts and a double-entry ledger
- OAuth 2.0 Authorization Code flow with a consent screen
- Account information API (accounts, balances, transactions)
- Payment initiation with explicit customer authorisation on the bank's site
- HMAC-signed, retryable webhooks
- Customer web UI and admin UI, both server rendered
- Seed data, reset command, Docker Compose deployment, OpenAPI docs

Out of scope (deliberately):

- Real Open Banking specifications (UK OBIE, Berlin Group). We borrow the concepts only.
- PKCE, dynamic client registration, JWTs, mutual TLS, request signing
- Multi-currency. Every account is ZAR.
- Standing orders, scheduled payments, card products, interest, fees
- Payments to accounts outside TrustMeBank. The simulated world is closed.
- Horizontal scaling. One application process is enough for a class.

## 3. Data model

All primary keys are random, prefixed string IDs (for example `acc_7f3k9d2m1q`). The prefix makes
IDs self-describing in logs and API responses.

All timestamps are UTC and rendered as ISO 8601 with a `Z` suffix.

All monetary amounts are stored as `NUMERIC(18, 2)` and handled in Python as `decimal.Decimal`.
They are serialised as strings with exactly two decimal places (`"500.00"`). Floats are never used.

### 3.1 Entities

**Customer** `cus_`

| Field | Notes |
|---|---|
| id | primary key |
| email | unique, login identifier |
| full_name | display name, also used as counterparty name on other customers' statements |
| password_hash | bcrypt |
| kind | `personal`, `business` or `system`. System customers own the internal treasury and clearing accounts and cannot log in |
| is_active | inactive customers cannot log in and their tokens are rejected |
| created_at | |

**Account** `acc_`

| Field | Notes |
|---|---|
| id | primary key |
| customer_id | owner |
| name | for example `Everyday Account` |
| account_number | 10 digit string, unique, starts with `10` |
| account_type | `current`, `savings`, `business`, `system` |
| currency | always `ZAR` |
| balance | cached balance, always equal to the sum of the account's transactions |
| allow_overdraft | if true the balance may go negative. Only system accounts are seeded with this |
| status | `active` or `closed` |
| created_at | |

**Journal** `jnl_`

A journal is one balanced ledger event. The sum of amounts across its transactions is always zero.

| Field | Notes |
|---|---|
| id | primary key |
| kind | `transfer`, `payment`, `seed`, `adjustment` |
| description | |
| reference | free text, optional |
| payment_id | set when the journal settles a payment, unique |
| booked_at | |

**Transaction** `txn_`

One transaction is one account's side of a journal. This is what the API exposes.

| Field | Notes |
|---|---|
| id | primary key |
| seq | monotonically increasing integer used for cursor pagination |
| journal_id | |
| account_id | |
| amount | signed. Negative is a debit, positive is a credit |
| balance_after | account balance immediately after this transaction |
| description | |
| reference | |
| counterparty_name | |
| counterparty_account_number | |
| booked_at | |

**Application** `app_` (the TPP)

| Field | Notes |
|---|---|
| id | primary key, also the OAuth `client_id` |
| name | shown on consent screens |
| owner_label | free text, for example `Team 4 (RemitX)` |
| client_secret_hash | SHA-256. The plaintext secret is shown once at creation and on regeneration |
| redirect_uris | list of exact URIs. Used for both OAuth redirects and payment return redirects |
| webhook_url | optional |
| webhook_secret | plaintext, needed to sign deliveries. Prefixed `whsec_` |
| send_transaction_events | opt-in for `transaction.created` webhooks |
| is_active | inactive applications cannot authenticate |
| created_at | |

**Consent** `cns_`

| Field | Notes |
|---|---|
| id | primary key |
| customer_id | |
| application_id | |
| scopes | list of granted scopes |
| status | `active` or `revoked` |
| expires_at | 90 days after creation |
| created_at, revoked_at | |

**AuthorizationCode**

| Field | Notes |
|---|---|
| code_hash | SHA-256 of the code, primary key |
| consent_id, application_id | |
| redirect_uri | must be repeated at token exchange |
| expires_at | 5 minutes |
| used_at | single use |

**Token**

| Field | Notes |
|---|---|
| token_hash | SHA-256 of the token, primary key |
| kind | `access` or `refresh` |
| consent_id, application_id | |
| expires_at | access 1 hour, refresh 30 days (configurable) |
| revoked_at | |

**Payment** `pay_`

| Field | Notes |
|---|---|
| id | primary key |
| application_id, consent_id | who initiated it |
| debtor_account_id | must belong to the consenting customer |
| creditor_account_number | must be an existing TrustMeBank account |
| amount, currency | |
| reference | free text shown on both statements |
| status | see state machine |
| failure_reason | set when `FAILED` |
| idempotency_key | unique per application when present |
| request_hash | detects idempotency key reuse with a different body |
| redirect_uri | optional, must be a registered redirect URI |
| journal_id | set when settled |
| process_after | set when `PROCESSING` |
| created_at, authorised_at, completed_at | |

**WebhookDelivery** `evt_`

| Field | Notes |
|---|---|
| id | primary key, also the event id sent to the TPP |
| application_id | |
| event_type | `payment.completed`, `payment.failed`, `payment.rejected`, `transaction.created` |
| payload | JSON body exactly as sent |
| status | `pending`, `delivered`, `failed` |
| attempts | |
| next_attempt_at | |
| last_status_code, last_error, last_attempt_at, delivered_at | |

**AuditLog**

| Field | Notes |
|---|---|
| id | serial |
| actor_type | `customer`, `application`, `admin`, `system` |
| actor_id | |
| action | for example `payment.approved` |
| target_type, target_id | |
| details | JSON |
| ip | |
| created_at | |

### 3.2 Seed data

The seed command creates the following. Passwords are development only.

| Customer | Password | Accounts |
|---|---|---|
| `alice@example.com` (Alice Ndlovu) | `alice123` | Everyday Account `1000123456` (R15,240.50), Savings Account `1000123457` (R42,000.00) |
| `bob@example.com` (Bob van der Merwe) | `bob123` | Everyday Account `1000234567` (R3,870.25) |
| `carol@example.com` (Carol Pillay) | `carol123` | Everyday Account `1000345678` (R980.00) |
| `remitx@example.com` (RemitX (Pty) Ltd, business) | `remitx123` | RemitX Settlement Account `1000987654` (R250,000.00) |
| TrustMeBank Treasury (system) | cannot log in | `1000000000`, overdraft allowed |
| TrustMeBank Clearing (system) | cannot log in | `1000000001`, overdraft allowed |

Every seeded customer account has roughly 60 days of realistic history (salary credits, card
purchases, transfers). Seed history is posted through the normal ledger, so balances always
reconcile.

One application is seeded for the example apps and the Swagger UI:

| Field | Value |
|---|---|
| client_id | `app_remitx_demo` |
| client_secret | `tmbsk_remitx_demo_secret` |
| redirect URIs | `http://localhost:5000/callback`, `http://localhost:5000/payments/return`, `http://localhost:3000/callback`, `http://localhost:3000/payments/return`, `{PUBLIC_BASE_URL}/docs/oauth2-redirect` |
| webhook URL | `http://host.docker.internal:5000/webhooks/trustmebank` |
| webhook secret | `whsec_remitx_demo_secret` |

Admin login uses the `ADMIN_PASSWORD` environment variable.

## 4. Ledger behaviour

- The ledger is double entry. Money is never created or destroyed by a transfer: every journal
  has exactly one debit transaction and one credit transaction whose amounts sum to zero.
- Seeding money is a transfer from the Treasury account, which is allowed to go negative.
  Card purchases in seed history are transfers to the Clearing account with a merchant name as
  the counterparty.
- `account.balance` is a cache. It is updated in the same database transaction as the posting
  and is asserted against `SUM(transactions.amount)` in tests and in the admin ledger check.
- Posting a transfer:
  1. Lock both account rows with `SELECT ... FOR UPDATE`, in ascending id order to avoid deadlocks.
  2. Reject if either account is closed or the amount is not positive.
  3. Reject with `insufficient_funds` if the debit account's balance would go negative and
     neither `account.allow_overdraft` nor the global `ALLOW_NEGATIVE_BALANCES` setting is set.
  4. Insert the journal and two transactions, update both balances, commit.
- A customer can transfer between TrustMeBank accounts from the web UI. This is how students can
  simulate a manual EFT deposit to their settlement account with a reference.

## 5. OAuth lifecycle

TrustMeBank implements the OAuth 2.0 Authorization Code grant with confidential clients.

### 5.1 Scopes

| Scope | Grants |
|---|---|
| `accounts` | `GET /api/v1/accounts`, `GET /api/v1/accounts/{id}` (without balance) |
| `balances` | `GET /api/v1/accounts/{id}/balance`, and the `balance` field on account objects |
| `transactions` | `GET /api/v1/accounts/{id}/transactions` |
| `payments` | `POST /api/v1/payments`, `GET /api/v1/payments`, `GET /api/v1/payments/{id}` |

`GET /api/v1/me` requires any valid token and returns the consent's customer and scopes.

Scopes are enforced per endpoint. A token without the required scope receives
`403 insufficient_scope`.

### 5.2 Authorization request

```
GET /oauth/authorize?response_type=code
    &client_id=app_...
    &redirect_uri=https://app.example/callback
    &scope=accounts%20balances%20transactions%20payments
    &state=8f2c...
```

Validation, in order:

1. `client_id` must identify an active application. Otherwise an error page is shown.
2. `redirect_uri` must exactly match one of the application's registered URIs. Otherwise an
   error page is shown. TrustMeBank never redirects to an unregistered URI.
3. `response_type` must be `code`, `scope` must be a non-empty subset of the known scopes and
   `state` must be present. Otherwise TrustMeBank redirects to `redirect_uri` with
   `error=invalid_request` (or `invalid_scope`) and the `state` if one was supplied.
4. If the customer is not logged in they are sent to the login page and then returned here.
5. The consent screen lists the application name and a plain-language line per scope.

### 5.3 Consent decision

- **Approve**: any existing active consent for the same customer and application is revoked
  (its tokens stop working), a new consent is created with the requested scopes, an
  authorization code is issued and the browser is redirected to
  `{redirect_uri}?code=...&state=...`.
- **Deny**: the browser is redirected to `{redirect_uri}?error=access_denied&state=...`.

`state` is required. TrustMeBank echoes it back unchanged. The TPP must compare it with the value
it stored before the redirect. This is the TPP's CSRF protection; TrustMeBank cannot do it for them.

### 5.4 Token exchange

```
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&code=...&redirect_uri=...&client_id=...&client_secret=...
```

Client credentials may also be sent as HTTP Basic. Success:

```json
{
  "access_token": "tmbat_...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "tmbrt_...",
  "scope": "accounts balances transactions payments",
  "consent_id": "cns_..."
}
```

Rules:

- Codes expire after 5 minutes and are single use. Reusing a code revokes every token that was
  issued from it (RFC 6749 section 4.1.2).
- `redirect_uri` must equal the one used in the authorization request.
- `client_secret` must match. Failures use the RFC 6749 error body:
  `{"error": "invalid_client", "error_description": "..."}` with status 400 or 401.
- `grant_type=refresh_token` with `refresh_token`, `client_id`, `client_secret` issues a new
  access token and a new refresh token, and revokes the old refresh token.

### 5.5 Using a token

`Authorization: Bearer tmbat_...`

A request is rejected with `401 invalid_token` if the token is unknown, expired or revoked,
if the consent is revoked or expired, or if the application or customer is inactive.

## 6. Consent lifecycle

```
(none) --approve--> active --revoke (customer UI, admin, or re-consent)--> revoked
                     |
                     +--90 days--> expired (treated as revoked)
```

- A customer sees every active consent under "Connected apps" and can revoke each one.
- Revoking a consent immediately invalidates its tokens and codes.
- Pending payments created under a revoked consent can still be approved or rejected by the
  customer; the authorisation page is the customer's explicit decision.

## 7. API endpoints

All API responses are JSON. Base path `/api/v1`. Bearer token required unless stated.

| Method | Path | Scope | Purpose |
|---|---|---|---|
| GET | `/api/v1/me` | any | Customer id, name, consent id, scopes, consent expiry |
| GET | `/api/v1/accounts` | accounts | List the consenting customer's active accounts |
| GET | `/api/v1/accounts/{account_id}` | accounts | One account |
| GET | `/api/v1/accounts/{account_id}/balance` | balances | Balance |
| GET | `/api/v1/accounts/{account_id}/transactions` | transactions | Paginated transactions, newest first |
| POST | `/api/v1/payments` | payments | Initiate a payment |
| GET | `/api/v1/payments` | payments | List payments created by this application for this consent |
| GET | `/api/v1/payments/{payment_id}` | payments | Payment status |
| POST | `/oauth/token` | client auth | Token exchange and refresh |
| GET | `/health` | none | Liveness |

Non-JSON, browser facing routes: `/oauth/authorize`, `/login`, `/logout`, `/accounts`,
`/accounts/{id}`, `/transfer`, `/connected-apps`, `/payments/{id}/authorise`, `/admin/...`,
`/docs`, `/guide/...`.

### 7.1 Account object

```json
{
  "id": "acc_7f3k9d2m1q",
  "name": "Everyday Account",
  "account_number": "1000123456",
  "account_type": "current",
  "currency": "ZAR",
  "balance": "15240.50",
  "created_at": "2026-01-14T09:12:44Z"
}
```

`balance` is only present when the consent includes the `balances` scope.

### 7.2 Balance object

```json
{ "account_id": "acc_7f3k9d2m1q", "currency": "ZAR", "balance": "15240.50", "as_of": "2026-09-28T10:00:00Z" }
```

### 7.3 Transaction list

`GET /api/v1/accounts/{id}/transactions?limit=50&cursor=...&from_date=2026-09-01&to_date=2026-09-30`

- `limit` 1 to 200, default 50.
- `cursor` is the `next_cursor` from the previous page. It is opaque.
- `from_date` and `to_date` are inclusive calendar dates (UTC).

```json
{
  "data": [
    {
      "id": "txn_9d8s7f6g5h",
      "account_id": "acc_7f3k9d2m1q",
      "type": "DEBIT",
      "amount": "-500.00",
      "currency": "ZAR",
      "description": "Payment to RemitX (Pty) Ltd",
      "reference": "REM-92831",
      "counterparty": { "name": "RemitX (Pty) Ltd", "account_number": "1000987654" },
      "balance_after": "14740.50",
      "payment_id": "pay_abc",
      "journal_id": "jnl_xyz",
      "booked_at": "2026-09-28T10:00:00Z"
    }
  ],
  "next_cursor": "MTIzNA",
  "has_more": true
}
```

### 7.4 Payment object

```json
{
  "payment_id": "pay_abc",
  "status": "AWAITING_AUTHORISATION",
  "debtor_account_id": "acc_7f3k9d2m1q",
  "creditor_account_number": "1000987654",
  "creditor_name": "RemitX (Pty) Ltd",
  "amount": "500.00",
  "currency": "ZAR",
  "reference": "REM-92831",
  "authorisation_url": "http://localhost:8000/payments/pay_abc/authorise",
  "redirect_uri": "http://localhost:5000/payments/return",
  "failure_reason": null,
  "journal_id": null,
  "created_at": "...",
  "authorised_at": null,
  "completed_at": null
}
```

## 8. Payment initiation and state machine

### 8.1 Create

```
POST /api/v1/payments
Authorization: Bearer tmbat_...
Idempotency-Key: 4c2e1a7b-...      (optional but recommended)

{
  "debtor_account_id": "acc_7f3k9d2m1q",
  "creditor_account_number": "1000987654",
  "amount": "500.00",
  "currency": "ZAR",
  "reference": "REM-92831",
  "redirect_uri": "http://localhost:5000/payments/return"
}
```

Validation:

- `debtor_account_id` must be an active account owned by the consenting customer (404 otherwise,
  so the API does not reveal other customers' account ids).
- `creditor_account_number` must be an existing active TrustMeBank account and must differ from the
  debtor account (422 `invalid_creditor`).
- `amount` must be a string, positive, at most two decimals, at most 1,000,000.00.
- `currency` must be `ZAR`.
- `reference` 1 to 35 characters (a common bank statement limit).
- `redirect_uri`, if present, must exactly match a registered redirect URI.
- `Idempotency-Key`: if the same application sends the same key again with the same body, the
  original payment is returned with status 200 and header `Idempotent-Replayed: true`. With a
  different body the request is rejected with 409 `idempotency_key_reused`.

Response `201` with the payment object. The TPP redirects the customer to `authorisation_url`.

### 8.2 States

```
AWAITING_AUTHORISATION --approve--> PROCESSING --settled--> COMPLETED
          |                             |
          +--reject--> REJECTED         +--insufficient funds / closed account--> FAILED
```

| State | Meaning |
|---|---|
| `AWAITING_AUTHORISATION` | Created by the TPP. Nothing has moved. |
| `PROCESSING` | The customer approved. Settlement is queued. With the default configuration settlement happens immediately in the same request, so TPPs rarely observe this state, but they must handle it. |
| `COMPLETED` | Ledger updated. `journal_id` is set. Terminal. |
| `REJECTED` | The customer clicked Reject. Terminal. |
| `FAILED` | Settlement failed (`insufficient_funds`, `account_closed`). `failure_reason` is set. Terminal. |

`PAYMENT_PROCESSING_DELAY_SECONDS` (default 0) can be raised so payments sit in `PROCESSING`
for a while. This lets the instructor force students to rely on webhooks or polling instead
of assuming completion when the browser returns.

### 8.3 Authorisation page

`GET /payments/{payment_id}/authorise` requires a logged-in customer who owns the debtor
account. It shows the application name, from account, creditor account number and name,
amount and reference, with Approve and Reject buttons.

- Approve (POST, CSRF protected): status becomes `PROCESSING` and settlement is attempted.
- Reject (POST): status becomes `REJECTED`.
- If the payment is not `AWAITING_AUTHORISATION` the page shows the current status instead.

After the decision the customer is redirected to `{redirect_uri}?payment_id=...&status=...`
if the payment has a redirect URI, otherwise a confirmation page is shown.

### 8.4 Settlement

Settlement is atomic and idempotent:

1. Lock the payment row. If status is not `PROCESSING`, stop (another worker or request has
   already handled it).
2. Post a transfer through the ledger (section 4). This locks both accounts.
3. On success: status `COMPLETED`, `journal_id` set, webhook `payment.completed` queued.
4. On `insufficient_funds` or a closed account: status `FAILED`, `failure_reason` set, webhook
   `payment.failed` queued.
5. Everything in steps 1 to 4 is one database transaction. The `journals.payment_id` unique
   constraint is a second line of defence against double settlement.

Only settlement moves money. There is no API that debits an account without a customer
clicking Approve on TrustMeBank's own site.

## 9. Webhooks

### 9.1 Events

| Event | When | `data` |
|---|---|---|
| `payment.completed` | Settlement succeeded | payment object fields plus `journal_id` |
| `payment.failed` | Settlement failed | payment object fields plus `failure_reason` |
| `payment.rejected` | Customer rejected | payment object fields |
| `transaction.created` | A transaction was posted to an account whose owner has an active consent with the `transactions` scope for this application. Opt-in per application. | transaction object |

Envelope:

```json
{
  "id": "evt_5h4g3f2d1s",
  "event": "payment.completed",
  "created_at": "2026-09-28T10:00:01Z",
  "data": { "payment_id": "pay_abc", "status": "COMPLETED", "amount": "500.00", "currency": "ZAR", "reference": "REM-92831", "...": "..." }
}
```

### 9.2 Delivery

- `POST {webhook_url}` with `Content-Type: application/json`, timeout 10 seconds.
- Headers: `X-TrustMeBank-Event`, `X-TrustMeBank-Delivery-Id`, `X-TrustMeBank-Signature`.
- Any 2xx response marks the delivery `delivered`. Anything else, or a network error, schedules a
  retry after 30s, 2m, 10m, 30m and 1h. After 6 failed attempts the delivery is `failed` and the
  administrator can retry it manually.
- Deliveries are written to the database in the same transaction as the event that caused them
  (transactional outbox), so an event can never be lost between the ledger and the webhook.
- The TPP must treat deliveries as at-least-once and de-duplicate on `id`.

### 9.3 Signature

```
X-TrustMeBank-Signature: t=1727517601,v1=5257a869e7ecebeda32affa62cdca3fa51cad7e77a0e56ff536d0ce8e108d8c2
```

`v1 = HMAC_SHA256(key = webhook_secret, message = f"{t}.{raw_request_body}")` in lowercase hex.
The TPP recomputes it over the raw bytes it received and compares with a constant-time
comparison. It should also reject timestamps older than 5 minutes to limit replay.

## 10. Web UI

Customer pages (session cookie, login required):

| Page | Purpose |
|---|---|
| `/login`, `/logout` | Email and password login |
| `/accounts` | Accounts and balances |
| `/accounts/{id}` | Transaction history |
| `/transfer` | Transfer to any TrustMeBank account number with a reference |
| `/connected-apps` | Active consents with a Revoke button |
| `/oauth/authorize` | Consent screen |
| `/payments/{id}/authorise` | Payment approval screen |

Admin pages (`/admin`, password from `ADMIN_PASSWORD`):

| Page | Purpose |
|---|---|
| Dashboard | Counts, ledger integrity check, reset buttons |
| Customers | List, create, reset password, deactivate; per customer: create account, top up, seed history |
| Applications | List, register (optionally with a business customer and settlement account), regenerate secret, rotate webhook secret, edit redirect URIs and webhook URL, deactivate |
| Consents | List and revoke |
| Payments | List with status filter, detail with related webhook deliveries |
| Webhook deliveries | List with status filter, detail with payload and last response, retry |
| Audit log | Newest first |

Reset options:

- **Reset activity**: deletes payments, consents, codes, tokens, webhook deliveries, journals,
  transactions and audit entries, then re-seeds balances and history for the seeded customers.
  Customers and applications are kept. Accounts created by the admin are kept with a zero
  balance.
- **Full reset**: drops all data and re-runs the seed.

CLI equivalents: `tmb seed`, `tmb reset-activity`, `tmb reset --yes`.

## 11. Error format

API errors (everything under `/api/v1`):

```json
{
  "error": {
    "code": "insufficient_scope",
    "message": "This endpoint requires the 'balances' scope.",
    "details": {}
  }
}
```

| Status | Codes |
|---|---|
| 400 | `invalid_request` |
| 401 | `invalid_token`, `missing_token` |
| 403 | `insufficient_scope` |
| 404 | `not_found` |
| 409 | `idempotency_key_reused` |
| 422 | `validation_error` (details lists field errors), `invalid_creditor`, `invalid_amount`, `invalid_redirect_uri` |
| 429 | `rate_limited` |

`POST /oauth/token` uses the RFC 6749 shape instead: `{"error": "invalid_grant", "error_description": "..."}`.

Browser routes render HTML error pages.

## 12. Security assumptions and rules

- Passwords are hashed with bcrypt. Client secrets, authorization codes and tokens are stored
  as SHA-256 hashes. Webhook secrets must be stored in plaintext because TrustMeBank signs with them.
- Authorization codes: 5 minutes, single use, bound to the client and redirect URI.
- Access tokens: 1 hour. Refresh tokens: 30 days, rotated on use.
- Redirect URIs: exact string match against the registered list, for OAuth and for payments.
- Scopes are checked on every API endpoint.
- Payment authorisation happens only through the bank's own page, behind the customer's
  session and a CSRF token. There is no API to approve a payment.
- Negative balances are refused unless `ALLOW_NEGATIVE_BALANCES=true` or the account is a
  system account with `allow_overdraft`.
- Payment creation is idempotent per application through `Idempotency-Key`. Settlement is
  idempotent through the status check under a row lock and the unique `journals.payment_id`.
- Webhooks are HMAC-SHA256 signed with a per application secret.
- Rate limiting: 300 API requests per minute per token or IP, 20 login attempts per minute per
  IP, 60 token requests per minute per IP. In memory, per process.
- Audit log records logins, consents, payments, admin actions and webhook outcomes.
- Secrets come from environment variables: `SECRET_KEY`, `ADMIN_PASSWORD`, `DATABASE_URL`.
- The deployment is assumed to sit behind HTTPS in production (`SESSION_COOKIE_SECURE=true`).
  Students' apps run on `localhost`, so `http://localhost...` redirect URIs are allowed.
- This is a teaching system. It is not hardened against a hostile operator and it should not be
  exposed with the default admin password.

## 13. Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://trustmebank:trustmebank@db:5432/trustmebank` | |
| `SECRET_KEY` | required | Signs session cookies |
| `ADMIN_PASSWORD` | required | Admin UI login |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Used to build `authorisation_url` and docs |
| `SEED_ON_STARTUP` | `true` | Seed if the database is empty |
| `ALLOW_NEGATIVE_BALANCES` | `false` | |
| `ACCESS_TOKEN_TTL_SECONDS` | `3600` | |
| `REFRESH_TOKEN_TTL_SECONDS` | `2592000` | |
| `AUTH_CODE_TTL_SECONDS` | `300` | |
| `CONSENT_TTL_DAYS` | `90` | |
| `PAYMENT_PROCESSING_DELAY_SECONDS` | `0` | |
| `WEBHOOK_WORKER_ENABLED` | `true` | Background thread for webhooks and delayed settlement |
| `SESSION_COOKIE_SECURE` | `false` | Set true behind HTTPS |
| `LOG_LEVEL` | `INFO` | |
