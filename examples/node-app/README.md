# Example: RemitX (Node / Express)

The same demo as `examples/python-app`, in JavaScript. Connects a MockBank customer with
OAuth, lists accounts, initiates a R100 deposit to RemitX's settlement account and credits the
RemitX wallet when the signed webhook arrives.

```bash
npm install
MOCKBANK_URL=http://localhost:8000 npm start
```

Open http://localhost:3000. Requires Node 18+ (uses the built-in `fetch`).

Environment variables and defaults are the same as the Python example, except `BASE_URL`
defaults to `http://localhost:3000`. The demo application has `localhost:3000` redirect URIs
registered, but its webhook URL points at port 5000; ask the instructor to set it to
`http://host.docker.internal:3000/webhooks/mockbank` for your app, or run the Python example on
5000 while reading this one.
