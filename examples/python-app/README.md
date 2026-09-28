# Example: RemitX (Python / Flask)

A minimal fintech app that integrates with TrustMeBank. It connects a TrustMeBank customer with
OAuth, shows their accounts, lets them "deposit" R100 into their RemitX wallet by initiating a
payment to RemitX's settlement account, and credits the wallet when the signed
`payment.completed` webhook arrives.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
TRUSTMEBANK_URL=http://localhost:8000 python app.py
```

Open http://localhost:5000. Log in to TrustMeBank as `alice@example.com` / `alice123` when asked.

Environment variables (defaults match the seeded demo application):

| Variable | Default |
|---|---|
| `TRUSTMEBANK_URL` | `http://localhost:8000` |
| `CLIENT_ID` | `app_remitx_demo` |
| `CLIENT_SECRET` | `tmbsk_remitx_demo_secret` |
| `WEBHOOK_SECRET` | `whsec_remitx_demo_secret` |
| `SETTLEMENT_ACCOUNT_NUMBER` | `1000987654` |
| `BASE_URL` | `http://localhost:5000` (must match the registered redirect URIs) |

If TrustMeBank runs in Docker on this machine, its webhook URL for the demo application is
already `http://host.docker.internal:5000/webhooks/trustmebank`, which reaches this app.

Everything is kept in memory; restart the app and it forgets. That is deliberate: the point is
to read the code, not to reuse it.
