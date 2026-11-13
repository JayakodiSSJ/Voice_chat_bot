## Build Deployment Enviornment

### Front-end

- Navigate to the `aisle-agent-store/chatbot/src/urls.js` file and change the *BASE_URL* to server url address.
- Navigate to the front-end directory

```bash
cd aisle-agent-store/chatbot
```
- Build the app
```bash
npm run build
```
- Navigagte to the `build` dir
```bash
cd build
```
- Copy the built files into nginx dir
```bash
cp -r * /usr/share/nginx/html
```

### Backend

- Build the backend dockerfile
```bash
docker build -t flask-api-app .
```

- Run the backend on the Docker network only. Do not publish backend, database, or vector database ports directly to the internet.
```bash
docker compose up -d
```

### Local Development Ports

Use the development override only when local tools need to reach Docker services through `localhost`.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

This binds Postgres, Qdrant, and the backend to `127.0.0.1` only:

- Postgres: `localhost:5433`
- Qdrant: `localhost:6335`
- Backend: `localhost:5001`

### Production Admin Port Hardening

Do not expose admin interfaces directly to the internet. Port `9090` must remain closed unless it is protected behind a VPN, private network, or strict IP allowlist.

On a Linux host using UFW, the production baseline should deny `9090` and allow only the public web entry points:

```bash
sudo ufw deny 9090/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

If SSH is required, restrict it to trusted admin IPs instead of opening it globally.

Verify from outside the server:

```bash
nmap -Pn -p 9090 aisleagents.raccoon-ai.com
```

The expected result is `closed` or `filtered`, not `open`.

### Production HSTS

HSTS is sent by Nginx with:

```http
Strict-Transport-Security: max-age=31536000; includeSubDomains
```

Browsers only enforce HSTS when the response is served over HTTPS. In production, make sure `aisleagents.raccoon-ai.com` has a valid TLS certificate, serves the app on `443`, and redirects plain HTTP on `80` to HTTPS.

### Production CORS

The backend API uses an explicit CORS allowlist. Set the production frontend origin in the backend environment:

```bash
FRONTEND_URL=https://aisleagents.raccoon-ai.com
```

If additional trusted origins are required, add them as a comma-separated list:

```bash
CORS_ALLOWED_ORIGINS=https://admin.example.com,https://support.example.com
```

Do not use `*` for authenticated API routes.

### WhatsApp Cloud API

The WhatsApp add-on supports a per-workspace provider choice: Meta Cloud API or
WAHA. Meta remains the default for existing connections.

Set the backend's public HTTPS origin so each workspace receives a callback URL that Meta can reach:

```bash
PUBLIC_BACKEND_URL=https://api.example.com
WHATSAPP_GRAPH_API_VERSION=v26.0
WHATSAPP_API_TIMEOUT_SECONDS=20
WHATSAPP_WORKER_THREADS=2
```

`WHATSAPP_GRAPH_API_VERSION` is configurable so deployments can pin and deliberately upgrade the Meta Graph API version.

For each entitled workspace, the company admin must configure these values in the WhatsApp configuration dialog:

- The WhatsApp business number.
- The Meta app secret used to validate webhook signatures.
- The Meta Phone Number ID used by the `/messages` endpoint.
- A system-user access token with `whatsapp_business_messaging` permission.

Add the generated callback URL and verify token to the Meta app's WhatsApp webhook configuration. Access tokens and app secrets are encrypted at rest and are never returned by the configuration API.

### WAHA WhatsApp Provider

Deploy WAHA as a private service and configure the backend with its internal
origin:

```bash
WAHA_BASE_URL=http://waha:3000
```

Each workspace selecting WAHA configures its E.164 number, unique WAHA session,
and API key in **Configure WhatsApp**. Copy the generated callback URL and HMAC
secret into that WAHA session and subscribe to `message` and `message.ack`.
WAHA API keys are encrypted at rest and are never returned by the API.

See the [WAHA provider runbook](waha-adapter-runbook.md) for setup and acceptance
testing.

### Paid WhatsApp Add-on Billing

The paid add-on currently uses the same authenticated Checkout-return flow as
workspace subscription purchases. Configure Stripe's secret key in the backend
environment:

```bash
STRIPE_SECRET_KEY=sk_live_replace_me
```

After Checkout, Stripe redirects the company admin to the dashboard with the
Checkout Session ID. The dashboard sends that ID to the authenticated
`/api/auth/whatsapp/addon/payment-success` endpoint. The backend retrieves the
Session and subscription directly from Stripe, verifies the workspace and
feature metadata against the pending local purchase, and activates the add-on.
`STRIPE_WEBHOOK_SECRET` is not required for this flow.

This flow verifies the initial purchase and supports cancel/restore actions made
inside the application through direct Stripe API calls. Without a Stripe
webhook, automatic renewals, failed invoices, expired Checkout sessions, and
changes made in the Stripe Dashboard are not synchronized automatically.

#### Deferred Stripe webhook configuration

The signed webhook implementation remains available for when Stripe Dashboard
access is granted. At that time, set:

```bash
STRIPE_WEBHOOK_SECRET=whsec_replace_me
```

Register `https://aisleagents.raccoon-ai.com/api/stripe/webhook` in Stripe and
subscribe it to:

- `checkout.session.completed` and `checkout.session.expired`
- `customer.subscription.created`, `customer.subscription.updated`, and
  `customer.subscription.deleted`
- `invoice.paid` and `invoice.payment_failed`

The endpoint validates Stripe signatures, processes events idempotently, and
returns failures to Stripe for retry. Complete the [WhatsApp add-on staging
runbook](whatsapp-addon-runbook.md) in test mode before using production
credentials or enabling add-on availability.

### Production TCP Timestamp Hardening

TCP timestamps are controlled by the network stack that terminates the public TCP connection. For Docker Compose deployments, the frontend and backend containers disable TCP timestamps with:

```yaml
sysctls:
  net.ipv4.tcp_timestamps: "0"
```

Recreate the containers after changing this setting:

```bash
docker compose up -d --force-recreate
```

Verify inside the public frontend container:

```bash
docker exec aisle-frontend cat /proc/sys/net/ipv4/tcp_timestamps
```

The expected value is `0`.

For Kubernetes or cloud deployments, apply the equivalent hardening at the node, load balancer, or Ingress controller layer. The scanner will report the behavior of the public endpoint, not necessarily the application pod behind it.
