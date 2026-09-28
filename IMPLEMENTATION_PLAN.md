# TrustMeBank Implementation Plan

**Status: all milestones complete.** The test suite (78 tests) covers every milestone's
acceptance criteria, and both example applications have been driven end to end against a
running instance, including webhook delivery from the background worker.

Development is divided into milestones. Each milestone leaves the repository in a working,
tested state and ends with a commit. Later milestones do not require rework of earlier ones.

## Milestone 0: Skeleton

- `pyproject.toml`, pinned `requirements.txt`, `.env.example`, `.gitignore`
- `trustmebank/config.py`, `db.py`, `main.py` with `/health`
- Alembic configured against `DATABASE_URL`
- `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`
- Test harness: `tests/conftest.py` creates a clean schema per test session on `TEST_DATABASE_URL`

Done when: `uvicorn trustmebank.main:app` serves `/health` and `pytest` runs one passing test.

## Milestone 1: Models, ledger, seed

- All models from SPEC section 3 in `models.py`; initial Alembic migration
- `money.py`, `ids.py`, `security.py`
- `services/ledger.py`: `post_transfer`, `check_integrity`, `InsufficientFunds`
- `services/seed.py`: seed customers, system accounts, history, demo application; `reset`
- `cli.py`: `seed`, `reset`, `check-ledger`

Tests: money parsing edge cases, transfer updates both balances, insufficient funds refused,
overdraft on system account allowed, integrity check passes after seed, concurrent transfers
from two threads keep the ledger consistent.

## Milestone 2: Customer web UI

- Session middleware, login, logout, CSRF helper
- Accounts list, account detail with transactions, transfer form, connected apps (empty for now)
- Base template and stylesheet, HTML error pages

Tests: login with seeded user, wrong password rejected, transfer through the UI moves money,
`next` redirect refuses external URLs.

## Milestone 3: Applications and OAuth

- `services/oauth.py`: validate authorize request, consent creation, code issue and exchange,
  refresh, token lookup
- `/oauth/authorize` GET and POST (consent screen), `/oauth/token`
- Connected apps page with revoke
- `api/deps.py` with `require_scope`
- `GET /api/v1/me`

Tests: full code flow, state echoed, wrong redirect URI shows error page and never redirects,
invalid scope redirects with error, deny redirects with `access_denied`, code single use and
reuse revokes tokens, wrong secret, expired token, revoked consent, refresh rotation.

## Milestone 4: Account information API

- Accounts, account, balance, transactions with cursor pagination and date filters
- Scope enforcement including balance field visibility
- OpenAPI metadata: title, description, tags, security schemes

Tests: each endpoint with and without scope, other customer's account is 404, pagination walks
every transaction exactly once, date filters.

## Milestone 5: Payment initiation and authorisation

- `services/payments.py`: create (validation, idempotency), approve, reject, settle
- `POST /api/v1/payments`, `GET /api/v1/payments`, `GET /api/v1/payments/{id}`
- `/payments/{id}/authorise` pages
- Delayed settlement via `process_after`

Tests: create returns `AWAITING_AUTHORISATION` and a URL, idempotency replay and conflict,
non-owner cannot see or approve, approve settles and updates both balances, insufficient funds
gives `FAILED`, reject gives `REJECTED`, settle twice is a no-op, redirect back with status.

## Milestone 6: Webhooks and worker

- `services/webhooks.py`: enqueue in the same transaction, sign, deliver, retry schedule
- `worker.py`: loop, `run_once`, lifespan start and stop
- `transaction.created` opt-in

Tests: delivery signed correctly and verifiable with the documented snippet, non-2xx schedules
retry with the expected backoff, gives up after the maximum, `run_once` settles a delayed payment.

## Milestone 7: Admin UI

- Admin login, dashboard with counts and integrity check
- Customers: create, reset password, deactivate, add account, top up, seed history
- Applications: register (with optional settlement customer and account), show credentials once,
  regenerate secret, rotate webhook secret, edit, deactivate
- Consents, payments, webhook deliveries (retry), audit log
- Reset activity and full reset

Tests: admin login required, register application through the form, reset activity keeps
applications and restores seeded balances.

## Milestone 8: Documentation, examples, end-to-end

- README, docs/ guides (getting started, OAuth, payments, webhooks, API reference, admin guide,
  curl examples), rendered at `/guide`
- `examples/python-app` (Flask) and `examples/node-app` (Express)
- `tests/test_lifecycle.py`: the full contract in one test
- Final pass: remove dead code, check every doc link, confirm `docker compose up` path by reading
  the Dockerfile and entrypoint carefully

## Design review before implementation

The specification was reviewed for unnecessary complexity before coding. Items removed or
reduced:

| Considered | Decision |
|---|---|
| Separate worker container with Redis or Celery | Removed. One in-process thread over an outbox table |
| PKCE | Removed. Students have backends and confidential clients. Noted as an extension exercise |
| JWT access tokens | Removed. Opaque hashed tokens make revocation trivial |
| Multiple balance types (available, booked, cleared) | Removed. One balance |
| `EXPIRED` payment state and code to expire stale payments | Removed. A stale unapproved payment is harmless |
| Consent object with per-account selection on the consent screen | Removed. Consent covers all of the customer's accounts. Per-account selection is realistic but adds a screen and a join for little teaching value |
| `transaction.created` for every consented application | Reduced to opt-in per application, so 25 teams connecting `alice` do not each receive every one of her transactions |
| Developer self-service portal | Removed. The admin form onboards a team (application plus settlement customer and account) in one submission |
| Storing client secrets in plaintext for convenience | Rejected. Hashed, shown once, regenerable. This is the pattern students should see |
| Offset pagination for simplicity | Rejected in favour of an integer sequence cursor. Same effort, correct under inserts |
| SQLite for tests | Rejected. It ignores `FOR UPDATE` and would make locking tests meaningless |
| Async SQLAlchemy | Rejected. No benefit at classroom scale, more ways to get transactions wrong |
| Payment expiry, payment cancellation API | Removed. Not part of the lifecycle being taught |
| A `client_credentials` grant for the settlement account | Removed. The team's business customer consents like any other customer, which reinforces the model |

Items kept despite adding some code, because they matter for the teaching goal:

- Refresh tokens. Access tokens expire in an hour and a student app that dies after an hour
  teaches the wrong lesson.
- `Idempotency-Key` on payment creation. Idempotent APIs are core fintech knowledge.
- The `PROCESSING` state with an optional delay. It is the cheapest way to force students to
  reconcile through webhooks or polling rather than trusting the browser redirect.
- The UI transfer form. It makes the "manual EFT deposit with a reference" scenario possible
  without any extra API.
