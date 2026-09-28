# Administrator guide

Everything an instructor needs to run TrustMeBank for a class. Nothing here requires touching the
database directly.

## Deploying

```bash
git clone <this repository> trustmebank && cd trustmebank
cp .env.example .env
# edit .env: set SECRET_KEY, ADMIN_PASSWORD and PUBLIC_BASE_URL
docker compose up -d
```

On first start the app container waits for PostgreSQL, applies migrations, seeds the demo data
and starts serving on port 8000. Visit `PUBLIC_BASE_URL/admin` and log in with
`ADMIN_PASSWORD`.

For a class, put it behind HTTPS (Caddy example):

```
trustmebank.example.ac.za {
    reverse_proxy localhost:8000
}
```

and set `PUBLIC_BASE_URL=https://trustmebank.example.ac.za` and `SESSION_COOKIE_SECURE=true`.
Restart with `docker compose up -d`.

Upgrading: `git pull && docker compose build && docker compose up -d`. Migrations run
automatically.

Backups: `docker compose exec db pg_dump -U trustmebank trustmebank > trustmebank-$(date +%F).sql`.

Logs: `docker compose logs -f app`.

## Onboarding a team

Admin, **Applications**, **Register an application**:

1. Name (what customers see on consent screens), team label, redirect URIs (one per line,
   exact match), webhook URL (optional now, editable later).
2. Optionally fill in **Settlement account**: an email for the team's business customer, a
   password (or leave blank to generate one) and an opening balance. This creates
   `<Name> (Pty) Ltd` with a settlement account in one step.
3. Submit. The next page shows `client_id`, `client_secret`, `webhook_secret` and the
   settlement login. **Copy the client secret now**; it is stored hashed and cannot be shown
   again. If it is lost, open the application and click **Regenerate**.

Send the team the credentials, the base URL and a link to `/guide/getting-started`.

From the command line instead:

```bash
docker compose exec app tmb create-app --name RemitX \
  --redirect-uri http://localhost:5000/callback --redirect-uri http://localhost:5000/payments/return \
  --webhook-url https://remitx.example/webhooks/trustmebank \
  --settlement-email remitx-ops@example.com --settlement-balance 10000.00
```

## Day-to-day

| Task | Where |
|---|---|
| Create a test customer with realistic history | Customers, **Create a customer** (choose a history style) |
| Give a customer more money | Customer page, **Top up** next to the account |
| Add an account to a customer | Customer page, **Add an account** |
| Reset a student's password | Customer page, **Reset password** |
| Change a team's redirect URIs or webhook URL | Application page, **Settings** |
| Turn on `transaction.created` events for a team | Application page, checkbox |
| See why a webhook is not arriving | **Webhook deliveries**, filter `failed` or `pending`, open the delivery for the last error and response body |
| Retry a failed webhook | Delivery page, **Retry now** |
| Inspect a payment end to end | **Payments**, open it: ledger legs, deliveries and audit trail |
| Revoke a consent | **Consents** or the customer page |
| Check the ledger | Dashboard, **Ledger integrity** (also `tmb check-ledger`) |
| Audit who did what | **Audit log**, filter by action prefix such as `payment.` or `admin.` |

## Resetting

Dashboard, **Reset the environment**, type `RESET`:

- **Reset activity**: wipes payments, consents, tokens, webhook deliveries, the ledger and the
  audit log. Keeps customers, accounts and applications. Seeded customers get their balances and
  history back; other accounts end at zero (top them up afterwards). Use this between
  assignments or when the seeded users have been drained.
- **Full reset**: everything goes, including the applications you registered. The seed runs
  again.

CLI: `docker compose exec app tmb reset-activity` or `tmb reset --yes`.

## Configuration knobs worth knowing

| Variable | Effect |
|---|---|
| `PAYMENT_PROCESSING_DELAY_SECONDS` | Set to, say, `20` to hold approved payments in `PROCESSING` before settling. Students who assume the browser redirect means "done" will get caught |
| `ALLOW_NEGATIVE_BALANCES` | `true` lets any account overdraw. Off by default so `FAILED` with `insufficient_funds` can be demonstrated |
| `ACCESS_TOKEN_TTL_SECONDS` | Shorten to force refresh handling |
| `SEED_ON_STARTUP` | Set `false` once the class is running if you do not want the seed re-checked on each restart (it is idempotent either way) |

## Capacity

One container comfortably serves a class of 100 students. The rate limiter allows 300 API
requests per minute per token, which is far more than a student app needs. If the seeded
customers get drained by many teams testing payments, top them up or reset activity.

## Seeded data reference

| Customer | Login | Accounts |
|---|---|---|
| Alice Ndlovu | `alice@example.com` / `alice123` | `1000123456` Everyday R15,240.50; `1000123457` Savings R42,000.00 |
| Bob van der Merwe | `bob@example.com` / `bob123` | `1000234567` Everyday R3,870.25 |
| Carol Pillay | `carol@example.com` / `carol123` | `1000345678` Everyday R980.00 |
| RemitX (Pty) Ltd | `remitx@example.com` / `remitx123` | `1000987654` Settlement R250,000.00 |
| TrustMeBank Treasury (system) | cannot log in | `1000000000`, source of all seeded money |
| TrustMeBank Clearing (system) | cannot log in | `1000000001`, destination of seeded card purchases |

Demo application: `app_remitx_demo` / `tmbsk_remitx_demo_secret`, webhook secret
`whsec_remitx_demo_secret`, redirect URIs on `localhost:5000` and `localhost:3000`, plus the
Swagger UI redirect. It is intended for the example apps and for trying the API from `/docs`.
