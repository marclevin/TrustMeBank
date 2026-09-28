# Webhooks

MockBank POSTs a JSON event to your webhook URL when something happens that your application
cares about. Events are signed with your `webhook_secret` so you can prove they came from
MockBank and were not tampered with.

## Events

| Event | When | Notes |
|---|---|---|
| `payment.completed` | A payment you created was approved and settled | Credit your customer now (once) |
| `payment.failed` | Approved but settlement failed | `data.failure_reason` says why (`insufficient_funds`, `account_closed`) |
| `payment.rejected` | The customer clicked Reject | |
| `transaction.created` | A transaction was posted to an account of a customer who consented to your app with the `transactions` scope | Off by default. Ask your instructor to enable it for your application |

## The request

```
POST https://your-app.example/webhooks/mockbank
Content-Type: application/json
User-Agent: MockBank-Webhooks/1.0
X-MockBank-Event: payment.completed
X-MockBank-Delivery-Id: evt_5h4g3f2d1s
X-MockBank-Signature: t=1727517601,v1=054e4eb8d78fc0d053363ec4cd6b016ebd24a4ae6a1984a18c41bf9544ddc6fd

{"id":"evt_5h4g3f2d1s","event":"payment.completed","created_at":"2026-09-28T10:00:01Z","data":{"payment_id":"pay_abc","amount":"500.00","currency":"ZAR","reference":"REM-92831"}}
```

The signature above is real: it is what MockBank produces for exactly that body with the demo
secret `whsec_remitx_demo_secret` and timestamp `1727517601`. Use it to test your verifier
(disable the timestamp check while testing, since that timestamp is in the past).

For payment events, `data` contains the payment object as returned by
`GET /api/v1/payments/{id}` (without `authorisation_url`). For `transaction.created`, `data` is
a transaction object.

## Verifying the signature

The header has two parts: `t`, a Unix timestamp, and `v1`, a lowercase hex HMAC-SHA256.

```
signed_payload = "<t>" + "." + <raw request body bytes>
expected       = HMAC_SHA256(key = webhook_secret, message = signed_payload)   as hex
valid          = constant_time_compare(expected, v1)  and  |now - t| <= 300 seconds
```

Rules that trip people up:

1. Hash the **raw bytes** of the request body, exactly as received. Do not parse the JSON and
   re-serialise it; key order or spacing may differ and the signature will not match.
2. Use a constant-time comparison, not `==`.
3. Check the timestamp to limit replay attacks. Five minutes tolerance is plenty.

### Python (Flask)

```python
import hashlib, hmac, time
from flask import Flask, request, abort

WEBHOOK_SECRET = "whsec_remitx_demo_secret"
app = Flask(__name__)

def verify(body: bytes, header: str, tolerance: int = 300) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t, v1 = int(parts["t"]), parts["v1"]
    except (ValueError, KeyError):
        return False
    if abs(time.time() - t) > tolerance:
        return False
    expected = hmac.new(WEBHOOK_SECRET.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)

@app.post("/webhooks/mockbank")
def webhook():
    if not verify(request.get_data(), request.headers.get("X-MockBank-Signature", "")):
        abort(400)
    event = request.get_json()
    if already_processed(event["id"]):          # idempotency: deliveries can repeat
        return "", 200
    if event["event"] == "payment.completed":
        credit_customer(payment_id=event["data"]["payment_id"], amount=event["data"]["amount"])
    mark_processed(event["id"])
    return "", 200
```

### Node (Express)

```js
const crypto = require("crypto");
const express = require("express");
const WEBHOOK_SECRET = "whsec_remitx_demo_secret";
const app = express();

function verify(rawBody, header, tolerance = 300) {
  const parts = Object.fromEntries(header.split(",").map((p) => p.split("=", 2)));
  const t = parseInt(parts.t, 10);
  if (!parts.v1 || Number.isNaN(t) || Math.abs(Date.now() / 1000 - t) > tolerance) return false;
  const expected = crypto.createHmac("sha256", WEBHOOK_SECRET)
    .update(`${t}.`).update(rawBody).digest("hex");
  const a = Buffer.from(expected, "hex"), b = Buffer.from(parts.v1, "hex");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// express.raw keeps the exact bytes; express.json would re-serialise them.
app.post("/webhooks/mockbank", express.raw({ type: "application/json" }), (req, res) => {
  if (!verify(req.body, req.get("X-MockBank-Signature") || "")) return res.sendStatus(400);
  const event = JSON.parse(req.body.toString("utf8"));
  // de-duplicate on event.id, then act on event.event
  res.sendStatus(200);
});
```

## Responding

Return any 2xx status within 10 seconds. The body is ignored. Do slow work (blockchain calls,
emails) after responding, or in a background job.

Anything other than 2xx, or a timeout, counts as a failure.

## Retries

Failed deliveries are retried with backoff:

| Attempt | Delay after previous failure |
|---|---|
| 2 | 30 seconds |
| 3 | 2 minutes |
| 4 | 10 minutes |
| 5 | 30 minutes |
| 6 | 1 hour |

After six failures the delivery is marked `failed` and only the administrator can retry it.
Because deliveries can repeat (a timeout on your side after you processed the event, for
example), always de-duplicate on the event `id`.

The administrator can see every delivery, its attempts, the last response code and body, and
the exact payload, under **Admin, Webhook deliveries**. If your webhook is not arriving, that
page tells you why.

## Ordering

Events are delivered in creation order per application when everything succeeds, but a retry
can arrive after a later event. Do not depend on order. Payment events are final states, so
this rarely matters in practice.

## Local development

MockBank must be able to reach your URL.

- MockBank in Docker on your laptop, your app on the same laptop: register
  `http://host.docker.internal:5000/webhooks/mockbank` (any port you use). The compose file
  maps that hostname to your machine.
- MockBank on a shared server, your app on your laptop: expose your app with a tunnel such as
  `ngrok http 5000` or `cloudflared tunnel --url http://localhost:5000`, and ask the instructor
  to set the tunnel URL as your webhook URL. Tunnel URLs change, so consider a paid static
  domain or deploy your app somewhere public.
- If you cannot receive webhooks at all, poll `GET /api/v1/payments/{id}`. You lose nothing
  functionally, only latency.
