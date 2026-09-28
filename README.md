# TrustMeBank (TMB)

TrustMeBank, TMB for short, is a small, realistic simulated bank for a university Financial
Technology course. Student teams build fintech applications that integrate with TrustMeBank
through an Open Banking style API:

```
Student App → OAuth consent → Account API → Payment initiation → Customer approves on TrustMeBank
           → Double-entry ledger → HMAC-signed webhook → Student App reconciles
```

Nothing here is real. Customers, accounts and money are all simulated.

## Run it

```bash
cp .env.example .env        # set SECRET_KEY and ADMIN_PASSWORD
docker compose up
```

Then open:

| URL | What |
|---|---|
| http://localhost:8000 | Customer banking UI. Log in as `alice@example.com` / `alice123` |
| http://localhost:8000/admin | Instructor UI. Password from `ADMIN_PASSWORD` (default `admin`) |
| http://localhost:8000/docs | Swagger UI. Click **Authorize** to run the OAuth flow in the browser |
| http://localhost:8000/guide | Student documentation, rendered from `docs/` |

The first start applies migrations and seeds customers, accounts, sixty days of transaction
history, a business settlement account and a demo application.

## For students

Start at [docs/getting-started.md](docs/getting-started.md) (or `/guide/getting-started` on a
running instance). The example apps in [examples/python-app](examples/python-app) and
[examples/node-app](examples/node-app) complete an OAuth connection, initiate a payment and
verify a webhook in under 250 lines each.

| Guide | Contents |
|---|---|
| [Getting started](docs/getting-started.md) | The whole integration in 15 minutes |
| [OAuth and consent](docs/oauth.md) | Authorization Code flow, scopes, `state`, refresh, revocation |
| [Payments](docs/payments.md) | Payment initiation, states, idempotency, settlement accounts, reconciliation |
| [Webhooks](docs/webhooks.md) | Events, HMAC signature verification in Python and Node, retries |
| [API reference](docs/api-reference.md) | Every endpoint and field |
| [curl examples](docs/curl-examples.md) | Copy and paste |
| [Administrator guide](docs/admin-guide.md) | Deploying, onboarding teams, resetting |

Seeded credentials for development:

| Who | Login | Notes |
|---|---|---|
| Alice Ndlovu | `alice@example.com` / `alice123` | Everyday `1000123456`, Savings `1000123457` |
| Bob van der Merwe | `bob@example.com` / `bob123` | Everyday `1000234567` |
| Carol Pillay | `carol@example.com` / `carol123` | Everyday `1000345678` |
| RemitX (Pty) Ltd | `remitx@example.com` / `remitx123` | Settlement account `1000987654` |
| Demo application | `app_remitx_demo` / `tmbsk_remitx_demo_secret` | Webhook secret `whsec_remitx_demo_secret` |

## For the instructor

[docs/admin-guide.md](docs/admin-guide.md) covers deployment behind HTTPS, onboarding a team
in one form (application plus settlement customer and account), day-to-day tasks, and the two
reset modes. Command line equivalents:

```bash
docker compose exec app tmb --help
docker compose exec app tmb seed            # idempotent
docker compose exec app tmb reset-activity  # wipe activity, keep customers and apps
docker compose exec app tmb reset --yes     # wipe everything and re-seed
docker compose exec app tmb check-ledger    # balances == sum of transactions
docker compose exec app tmb create-app --name RemitX --redirect-uri http://localhost:5000/callback
```

## Design documents

- [SPEC.md](SPEC.md): entities, OAuth and consent lifecycle, endpoints, scopes, payment state
  machine, ledger rules, webhook behaviour, admin functions, error format, security assumptions
- [ARCHITECTURE.md](ARCHITECTURE.md): stack, code layout, request handling, ledger and money,
  the outbox worker, deployment, testing, decisions and alternatives
- [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md): milestones and the pre-implementation
  design review that removed unnecessary complexity

## Development

Requires Python 3.11+ and a PostgreSQL 16 server.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e .
createdb trustmebank && createdb trustmebank_test     # or use docker compose up db
export DATABASE_URL=postgresql+psycopg://trustmebank:trustmebank@localhost:5432/trustmebank
alembic upgrade head && tmb seed
uvicorn trustmebank.main:app --reload
```

Tests run against a real PostgreSQL because the ledger relies on row locks:

```bash
export TEST_DATABASE_URL=postgresql+psycopg://trustmebank:trustmebank@localhost:5432/trustmebank_test
pytest
ruff check .
```

`tests/test_lifecycle.py` is the contract: register app, authorize, exchange code, read
accounts, initiate payment, approve, verify the ledger, deliver and verify the signed webhook.

Layout:

```
trustmebank/
  services/   ledger, oauth, payments, webhooks, seed, admin  (all business rules)
  api/        JSON routers (/oauth/token, /api/v1/...)
  web/        HTML routers (login, accounts, consent, payment authorisation, admin, guides)
  templates/  Jinja2
  worker.py   background thread: delayed settlement and webhook delivery
  cli.py      trustmebank command
alembic/      migrations
docs/         student guides, rendered at /guide
examples/     python-app (Flask), node-app (Express)
tests/
```

## Security notes

Passwords are bcrypt hashed; client secrets, authorization codes and tokens are stored as
SHA-256 hashes. Codes are single use and expire in 5 minutes; access tokens in 1 hour; refresh
tokens rotate. Redirect URIs must match exactly. Scopes are enforced on every endpoint.
Payments can only be executed through the customer's own session on TrustMeBank's approval page,
protected by a CSRF token. Webhooks are HMAC-SHA256 signed. Money is `Decimal` and
`NUMERIC(18,2)` throughout; there is no floating point in the money path.

This is teaching software. Change the default secrets, serve it over HTTPS, and do not expose
the admin interface with the default password.

## Licence

MIT. See [LICENSE](LICENSE).
