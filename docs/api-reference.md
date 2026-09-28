# API reference

Base URL: the MockBank deployment, for example `http://localhost:8000`. Interactive
documentation with request builders is at `/docs` (Swagger UI) and the raw schema at
`/openapi.json`.

## Conventions

- JSON in and out, UTF-8. Timestamps are UTC ISO 8601 with a `Z` suffix.
- Money is a string with two decimals: `"15240.50"`. Debits on transactions are negative.
  Currency is always `ZAR`. Never send a JSON number for an amount.
- IDs are prefixed strings: `acc_`, `txn_`, `jnl_`, `pay_`, `cns_`, `evt_`, `app_`, `cus_`.
- Authentication: `Authorization: Bearer <access_token>` on every `/api/v1` endpoint.
- Rate limit: 300 requests per minute per token. Over the limit you get `429 rate_limited`
  with a `Retry-After` header.

## Errors

```json
{"error": {"code": "insufficient_scope", "message": "This endpoint requires the 'balances' scope.", "details": {"required_scope": "balances", "granted_scopes": ["accounts"]}}}
```

| Status | Code | When |
|---|---|---|
| 400 | `invalid_request` | Malformed cursor or parameter |
| 401 | `missing_token` | No `Authorization` header |
| 401 | `invalid_token` | Expired, revoked, unknown, or consent revoked. Message explains which |
| 403 | `insufficient_scope` | Consent lacks the scope |
| 404 | `not_found` | No such resource for this customer or application |
| 409 | `idempotency_key_reused` | Same `Idempotency-Key`, different body |
| 422 | `validation_error` | Schema violation. `details.fields` lists each problem |
| 422 | `invalid_amount`, `invalid_creditor`, `invalid_redirect_uri` | Business validation |
| 429 | `rate_limited` | |

`POST /oauth/token` uses the OAuth error shape instead: `{"error": "invalid_grant", "error_description": "..."}`.

## OAuth

### `GET /oauth/authorize`

Browser endpoint. See [OAuth and consent](oauth.md).

### `POST /oauth/token`

Form encoded. Grants: `authorization_code` (`code`, `redirect_uri`) and `refresh_token`
(`refresh_token`). Client credentials as form fields or HTTP Basic.

Response: `access_token`, `token_type` (`Bearer`), `expires_in` (seconds), `refresh_token`,
`scope`, `consent_id`.

## Identity

### `GET /api/v1/me`

Any valid token.

```json
{"customer_id": "cus_...", "full_name": "Alice Ndlovu", "consent_id": "cns_...",
 "scopes": ["accounts", "balances"], "consent_expires_at": "2026-12-27T10:00:00Z",
 "application_id": "app_remitx_demo"}
```

## Accounts

### `GET /api/v1/accounts`

Scope `accounts`. Returns `{"data": [Account, ...]}` for the consenting customer's active
accounts.

Account object:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Use as `debtor_account_id` |
| `name` | string | |
| `account_number` | string | 10 digits. Use as `creditor_account_number` when paying this account |
| `account_type` | string | `current`, `savings`, `business` |
| `currency` | string | `ZAR` |
| `balance` | string | Only present with the `balances` scope |
| `created_at` | string | |

### `GET /api/v1/accounts/{account_id}`

Scope `accounts`. One account object.

### `GET /api/v1/accounts/{account_id}/balance`

Scope `balances`.

```json
{"account_id": "acc_...", "currency": "ZAR", "balance": "15240.50", "as_of": "2026-09-28T10:00:00Z"}
```

### `GET /api/v1/accounts/{account_id}/transactions`

Scope `transactions`. Newest first.

| Query parameter | Notes |
|---|---|
| `limit` | 1 to 200, default 50 |
| `cursor` | `next_cursor` from the previous page |
| `from_date` | `YYYY-MM-DD`, inclusive, UTC |
| `to_date` | `YYYY-MM-DD`, inclusive, UTC |

```json
{"data": [Transaction, ...], "next_cursor": "MTIzNA", "has_more": true}
```

Transaction object:

| Field | Notes |
|---|---|
| `id` | |
| `account_id` | |
| `type` | `DEBIT` or `CREDIT` |
| `amount` | Signed string. `"-500.00"` for a debit |
| `currency` | `ZAR` |
| `description` | For example `Payment via RemitX Demo`, `Card purchase: Woolworths Food` |
| `reference` | The payment or transfer reference, or `null` |
| `counterparty` | `{"name": "...", "account_number": "..."}`: the other side of the transfer |
| `balance_after` | Account balance after this transaction |
| `payment_id` | Set when this transaction settled a payment, else `null` |
| `journal_id` | Groups the debit and credit sides of one transfer |
| `booked_at` | |

## Payments

### `POST /api/v1/payments`

Scope `payments`. Header `Idempotency-Key` recommended. Body and rules in
[Payments](payments.md). Returns `201` with the payment object, or `200` with header
`Idempotent-Replayed: true` on a replay.

Payment object:

| Field | Notes |
|---|---|
| `payment_id` | |
| `status` | `AWAITING_AUTHORISATION`, `PROCESSING`, `COMPLETED`, `REJECTED`, `FAILED` |
| `debtor_account_id`, `creditor_account_number`, `creditor_name` | |
| `amount`, `currency`, `reference` | As submitted |
| `authorisation_url` | Send the customer here |
| `redirect_uri` | Where the customer is sent afterwards, or `null` |
| `failure_reason` | `insufficient_funds`, `account_closed`, or `null` |
| `journal_id` | Set once `COMPLETED` |
| `created_at`, `authorised_at`, `completed_at` | |

### `GET /api/v1/payments`

Scope `payments`. Payments your application created for this customer, newest first.
Query: `status`, `limit`, `cursor`. Returns `{"data": [...], "next_cursor": ..., "has_more": ...}`.

### `GET /api/v1/payments/{payment_id}`

Scope `payments`. `404` if the payment belongs to another application or customer.

## System

### `GET /health`

`{"status": "ok", "version": "1.0.0"}`. No authentication.

## Webhooks

Outbound only. See [Webhooks](webhooks.md).
