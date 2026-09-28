# TrustMeBank Architecture

This document explains how TrustMeBank is built and why. Read `SPEC.md` first for what it does.

## 1. The lifecycle we are teaching

```
Student App ──(1) redirect──▶ TrustMeBank /oauth/authorize ──(2) consent──▶ redirect back with code
Student App ──(3) POST /oauth/token──▶ access token
Student App ──(4) GET /api/v1/accounts──▶ accounts
Student App ──(5) POST /api/v1/payments──▶ AWAITING_AUTHORISATION + authorisation_url
Student App ──(6) redirect customer──▶ TrustMeBank /payments/{id}/authorise ──(7) Approve──▶ ledger
TrustMeBank ──(8) POST webhook payment.completed (HMAC signed)──▶ Student App
Student App ──(9) verify signature, GET /api/v1/payments/{id}, credit internal balance
```

Every architectural decision below exists to make those nine steps reliable, observable and
understandable. Anything that does not serve them was left out.

## 2. Stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Course preference, readable for students |
| Web framework | FastAPI | JSON API and OpenAPI docs for free, Jinja2 for HTML pages |
| Database | PostgreSQL 16 | Row locks (`SELECT ... FOR UPDATE`), `NUMERIC`, `SKIP LOCKED` for the worker |
| ORM and migrations | SQLAlchemy 2.0 (sync) and Alembic | Boring and maintainable. Sync sessions keep transactions easy to reason about |
| Templates | Jinja2 with one hand-written stylesheet | No frontend build step, nothing to maintain |
| Passwords | bcrypt | Standard |
| HTTP client | httpx | Used for webhook delivery and tests |
| Background work | One thread inside the app process | No Redis, no Celery. See section 6 |
| Packaging | `pyproject.toml` with a pinned `requirements.txt`; the Docker image runs the source tree directly | Reproducible builds, no package build step |
| Deployment | Docker Compose: `db` and `app` | `docker compose up` is the whole story |

FastAPI endpoints are plain `def` functions. FastAPI runs them in a thread pool, so the
synchronous SQLAlchemy session is fine and there is no async complexity anywhere in the
codebase. A class of 100 students generates a few requests per second at most.

## 3. Code layout

```
trustmebank/
  main.py            app factory, middleware, router registration, lifespan (starts the worker)
  config.py          Settings loaded from environment variables
  db.py              engine, SessionLocal, Base
  models.py          all SQLAlchemy models (one file, the schema is small)
  ids.py             prefixed random id generation
  money.py           Decimal parsing and formatting
  security.py        password hashing, secret generation and hashing, HMAC signing
  errors.py          APIError and the JSON error handlers
  ratelimit.py       small in-memory sliding window limiter
  audit.py           audit log helper
  schemas.py         Pydantic models for the JSON API (request and response shapes)
  services/
    ledger.py        post_transfer, balance checks, integrity check
    oauth.py         authorize validation, codes, tokens, consents
    payments.py      create, approve, reject, settle
    webhooks.py      enqueue, sign, deliver, retry schedule
    seed.py          seed and reset
    admin.py         admin operations that are more than one query
  api/               JSON routers: deps (auth), oauth token, me, accounts, payments
  web/               HTML routers: auth, accounts, transfer, consent, payment authorisation, admin, guide
  templates/         Jinja2 templates
  static/            style.css
  worker.py          background loop: deliver due webhooks, settle delayed payments
  cli.py             seed, reset, create-app, check-ledger, run-worker
alembic/             migrations
tests/               pytest suite (runs against a real PostgreSQL)
docs/                student facing guides rendered at /guide
examples/            python-app (Flask) and node-app (Express) reference integrations
```

Services contain all business rules and are called by both the API and the web routers.
Routers only parse input, call a service and render output. Tests exercise services directly
and the full HTTP flow through FastAPI's `TestClient`.

## 4. Request handling

```
Browser ──cookie session──▶ web/* routers ──▶ services ──▶ PostgreSQL
TPP     ──Bearer token────▶ api/* routers ──▶ services ──▶ PostgreSQL
```

- **Customer sessions**: Starlette `SessionMiddleware` (signed cookie, `SECRET_KEY`). The session
  holds `customer_id`, an `admin` flag and a CSRF token. Every state-changing form posts the CSRF
  token.
- **API authentication**: `api/deps.py` hashes the bearer token, loads `Token -> Consent ->
  Application, Customer`, checks expiry, revocation and active flags, and returns an
  `AuthContext`. `require_scope("balances")` is a dependency factory used per endpoint.
- **Errors**: services raise `APIError(status, code, message)`. A single exception handler turns
  it into the JSON error envelope for `/api` and `/oauth/token`, and an HTML error page for
  browser routes. Pydantic validation errors are reshaped into the same envelope.
- **Rate limiting**: a middleware keyed on bearer token, client_id or IP with an in-memory
  sliding window. Single process, so no shared store is needed.

## 5. Ledger and money

- `Decimal` everywhere. `money.parse_amount()` is the only place a string becomes a Decimal
  and it enforces two decimal places and bounds. `money.fmt()` is the only place a Decimal
  becomes a string.
- `ledger.post_transfer()` is the only function that writes journals or transactions and the
  only function that changes `account.balance`. Payments, UI transfers, seeds and admin top-ups
  all go through it.
- Locking discipline: lock accounts in ascending id order with `FOR UPDATE`. This makes
  concurrent transfers serialise per account pair and prevents deadlocks.
- Integrity: `ledger.check_integrity()` compares every cached balance with the sum of its
  transactions and every journal with zero. It runs in tests and from the admin dashboard.

## 6. Background work: the outbox worker

Webhooks must be reliable and retryable, and delayed settlement must not block a request.
Both are handled by one thread started in the FastAPI lifespan (`worker.py`):

```
loop every 2 seconds:
    settle payments where status = PROCESSING and process_after <= now   (FOR UPDATE SKIP LOCKED)
    deliver webhook_deliveries where status = pending and next_attempt_at <= now (FOR UPDATE SKIP LOCKED)
```

- Deliveries are inserted in the same transaction as the ledger change that caused them. The
  database is the queue. If the process dies mid-delivery the row is still `pending` and is
  retried.
- `SKIP LOCKED` means a second app process (or the `tmb run-worker` CLI) can safely run
  the same loop. We do not need that for a class, but it costs nothing.
- Tests disable the thread and call `worker.run_once()` with an `httpx.Client` whose transport
  is a mock, so the whole lifecycle including signature verification runs in one test.

## 7. Web UI

Server rendered Jinja2 with a single stylesheet. The pages are intentionally plain: a bank a
student can read in an afternoon. Layout: `base.html` with a nav for customers and a separate
`admin/base.html`. Forms use POST with a hidden CSRF token and redirect after success.

Markdown files in `docs/` are rendered at `/guide/{name}` so the deployed instance carries
its own documentation. `/docs` is the Swagger UI generated from the API routers only; HTML
routes are excluded from the schema.

## 8. Deployment

```
docker compose up
  db  : postgres:16-alpine with a named volume
  app : builds ./Dockerfile, waits for db, runs alembic upgrade head, seeds if empty, starts uvicorn
```

- One uvicorn process (`--workers 1`). The in-process worker and rate limiter assume this.
  If more throughput were ever needed, run a second `app` replica with
  `WEBHOOK_WORKER_ENABLED=false` and start `tmb run-worker` once.
- `extra_hosts: host.docker.internal:host-gateway` so a webhook URL of
  `http://host.docker.internal:5000/...` reaches a student app on the instructor's machine.
- Put a TLS terminating reverse proxy (Caddy, nginx, a cloud load balancer) in front and set
  `PUBLIC_BASE_URL` and `SESSION_COOKIE_SECURE=true`.
- Backups: `docker compose exec db pg_dump -U trustmebank trustmebank > backup.sql`.

## 9. Testing strategy

- Tests run against a real PostgreSQL (`TEST_DATABASE_URL`). SQLite would silently ignore
  row locks and change the meaning of the concurrency tests.
- `tests/test_lifecycle.py` is the contract: register app, authorize, exchange, list accounts,
  create payment, approve, assert ledger, run worker, assert signed webhook.
- Unit tests cover money parsing, ledger invariants, OAuth edge cases (code reuse, wrong
  redirect URI, wrong secret, expired token, scope enforcement), payment idempotency, webhook
  retry scheduling and signature verification.
- One test runs `alembic upgrade head` on an empty database and compares it with the models.

## 10. Decisions and alternatives considered

| Decision | Alternative rejected | Reason |
|---|---|---|
| Opaque random tokens stored hashed | JWTs | Revocation must be immediate when a customer revokes consent. A DB lookup per request is trivial at this scale |
| Cursor pagination on an integer `seq` | Offset pagination | Stable under inserts, and the cursor is still trivial to implement |
| One thread as worker | Celery, RQ, a separate container | Nothing to deploy or monitor. The outbox table is the queue |
| Admin registers applications | Developer self-service portal | The instructor wants control over who is registered. An "onboard team" form keeps it to one click per team |
| Payments only to TrustMeBank account numbers | External beneficiaries | Keeps the ledger closed and balanced, and makes the settlement account scenario possible |
| `PROCESSING` state with optional delay | Synchronous only | Lets the instructor make students handle asynchronous settlement without a different code path |
| Consent replaced on re-authorisation | Merged scopes | Simple mental model: the latest consent screen is the truth |
| Session cookies for the bank UI | Separate auth for admin | One mechanism, one middleware. The admin flag lives in the same session |
