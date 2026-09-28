"""RemitX: a minimal fintech app integrating with TrustMeBank.

Read top to bottom. Every TrustMeBank interaction is in this one file:
  /connect            -> redirect to TrustMeBank's consent screen (OAuth authorization request)
  /callback           -> exchange the code for tokens, link the bank customer to the RemitX user
  /                   -> show bank accounts (Account Information API) and the RemitX wallet
  /deposit            -> initiate a payment to RemitX's settlement account (Payment Initiation API)
  /payments/return    -> the customer is back from TrustMeBank; show status, do NOT credit yet
  /webhooks/trustmebank  -> verify the HMAC signature, then credit the wallet exactly once
"""

import hashlib
import hmac
import os
import secrets
import time
import uuid
from decimal import Decimal
from urllib.parse import urlparse

import requests
from flask import Flask, abort, redirect, render_template_string, request, session, url_for

TRUSTMEBANK_URL = os.environ.get("TRUSTMEBANK_URL", "http://localhost:8000").rstrip("/")
CLIENT_ID = os.environ.get("CLIENT_ID", "app_remitx_demo")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "tmbsk_remitx_demo_secret")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "whsec_remitx_demo_secret")
SETTLEMENT_ACCOUNT_NUMBER = os.environ.get("SETTLEMENT_ACCOUNT_NUMBER", "1000987654")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000").rstrip("/")
SCOPES = "accounts balances transactions payments"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-only")

# RemitX's own state. In a real app this is a database. Note the separation:
#   bank_links  : the customer's TrustMeBank tokens (bank money lives at TrustMeBank)
#   wallets     : RemitX's internal ledger (platform balance lives here)
#   deposits    : our record of each payment we initiated, keyed by TrustMeBank payment_id
#   seen_events : webhook event ids we already processed (deliveries can repeat)
bank_links: dict[str, dict] = {}
wallets: dict[str, Decimal] = {}
deposits: dict[str, dict] = {}
seen_events: set[str] = set()


def current_user() -> str:
    """One RemitX user per browser session; good enough for a demo."""
    if "user_id" not in session:
        session["user_id"] = "user_" + secrets.token_hex(4)
    return session["user_id"]


def bank_get(user_id: str, path: str, **params):
    link = bank_links.get(user_id)
    if not link:
        return None
    r = requests.get(
        f"{TRUSTMEBANK_URL}{path}",
        params=params,
        timeout=10,
        headers={"Authorization": f"Bearer {link['access_token']}"},
    )
    if r.status_code == 401:
        # Token expired or the customer revoked us. Try one refresh, else force a reconnect.
        if refresh_tokens(user_id):
            return bank_get(user_id, path, **params)
        bank_links.pop(user_id, None)
        return None
    r.raise_for_status()
    return r.json()


def refresh_tokens(user_id: str) -> bool:
    link = bank_links[user_id]
    r = requests.post(
        f"{TRUSTMEBANK_URL}/oauth/token",
        timeout=10,
        data={
            "grant_type": "refresh_token",
            "refresh_token": link["refresh_token"],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    if r.status_code != 200:
        return False
    link.update(r.json())
    return True


# ----------------------------------------------------------------------------- OAuth


@app.get("/connect")
def connect():
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state  # remembered for the callback
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": f"{BASE_URL}/callback",
        "scope": SCOPES,
        "state": state,
    }
    return redirect(f"{TRUSTMEBANK_URL}/oauth/authorize?" + requests.compat.urlencode(params))


@app.get("/callback")
def callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        abort(400, "state mismatch: this callback did not come from a flow we started")
    if "error" in request.args:
        return f"TrustMeBank said: {request.args['error']}. <a href='/'>Back</a>", 400
    r = requests.post(
        f"{TRUSTMEBANK_URL}/oauth/token",
        timeout=10,
        data={
            "grant_type": "authorization_code",
            "code": request.args["code"],
            "redirect_uri": f"{BASE_URL}/callback",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    if r.status_code != 200:
        return f"Token exchange failed: {r.text}", 400
    user_id = current_user()
    bank_links[user_id] = r.json()
    me = bank_get(user_id, "/api/v1/me")
    bank_links[user_id]["customer"] = me
    print(f"[remitx] linked {user_id} to TrustMeBank customer {me['customer_id']} ({me['full_name']})")
    return redirect(url_for("home"))


# ----------------------------------------------------------------------------- pages

PAGE = """
<!doctype html><title>RemitX</title>
<style>body{font-family:sans-serif;max-width:720px;margin:40px auto;line-height:1.5}
.card{border:1px solid #ddd;border-radius:8px;padding:16px;margin:12px 0}
.btn{display:inline-block;padding:8px 14px;background:#2b4c7e;color:#fff;border-radius:6px;text-decoration:none;border:0;cursor:pointer}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #eee;padding:6px;text-align:left}</style>
<h1>RemitX <small style="color:#888">example fintech app</small></h1>
{% if msg %}<p style="background:#fff3cd;padding:10px">{{ msg }}</p>{% endif %}
<div class="card"><h3>Your RemitX wallet (RemitX's internal ledger)</h3>
<p style="font-size:1.6em">R{{ wallet }}</p>
<p>This number lives in RemitX, not at the bank. It only changes when a verified webhook says a deposit completed.</p></div>
{% if not link %}
<div class="card"><h3>Bank account</h3><p>Not connected.</p><a class="btn" href="/connect">Connect your bank (TrustMeBank)</a></div>
{% else %}
<div class="card"><h3>Bank accounts at TrustMeBank (bank money)</h3>
<p>Connected as {{ link.customer.full_name }}, scopes: {{ link.scope }}</p>
<table><tr><th>Account</th><th>Number</th><th>Balance</th><th></th></tr>
{% for a in accounts %}<tr><td>{{ a.name }}</td><td>{{ a.account_number }}</td><td>R{{ a.balance }}</td>
<td><form method="post" action="/deposit"><input type="hidden" name="account_id" value="{{ a.id }}"><button class="btn">Deposit R100 into RemitX</button></form></td></tr>{% endfor %}
</table></div>
{% endif %}
<div class="card"><h3>Deposits (RemitX's records, keyed by TrustMeBank payment_id)</h3>
<table><tr><th>payment_id</th><th>reference</th><th>amount</th><th>status</th><th>credited?</th></tr>
{% for d in deposits %}<tr><td>{{ d.payment_id }}</td><td>{{ d.reference }}</td><td>R{{ d.amount }}</td><td>{{ d.status }}</td><td>{{ 'yes' if d.credited else 'no' }}</td></tr>{% endfor %}
</table></div>
"""


@app.get("/")
def home():
    user_id = current_user()
    link = bank_links.get(user_id)
    accounts = bank_get(user_id, "/api/v1/accounts")["data"] if link else []
    mine = [d for d in deposits.values() if d["user_id"] == user_id]
    return render_template_string(
        PAGE,
        link=bank_links.get(user_id),
        accounts=accounts,
        wallet=wallets.get(user_id, Decimal("0.00")),
        deposits=mine,
        msg=request.args.get("msg"),
    )


# ----------------------------------------------------------------------------- payments


@app.post("/deposit")
def deposit():
    user_id = current_user()
    link = bank_links.get(user_id)
    if not link:
        return redirect(url_for("connect"))
    reference = f"DEP-{uuid.uuid4().hex[:8].upper()}"  # unique, so reconciliation is unambiguous
    r = requests.post(
        f"{TRUSTMEBANK_URL}/api/v1/payments",
        timeout=10,
        headers={"Authorization": f"Bearer {link['access_token']}", "Idempotency-Key": str(uuid.uuid4())},
        json={
            "debtor_account_id": request.form["account_id"],
            "creditor_account_number": SETTLEMENT_ACCOUNT_NUMBER,
            "amount": "100.00",
            "currency": "ZAR",
            "reference": reference,
            "redirect_uri": f"{BASE_URL}/payments/return",
        },
    )
    if r.status_code != 201:
        return redirect(url_for("home", msg=f"Payment initiation failed: {r.text}"))
    payment = r.json()
    deposits[payment["payment_id"]] = {
        "user_id": user_id,
        "payment_id": payment["payment_id"],
        "reference": reference,
        "amount": payment["amount"],
        "status": payment["status"],
        "credited": False,
    }
    print(f"[remitx] created {payment['payment_id']} ({reference}); sending customer to authorise")
    return redirect(payment["authorisation_url"])  # the customer approves at the bank


@app.get("/payments/return")
def payment_return():
    """The customer is back. The query string is only a hint; confirm with the API."""
    payment_id = request.args.get("payment_id", "")
    user_id = current_user()
    record = deposits.get(payment_id)
    if record and record["user_id"] == user_id:
        fresh = bank_get(user_id, f"/api/v1/payments/{payment_id}")
        if fresh:
            record["status"] = fresh["status"]
    status = record["status"] if record else "unknown"
    note = {
        "COMPLETED": "The bank says it is completed. Your wallet is credited when the webhook arrives.",
        "PROCESSING": "The bank is still processing it. We will credit your wallet when the webhook arrives.",
        "REJECTED": "You rejected the payment.",
        "FAILED": "The bank could not complete the payment.",
    }
    return redirect(url_for("home", msg=f"Payment {payment_id}: {status}. {note.get(status, '')}"))


# ----------------------------------------------------------------------------- webhooks


def verify_signature(body: bytes, header: str, tolerance: int = 300) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t, v1 = int(parts["t"]), parts["v1"]
    except (ValueError, KeyError):
        return False
    if abs(time.time() - t) > tolerance:
        return False
    expected = hmac.new(WEBHOOK_SECRET.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


@app.post("/webhooks/trustmebank")
def webhook():
    body = request.get_data()  # raw bytes, never re-serialised
    if not verify_signature(body, request.headers.get("X-TrustMeBank-Signature", "")):
        print("[remitx] webhook with BAD signature rejected")
        abort(400)
    event = request.get_json(force=True)
    if event["id"] in seen_events:  # at-least-once delivery: de-duplicate
        return "", 200
    seen_events.add(event["id"])
    data = event["data"]
    print(f"[remitx] webhook {event['event']} {event['id']} for {data.get('payment_id')}")
    record = deposits.get(data.get("payment_id"))
    if record:
        record["status"] = data["status"]
        if event["event"] == "payment.completed" and not record["credited"]:
            wallets[record["user_id"]] = wallets.get(record["user_id"], Decimal("0.00")) + Decimal(
                data["amount"]
            )
            record["credited"] = True
            print(
                f"[remitx] credited R{data['amount']} to {record['user_id']} (journal {data.get('journal_id')})"
            )
    return "", 200


if __name__ == "__main__":
    print(f"RemitX example on {BASE_URL}, talking to TrustMeBank at {TRUSTMEBANK_URL}")
    app.run(host="0.0.0.0", port=urlparse(BASE_URL).port or 5000, debug=True)
