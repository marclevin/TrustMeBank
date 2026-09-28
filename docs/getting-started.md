# Getting started

This guide takes you from nothing to a working integration: your app connects to a MockBank
customer, reads their accounts, asks them to approve a payment, and receives a signed webhook
when the money moves. Budget 15 minutes.

## What MockBank is

MockBank is a fake bank. It has fake customers with fake ZAR accounts and a real ledger that
moves fake money between them. Your fintech application (a "TPP", third-party provider) talks to
it the way a real app would talk to a bank that supports Open Banking:

```
Your app ── redirect ──▶ MockBank consent screen ── redirect back with code ──▶ Your app
Your app ── POST /oauth/token ──▶ access token
Your app ── GET /api/v1/accounts ──▶ the customer's accounts
Your app ── POST /api/v1/payments ──▶ payment in AWAITING_AUTHORISATION + authorisation_url
Your app ── redirect customer ──▶ MockBank payment screen ── customer approves ──▶ ledger moves money
MockBank ── POST your webhook (HMAC signed) ──▶ Your app credits the customer inside your own system
```

Three kinds of money exist in the course project and it is important not to confuse them:

| Kind | Where it lives | Who moves it |
|---|---|---|
| Bank money | MockBank's ledger (`/api/v1/accounts/.../balance`) | Only MockBank, and only after a customer approves |
| Your app's internal balance | Your own database | Your app, typically when a `payment.completed` webhook arrives |
| Tokens on a blockchain | The chain you use elsewhere in the project | Your app's wallet or smart contract |

A "deposit" in your app means: the customer moved bank money from their MockBank account to
**your** MockBank settlement account, MockBank told you about it, and you then increased the
customer's internal balance. MockBank never knows about your internal balances or tokens.

## What you need

Your instructor registers your application and gives you:

| Item | Example |
|---|---|
| `client_id` | `app_remitx_demo` |
| `client_secret` | `mbsk_remitx_demo_secret` |
| Registered redirect URIs | `http://localhost:5000/callback`, `http://localhost:5000/payments/return` |
| Webhook URL and `webhook_secret` | `http://host.docker.internal:5000/webhooks/mockbank`, `whsec_remitx_demo_secret` |
| Your settlement account | login `remitx@example.com` / `remitx123`, account number `1000987654` |

The values above are the seeded demo application. Use them to try things out before your own
registration exists. The MockBank base URL is whatever your instructor deployed, for example
`http://localhost:8000` when running it yourself.

Test customers: `alice@example.com` / `alice123`, `bob@example.com` / `bob123`,
`carol@example.com` / `carol123`.

## Fastest path: run the example app

```bash
git clone <this repository>
cd examples/python-app
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
MOCKBANK_URL=http://localhost:8000 python app.py
```

Open http://localhost:5000, click **Connect your bank**, log in as Alice, approve, and you are
connected. Then click **Deposit R100** and approve the payment on MockBank. Watch the terminal:
the webhook arrives, the signature verifies, and Alice's internal balance in the example app
increases. The Node version in `examples/node-app` does exactly the same.

Read `examples/python-app/app.py`. It is under 250 lines and contains every call you need.

## Doing it by hand

### 1. Send the customer to MockBank

Build this URL and redirect the browser to it. Generate a random `state`, store it in the
user's session, and check it when they come back.

```
http://localhost:8000/oauth/authorize
  ?response_type=code
  &client_id=app_remitx_demo
  &redirect_uri=http://localhost:5000/callback
  &scope=accounts%20balances%20transactions%20payments
  &state=0f3a9c...
```

The customer logs in and sees a consent screen. On approval MockBank redirects to
`http://localhost:5000/callback?code=mbac_...&state=0f3a9c...`.

### 2. Exchange the code

```bash
curl -X POST http://localhost:8000/oauth/token \
  -d grant_type=authorization_code \
  -d code=mbac_... \
  -d redirect_uri=http://localhost:5000/callback \
  -d client_id=app_remitx_demo \
  -d client_secret=mbsk_remitx_demo_secret
```

```json
{"access_token": "mbat_...", "token_type": "Bearer", "expires_in": 3600,
 "refresh_token": "mbrt_...", "scope": "accounts balances transactions payments",
 "consent_id": "cns_..."}
```

Codes last 5 minutes and work once. Access tokens last an hour; use the refresh token after that.

### 3. Read accounts

```bash
curl http://localhost:8000/api/v1/accounts -H "Authorization: Bearer mbat_..."
```

### 4. Initiate a payment

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "Authorization: Bearer mbat_..." \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d '{"debtor_account_id": "acc_...", "creditor_account_number": "1000987654",
       "amount": "100.00", "currency": "ZAR", "reference": "DEP-0001",
       "redirect_uri": "http://localhost:5000/payments/return"}'
```

The response has `"status": "AWAITING_AUTHORISATION"` and an `authorisation_url`. Redirect the
customer there. They approve. MockBank moves the money, sends the customer back to
`http://localhost:5000/payments/return?payment_id=pay_...&status=COMPLETED` and POSTs a
`payment.completed` event to your webhook.

### 5. Handle the webhook

Verify the `X-MockBank-Signature` header with your `webhook_secret`, then credit the customer
inside your own system. Never trust the browser redirect alone: the customer might close the
tab, and the query string can be forged. The webhook (or `GET /api/v1/payments/{id}`) is the
truth. See [Webhooks](webhooks.md) for the exact verification code.

## Where to go next

- [OAuth and consent](oauth.md): the flow in detail, errors, refresh, revocation
- [Payments](payments.md): states, idempotency, the settlement account pattern, reconciliation
- [Webhooks](webhooks.md): signatures, retries, de-duplication
- [API reference](api-reference.md): every endpoint and field
- [curl examples](curl-examples.md): copy and paste
- Swagger UI at `/docs`: click **Authorize** to run the OAuth flow from the browser and call
  endpoints interactively (uses the demo application)

## Common mistakes

| Symptom | Cause |
|---|---|
| Error page "redirect_uri does not exactly match" | The URI must match a registered one character for character, including the trailing slash and port |
| `invalid_grant` on `/oauth/token` | Code already used, older than 5 minutes, or a different `redirect_uri` than in step 1 |
| `401 invalid_token` | Token expired (refresh it) or the customer revoked your app under Connected apps |
| `403 insufficient_scope` | You did not ask for that scope when the customer consented. Ask again with the right scopes |
| `422 invalid_creditor` | The creditor account number does not exist at MockBank |
| Webhook never arrives | Your URL is not reachable from the MockBank server. If MockBank runs in Docker on your laptop, use `http://host.docker.internal:PORT/...`. Check Admin, Webhook deliveries for the error |
| Signature fails | You hashed a re-serialised JSON body instead of the raw bytes you received |
