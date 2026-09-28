# OAuth and consent

TrustMeBank uses the OAuth 2.0 Authorization Code grant with a confidential client. Your backend
holds the `client_secret`; the browser never sees it.

## Scopes

| Scope | What it unlocks |
|---|---|
| `accounts` | List accounts and read account details (without balance) |
| `balances` | Balance endpoint, and the `balance` field on account objects |
| `transactions` | Transaction history |
| `payments` | Create and read payments |

Ask for the smallest set you need. The customer sees one line per scope on the consent screen.

## The flow

```
┌─────────┐                                   ┌──────────┐
│ Browser │                                   │ TrustMeBank │
└────┬────┘                                   └────┬─────┘
     │ 1. GET /oauth/authorize?...&state=S         │
     │──────────────────────────────────────────▶ │
     │ 2. login page (if needed), consent screen   │
     │◀────────────────────────────────────────── │
     │ 3. POST approve                             │
     │──────────────────────────────────────────▶ │
     │ 4. 303 → redirect_uri?code=C&state=S        │
     │◀────────────────────────────────────────── │
┌────┴─────┐                                       │
│ Your app │ 5. check S matches the stored state   │
│ backend  │ 6. POST /oauth/token (code=C, secret) │
│          │──────────────────────────────────────▶│
│          │ 7. access_token, refresh_token         │
│          │◀──────────────────────────────────────│
└──────────┘                                       │
```

### 1. Authorization request

```
GET /oauth/authorize
    ?response_type=code
    &client_id=app_remitx_demo
    &redirect_uri=http://localhost:5000/callback
    &scope=accounts balances transactions payments
    &state=<random, stored in the user's session>
```

| Parameter | Rules |
|---|---|
| `response_type` | Must be `code` |
| `client_id` | Your application id |
| `redirect_uri` | Must exactly equal one of your registered URIs. URL encode it |
| `scope` | Space separated (URL encoded as `%20` or `+`) |
| `state` | Required. Random, unguessable, at least 16 characters |

What can go wrong:

- Unknown `client_id` or unregistered `redirect_uri`: TrustMeBank shows an error page and does
  **not** redirect. It cannot safely send the user to a URI it does not trust.
- Bad `response_type`, unknown scope or missing `state`: TrustMeBank redirects to your
  `redirect_uri` with `?error=invalid_request|invalid_scope|unsupported_response_type&error_description=...&state=...`.
- Customer clicks Reject: `?error=access_denied&state=...`.

### Why `state` matters

Without `state`, an attacker could send a victim a link to `https://yourapp/callback?code=...`
where the code belongs to the attacker's bank account. The victim's session in your app would
then be linked to the attacker's bank data (a login CSRF). `state` binds the callback to the
browser session that started the flow. TrustMeBank cannot check it for you: only your app knows
what it stored.

### 2. Token exchange

```
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=tmbac_...
&redirect_uri=http://localhost:5000/callback
&client_id=app_remitx_demo
&client_secret=tmbsk_remitx_demo_secret
```

You may send `client_id` and `client_secret` as HTTP Basic auth instead of form fields.

Response:

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

Errors follow RFC 6749:

| Status | `error` | Meaning |
|---|---|---|
| 401 | `invalid_client` | Wrong `client_id` or `client_secret`, or the application is inactive |
| 400 | `invalid_grant` | Code unknown, expired (5 minutes), already used, wrong `redirect_uri`, or consent revoked |
| 400 | `invalid_request` | Missing parameter |
| 400 | `unsupported_grant_type` | Only `authorization_code` and `refresh_token` exist |

Using a code twice is treated as an attack: every token issued from that code is revoked.

### 3. Calling the API

```
Authorization: Bearer tmbat_...
```

Store tokens server side, per user. Treat them like passwords.

### 4. Refreshing

Access tokens expire after one hour. When you get `401 invalid_token`, refresh:

```
POST /oauth/token
grant_type=refresh_token&refresh_token=tmbrt_...&client_id=...&client_secret=...
```

You get a new access token **and a new refresh token**. The old refresh token stops working.
Store both new values. Refresh tokens last 30 days.

## Consent lifecycle

- A consent is created when the customer approves. It lasts 90 days.
- If the same customer approves your app again, the previous consent and all its tokens are
  revoked and a new consent is created with the newly requested scopes. Re-authorising is how
  you ask for more scopes.
- The customer can revoke your access at any time under **Connected apps**. Your next API call
  returns `401 invalid_token` with a message saying the consent was revoked. Handle it by
  sending the customer through the flow again.
- `GET /api/v1/me` tells you which customer and consent a token belongs to. Use it right after
  the exchange to link the bank customer to your own user record.

## Connecting your own settlement account

Your settlement account belongs to a business customer that the instructor created for your
team (for the demo application it is `remitx@example.com` / `remitx123`). To read that account
through the API, run the same OAuth flow while logged in as that business customer. You will
usually do this once, store the tokens, and use them for reconciliation. There is no special
grant for it: a business customer consents exactly like a person.
