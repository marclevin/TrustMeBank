// RemitX: a minimal fintech app integrating with MockBank (Express).
// See examples/python-app/app.py for a line-by-line explanation of the same flow.

import crypto from "node:crypto";
import cookieSession from "cookie-session";
import express from "express";

const MOCKBANK_URL = (process.env.MOCKBANK_URL || "http://localhost:8000").replace(/\/$/, "");
const CLIENT_ID = process.env.CLIENT_ID || "app_remitx_demo";
const CLIENT_SECRET = process.env.CLIENT_SECRET || "mbsk_remitx_demo_secret";
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || "whsec_remitx_demo_secret";
const SETTLEMENT_ACCOUNT_NUMBER = process.env.SETTLEMENT_ACCOUNT_NUMBER || "1000987654";
const BASE_URL = (process.env.BASE_URL || "http://localhost:3000").replace(/\/$/, "");
const SCOPES = "accounts balances transactions payments";

// RemitX's own state (in memory for the demo). Bank money lives at MockBank; the wallet lives here.
const bankLinks = new Map();   // userId -> token response + customer
const wallets = new Map();     // userId -> cents (integer, never floats for money)
const deposits = new Map();    // paymentId -> our record
const seenEvents = new Set();  // webhook ids already processed

const app = express();
app.use(cookieSession({ name: "remitx", secret: process.env.SESSION_SECRET || "dev-only" }));

function userId(req) {
  if (!req.session.userId) req.session.userId = "user_" + crypto.randomBytes(4).toString("hex");
  return req.session.userId;
}

async function bankGet(uid, path) {
  const link = bankLinks.get(uid);
  if (!link) return null;
  let res = await fetch(MOCKBANK_URL + path, { headers: { Authorization: `Bearer ${link.access_token}` } });
  if (res.status === 401) {
    if (await refresh(uid)) return bankGet(uid, path);
    bankLinks.delete(uid);
    return null;
  }
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json();
}

async function refresh(uid) {
  const link = bankLinks.get(uid);
  const res = await fetch(`${MOCKBANK_URL}/oauth/token`, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: link.refresh_token,
                                client_id: CLIENT_ID, client_secret: CLIENT_SECRET }),
  });
  if (!res.ok) return false;
  Object.assign(link, await res.json());
  return true;
}

// ---------------------------------------------------------------- OAuth
app.get("/connect", (req, res) => {
  const state = crypto.randomBytes(18).toString("base64url");
  req.session.oauthState = state;
  const params = new URLSearchParams({ response_type: "code", client_id: CLIENT_ID,
    redirect_uri: `${BASE_URL}/callback`, scope: SCOPES, state });
  res.redirect(`${MOCKBANK_URL}/oauth/authorize?${params}`);
});

app.get("/callback", async (req, res) => {
  const expected = req.session.oauthState; delete req.session.oauthState;
  if (!expected || req.query.state !== expected) return res.status(400).send("state mismatch");
  if (req.query.error) return res.status(400).send(`MockBank said: ${req.query.error}`);
  const tokenRes = await fetch(`${MOCKBANK_URL}/oauth/token`, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "authorization_code", code: req.query.code,
      redirect_uri: `${BASE_URL}/callback`, client_id: CLIENT_ID, client_secret: CLIENT_SECRET }),
  });
  if (!tokenRes.ok) return res.status(400).send(`Token exchange failed: ${await tokenRes.text()}`);
  const uid = userId(req);
  bankLinks.set(uid, await tokenRes.json());
  bankLinks.get(uid).customer = await bankGet(uid, "/api/v1/me");
  console.log(`[remitx] linked ${uid} to MockBank customer ${bankLinks.get(uid).customer.customer_id}`);
  res.redirect("/");
});

// ---------------------------------------------------------------- pages
const rand = (cents) => "R" + (cents / 100).toFixed(2);

app.get("/", async (req, res) => {
  const uid = userId(req);
  const link = bankLinks.get(uid);
  const accounts = link ? (await bankGet(uid, "/api/v1/accounts"))?.data ?? [] : [];
  const mine = [...deposits.values()].filter((d) => d.userId === uid);
  res.send(`<!doctype html><title>RemitX</title>
<style>body{font-family:sans-serif;max-width:720px;margin:40px auto}.card{border:1px solid #ddd;border-radius:8px;padding:16px;margin:12px 0}
.btn{padding:8px 14px;background:#2b4c7e;color:#fff;border-radius:6px;text-decoration:none;border:0;cursor:pointer}table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid #eee;padding:6px;text-align:left}</style>
<h1>RemitX <small style="color:#888">example fintech app (Node)</small></h1>
${req.query.msg ? `<p style="background:#fff3cd;padding:10px">${escape(req.query.msg)}</p>` : ""}
<div class="card"><h3>Your RemitX wallet (internal ledger)</h3><p style="font-size:1.6em">${rand(wallets.get(uid) ?? 0)}</p></div>
${!link ? `<div class="card"><a class="btn" href="/connect">Connect your bank (MockBank)</a></div>` : `
<div class="card"><h3>Bank accounts at MockBank</h3><p>Connected as ${escape(link.customer.full_name)}</p>
<table><tr><th>Account</th><th>Number</th><th>Balance</th><th></th></tr>
${accounts.map((a) => `<tr><td>${escape(a.name)}</td><td>${a.account_number}</td><td>R${a.balance}</td>
<td><form method="post" action="/deposit"><input type="hidden" name="account_id" value="${a.id}"><button class="btn">Deposit R100</button></form></td></tr>`).join("")}
</table></div>`}
<div class="card"><h3>Deposits</h3><table><tr><th>payment_id</th><th>reference</th><th>status</th><th>credited?</th></tr>
${mine.map((d) => `<tr><td>${d.paymentId}</td><td>${d.reference}</td><td>${d.status}</td><td>${d.credited ? "yes" : "no"}</td></tr>`).join("")}
</table></div>`);
});

function escape(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

// ---------------------------------------------------------------- payments
app.post("/deposit", express.urlencoded({ extended: false }), async (req, res) => {
  const uid = userId(req);
  const link = bankLinks.get(uid);
  if (!link) return res.redirect("/connect");
  const reference = "DEP-" + crypto.randomBytes(4).toString("hex").toUpperCase();
  const r = await fetch(`${MOCKBANK_URL}/api/v1/payments`, {
    method: "POST",
    headers: { Authorization: `Bearer ${link.access_token}`, "Content-Type": "application/json",
               "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({ debtor_account_id: req.body.account_id, creditor_account_number: SETTLEMENT_ACCOUNT_NUMBER,
      amount: "100.00", currency: "ZAR", reference, redirect_uri: `${BASE_URL}/payments/return` }),
  });
  if (r.status !== 201) return res.redirect("/?msg=" + encodeURIComponent(`Payment initiation failed: ${await r.text()}`));
  const payment = await r.json();
  deposits.set(payment.payment_id, { userId: uid, paymentId: payment.payment_id, reference,
    amountCents: Math.round(Number(payment.amount) * 100), status: payment.status, credited: false });
  res.redirect(payment.authorisation_url);
});

app.get("/payments/return", async (req, res) => {
  const uid = userId(req);
  const record = deposits.get(String(req.query.payment_id));
  if (record && record.userId === uid) {
    const fresh = await bankGet(uid, `/api/v1/payments/${record.paymentId}`);  // confirm, do not trust the query string
    if (fresh) record.status = fresh.status;
  }
  res.redirect("/?msg=" + encodeURIComponent(`Payment ${req.query.payment_id}: ${record?.status ?? "unknown"}. The wallet is credited when the webhook arrives.`));
});

// ---------------------------------------------------------------- webhooks
function verifySignature(rawBody, header, tolerance = 300) {
  const parts = Object.fromEntries((header || "").split(",").map((p) => p.split("=", 2)));
  const t = parseInt(parts.t, 10);
  if (!parts.v1 || Number.isNaN(t) || Math.abs(Date.now() / 1000 - t) > tolerance) return false;
  const expected = crypto.createHmac("sha256", WEBHOOK_SECRET).update(`${t}.`).update(rawBody).digest("hex");
  const a = Buffer.from(expected, "hex"), b = Buffer.from(parts.v1, "hex");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// express.raw keeps the exact bytes MockBank signed. express.json() would re-serialise them.
app.post("/webhooks/mockbank", express.raw({ type: "application/json" }), (req, res) => {
  if (!verifySignature(req.body, req.get("X-MockBank-Signature"))) {
    console.log("[remitx] webhook with BAD signature rejected");
    return res.sendStatus(400);
  }
  const event = JSON.parse(req.body.toString("utf8"));
  if (seenEvents.has(event.id)) return res.sendStatus(200);      // deliveries can repeat
  seenEvents.add(event.id);
  console.log(`[remitx] webhook ${event.event} ${event.id} for ${event.data.payment_id}`);
  const record = deposits.get(event.data.payment_id);
  if (record) {
    record.status = event.data.status;
    if (event.event === "payment.completed" && !record.credited) {
      wallets.set(record.userId, (wallets.get(record.userId) ?? 0) + record.amountCents);
      record.credited = true;
      console.log(`[remitx] credited ${rand(record.amountCents)} to ${record.userId}`);
    }
  }
  res.sendStatus(200);
});

const port = Number(new URL(BASE_URL).port || 3000);
app.listen(port, () => console.log(`RemitX example on ${BASE_URL}, talking to MockBank at ${MOCKBANK_URL}`));
