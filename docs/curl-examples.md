# curl examples

Set these once. Use your own credentials when you have them.

```bash
export MOCKBANK=http://localhost:8000
export CLIENT_ID=app_remitx_demo
export CLIENT_SECRET=mbsk_remitx_demo_secret
export REDIRECT_URI=http://localhost:5000/callback
```

## 1. Get an authorization code

`/oauth/authorize` is a browser page, so open this URL in a browser, log in as
`alice@example.com` / `alice123`, and approve:

```bash
echo "$MOCKBANK/oauth/authorize?response_type=code&client_id=$CLIENT_ID&redirect_uri=$REDIRECT_URI&scope=accounts%20balances%20transactions%20payments&state=demo123"
```

The browser ends up at `http://localhost:5000/callback?code=mbac_...&state=demo123` (a
connection error there is fine if nothing is listening). Copy the code:

```bash
export CODE=mbac_...
```

## 2. Exchange it

```bash
curl -s -X POST $MOCKBANK/oauth/token \
  -d grant_type=authorization_code -d code=$CODE -d redirect_uri=$REDIRECT_URI \
  -d client_id=$CLIENT_ID -d client_secret=$CLIENT_SECRET | tee token.json
export TOKEN=$(python3 -c "import json; print(json.load(open('token.json'))['access_token'])")
export REFRESH=$(python3 -c "import json; print(json.load(open('token.json'))['refresh_token'])")
```

## 3. Who is connected

```bash
curl -s $MOCKBANK/api/v1/me -H "Authorization: Bearer $TOKEN"
```

## 4. Accounts, balance, transactions

```bash
curl -s $MOCKBANK/api/v1/accounts -H "Authorization: Bearer $TOKEN"
export ACCOUNT=acc_...   # the Everyday Account id from the response

curl -s $MOCKBANK/api/v1/accounts/$ACCOUNT/balance -H "Authorization: Bearer $TOKEN"
curl -s "$MOCKBANK/api/v1/accounts/$ACCOUNT/transactions?limit=5" -H "Authorization: Bearer $TOKEN"
curl -s "$MOCKBANK/api/v1/accounts/$ACCOUNT/transactions?from_date=2026-09-01&to_date=2026-09-30" \
  -H "Authorization: Bearer $TOKEN"
```

## 5. Initiate a payment

```bash
curl -s -X POST $MOCKBANK/api/v1/payments \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d "{\"debtor_account_id\": \"$ACCOUNT\", \"creditor_account_number\": \"1000987654\",
       \"amount\": \"100.00\", \"currency\": \"ZAR\", \"reference\": \"DEP-0001\",
       \"redirect_uri\": \"http://localhost:5000/payments/return\"}" | tee payment.json
export PAYMENT=$(python3 -c "import json; print(json.load(open('payment.json'))['payment_id'])")
```

Open the `authorisation_url` from the response in the browser and approve. Then:

```bash
curl -s $MOCKBANK/api/v1/payments/$PAYMENT -H "Authorization: Bearer $TOKEN"
curl -s "$MOCKBANK/api/v1/payments?status=COMPLETED" -H "Authorization: Bearer $TOKEN"
```

## 6. Refresh the token

```bash
curl -s -X POST $MOCKBANK/oauth/token \
  -d grant_type=refresh_token -d refresh_token=$REFRESH \
  -d client_id=$CLIENT_ID -d client_secret=$CLIENT_SECRET
```

## 7. Errors to try

```bash
# no token
curl -s $MOCKBANK/api/v1/accounts
# wrong scope: consent with scope=accounts only, then
curl -s $MOCKBANK/api/v1/accounts/$ACCOUNT/balance -H "Authorization: Bearer $TOKEN"
# float amount
curl -s -X POST $MOCKBANK/api/v1/payments -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"debtor_account_id\": \"$ACCOUNT\", \"creditor_account_number\": \"1000987654\", \"amount\": 100.0, \"currency\": \"ZAR\", \"reference\": \"X\"}"
# unknown creditor
curl -s -X POST $MOCKBANK/api/v1/payments -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"debtor_account_id\": \"$ACCOUNT\", \"creditor_account_number\": \"9999999999\", \"amount\": \"1.00\", \"currency\": \"ZAR\", \"reference\": \"X\"}"
```

## 8. Receive a webhook locally

A one-line receiver that prints headers and body:

```bash
python3 -c "
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        print(dict(self.headers)); print(body.decode()); self.send_response(200); self.end_headers()
HTTPServer(('0.0.0.0', 5000), H).serve_forever()"
```

With the demo application's webhook URL (`http://host.docker.internal:5000/webhooks/mockbank`)
and MockBank in Docker on the same machine, approving a payment prints the event here.
