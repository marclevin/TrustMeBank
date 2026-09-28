# Payments

MockBank does not let an application debit an account. It lets an application **ask**, and the
customer decides on MockBank's own website. This is payment initiation.

## The flow

```
Your app                         MockBank                          Customer's browser
   │ POST /api/v1/payments ──────▶│ create AWAITING_AUTHORISATION      │
   │◀── 201 {authorisation_url} ──│                                    │
   │ 302 → authorisation_url ─────────────────────────────────────────▶│
   │                              │◀── GET /payments/{id}/authorise ───│
   │                              │──── approval page ────────────────▶│
   │                              │◀── POST approve ───────────────────│
   │                              │ PROCESSING → ledger → COMPLETED    │
   │                              │──── 303 → redirect_uri?payment_id=&status= ─▶│
   │◀── POST webhook payment.completed (signed) ──│                    │
   │ GET /api/v1/payments/{id} ──▶│ (optional confirmation)            │
```

## 1. Create the payment

```
POST /api/v1/payments
Authorization: Bearer mbat_...
Idempotency-Key: 6d9a7d1e-1a2b-4c3d-9e8f-0a1b2c3d4e5f
Content-Type: application/json

{
  "debtor_account_id": "acc_7f3k9d2m1q0z",
  "creditor_account_number": "1000987654",
  "amount": "500.00",
  "currency": "ZAR",
  "reference": "REM-92831",
  "redirect_uri": "http://localhost:5000/payments/return"
}
```

| Field | Rules |
|---|---|
| `debtor_account_id` | One of the consenting customer's accounts (from `GET /api/v1/accounts`) |
| `creditor_account_number` | A 10 digit MockBank account number. Usually your settlement account |
| `amount` | A **string** with up to two decimals, greater than zero, at most `1000000.00`. Floats are rejected |
| `currency` | `ZAR` |
| `reference` | 1 to 35 characters. Appears on both statements. Make it unique per payment so you can reconcile |
| `redirect_uri` | Optional. Where MockBank sends the customer afterwards. Must be a registered redirect URI |

Response `201 Created`:

```json
{
  "payment_id": "pay_3k2j1h0g9f8d",
  "status": "AWAITING_AUTHORISATION",
  "debtor_account_id": "acc_7f3k9d2m1q0z",
  "creditor_account_number": "1000987654",
  "creditor_name": "RemitX (Pty) Ltd",
  "amount": "500.00",
  "currency": "ZAR",
  "reference": "REM-92831",
  "authorisation_url": "http://localhost:8000/payments/pay_3k2j1h0g9f8d/authorise",
  "redirect_uri": "http://localhost:5000/payments/return",
  "failure_reason": null,
  "journal_id": null,
  "created_at": "2026-09-28T10:00:00Z",
  "authorised_at": null,
  "completed_at": null
}
```

Nothing has moved yet. Store `payment_id` against your own order or deposit record.

### Idempotency

Networks fail. If you POST a payment and the response is lost, you do not know whether it was
created. Send an `Idempotency-Key` header (any unique string, a UUID is ideal) and retry with
the same key and body:

- Same key, same body: MockBank returns the original payment with status `200` and the header
  `Idempotent-Replayed: true`.
- Same key, different body: `409 idempotency_key_reused`.

Keys are scoped to your application.

## 2. Send the customer to approve

Redirect the browser to `authorisation_url`. The customer logs in (if needed) and sees:

```
RemitX Demo wants to make the following payment
From:      Everyday Account (1000123456)
To:        1000987654 (RemitX (Pty) Ltd)
Amount:    R500.00
Reference: REM-92831
[Approve] [Reject]
```

Only the owner of the debtor account can see this page. The approval form is protected by the
customer's session and a CSRF token. There is no API to approve a payment.

## 3. States

```
AWAITING_AUTHORISATION ──approve──▶ PROCESSING ──settled──▶ COMPLETED
          │                            │
          └──reject──▶ REJECTED        └──cannot settle──▶ FAILED (failure_reason)
```

| Status | What happened | What you should do |
|---|---|---|
| `AWAITING_AUTHORISATION` | Created, customer has not decided | Wait. Show "pending" |
| `PROCESSING` | Approved, settlement queued | Wait for the webhook or poll. Do not credit yet |
| `COMPLETED` | Money moved. `journal_id` is set | Credit the customer in your system, once |
| `REJECTED` | Customer clicked Reject | Cancel the order |
| `FAILED` | Approved but could not settle, for example `insufficient_funds` | Tell the customer, let them retry |

`COMPLETED`, `REJECTED` and `FAILED` are final.

The instructor may configure a settlement delay. Then approved payments stay in `PROCESSING` for
some seconds before the worker settles them, and the customer returns to your `redirect_uri`
with `status=PROCESSING`. Your code must cope with that: the redirect is not confirmation.

## 4. The customer comes back

After the decision MockBank redirects to
`{redirect_uri}?payment_id=pay_...&status=COMPLETED` (or `PROCESSING`, `REJECTED`, `FAILED`).

Treat these query parameters as a hint for the UI only. Anyone can type that URL. Confirm the
real state with `GET /api/v1/payments/{payment_id}` or wait for the webhook before you credit
anything.

## 5. Confirmation: webhook or polling

Best: handle the `payment.completed` webhook ([Webhooks](webhooks.md)). Also fine: poll
`GET /api/v1/payments/{payment_id}` every few seconds until the status is final. Robust apps do
both: the webhook for speed, a periodic poll of non-final payments for safety.

## The settlement account pattern

Every team has a MockBank business customer with a settlement account. This is the pattern for
a "deposit" into your platform:

1. Your user (a MockBank customer such as Alice) clicks "Deposit R500" in your app.
2. Your app creates a payment from Alice's account to your settlement account with a unique
   reference such as `DEP-000123`, and redirects Alice to the authorisation URL.
3. Alice approves. MockBank debits Alice, credits your settlement account, and records both
   sides in its ledger under one `journal_id`.
4. Your webhook receives `payment.completed`. You verify the signature, check you have not
   processed this event id before, find your deposit record by `payment_id` (or `reference`),
   and increase Alice's balance in **your** ledger by R500.
5. Bank money is now in your settlement account; Alice has R500 of platform balance. If your
   platform mints tokens for that balance, that is a third, separate ledger.

Withdrawals in the other direction are outside the API on purpose: an application cannot move
money out of its settlement account without a customer approving on MockBank. For the course,
simulate a withdrawal by logging in as your business customer and using the **Transfer** page,
or ask your instructor how they would like it handled.

## Reconciliation

Reconciliation means proving that your internal ledger agrees with the bank's ledger.

- Every completed payment has a `journal_id`. Both the debtor's and the creditor's transaction
  carry that `journal_id` and the `payment_id`, so you can match a bank statement line to your
  deposit record exactly.
- With a consent for your business customer (`transactions` scope), pull
  `GET /api/v1/accounts/{settlement_account_id}/transactions` and match each credit by
  `payment_id` or `reference`. Anything you cannot match is an exception to investigate.
- Customers can also transfer money to your settlement account manually from the MockBank
  **Transfer** page, with any reference they like. That is a real-world "EFT deposit". No
  `payment_id` exists for it; you can only match on `reference` and amount. This is what
  reference matching in fintech is about, and it is why references should be unique and
  hard to mistype.
- If the instructor turns on `transaction.created` events for your application, you receive a
  webhook for every transaction on accounts you have consent for, including manual EFTs.
