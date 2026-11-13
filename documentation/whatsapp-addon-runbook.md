# WhatsApp Add-on Staging Runbook

This runbook validates the paid WhatsApp add-on from global offer setup through
Stripe Checkout return verification, shared workspace token enforcement, and Meta webhook
delivery. Complete it in a Stripe test-mode environment before enabling the
add-on in production.

## 1. Configure the staging environment

Set these values in `Backend_Agentstore/.env` or in the backend deployment's
secret manager:

```dotenv
AUTH_SECRET_KEY=<stable-random-secret>
FRONTEND_URL=https://staging-app.example.com
PUBLIC_BACKEND_URL=https://staging-api.example.com
STRIPE_SECRET_KEY=sk_test_...
WHATSAPP_GRAPH_API_VERSION=v26.0
```

Requirements:

- `FRONTEND_URL` is the exact origin Stripe redirects to after checkout.
- `PUBLIC_BACKEND_URL` is a public HTTPS origin reachable by Stripe and Meta.
- `AUTH_SECRET_KEY` remains stable because it protects stored Meta credentials.
- Test and live Stripe credentials must never be mixed.

Restart the backend after changing environment values.

## 2. Confirm the Checkout return flow

Confirm the frontend and backend expose these authenticated routes through the
same public deployment:

```text
POST https://staging-api.example.com/api/auth/whatsapp/addon/checkout
POST https://staging-api.example.com/api/auth/whatsapp/addon/payment-success
```

Checkout redirects to the dashboard with these query parameters:

```text
whatsapp_addon=success&session_id={CHECKOUT_SESSION_ID}
```

The dashboard sends the Session ID to the authenticated payment-success route.
The backend retrieves the Checkout Session from Stripe and verifies its payment
status, workspace metadata, local pending payment, and Stripe subscription before
enabling access. The Session ID from the browser is never trusted by itself. This
flow does not require `STRIPE_WEBHOOK_SECRET`.

## 3. Publish the global offer

1. Sign in as a super admin and open the subscription configuration area.
2. Set **Monthly Price (USD)**.
3. Enable add-on availability and save.
4. Confirm the save succeeds without a pending Stripe price synchronization.

The offer has one global price for every purchasing company. A price change
updates Stripe subscription items without proration and takes effect on the
next renewal. WhatsApp agent calls consume the token quota of the workspace's
subscription package. Grandfathered legacy access is not repriced.

## 4. Validate purchase and activation

Use a company that does not already have WhatsApp access:

1. Sign in as the company admin and open **Configure WhatsApp**.
2. Confirm the global monthly price and shared workspace token quota notice are displayed.
3. Select **Purchase add-on** and complete Stripe Checkout with test card
   `4242 4242 4242 4242`, any future expiry, and any CVC.
4. Return to the dashboard and wait for payment verification to complete.
5. Reopen **Configure WhatsApp** and confirm the add-on is active.
6. Confirm the charge appears in payment history as **WhatsApp Add-on**.

Expected behavior:

- Checkout creates one pending subscription and one pending payment.
- Reopening an unexpired checkout reuses the existing Stripe session.
- Return verification enables the entitlement.
- Repeating verification does not duplicate billing records.

## 5. Validate cancellation and reactivation

1. Select **Cancel renewal** and confirm access remains active until the shown
   billing-period end.
2. Select **Restore renewal** before the period ends and confirm normal renewal
   is restored.

These actions call Stripe directly and update local state, so they do not require
a webhook. Until the deferred webhook is configured, automatic renewals, failed
invoices, expired Checkout sessions, and changes made in Stripe Dashboard must be
reconciled manually.

## 6. Configure Meta and exercise the shared workspace quota

After the test company has an active add-on:

1. Enter its WhatsApp business number, Meta app secret, Phone Number ID, and
   system-user access token in **Configure WhatsApp**.
2. Copy the generated callback URL and verify token into the Meta app's
   WhatsApp webhook settings.
3. Subscribe the Meta app to WhatsApp message events and confirm the dashboard
   reports the webhook as verified.
4. Grant WhatsApp access to at least two agents.
5. Send a message to the business number, select an agent, and verify only that
   agent responds until `/exit` is sent.

Workspace token quota acceptance checks:

- Opening the agent menu, selecting an agent, `/exit`, duplicate delivery, and
  outbound retry do not invoke a model and therefore consume no tokens.
- Each inbound message that invokes an agent charges the provider-reported
  input and output tokens to the workspace quota.
- The workspace chat usage total refreshes after processing.
- A message that starts below the limit completes and records its actual usage.
- The next message receives the workspace quota-exhausted response without
  invoking an agent.

## 7. Release checklist

- [ ] Staging uses a Stripe test-mode secret key.
- [ ] A paid Checkout return activates the add-on through direct Session
      verification.
- [ ] Purchase, duplicate checkout, cancellation, and reactivation behave as
      described.
- [ ] Payment history labels add-on invoices correctly.
- [ ] Meta verifies the company-specific callback URL over HTTPS.
- [ ] Agent selection, `/exit`, and quota exhaustion work from a real WhatsApp
      test number.
- [ ] Super-admin price changes respect their next-period timing.
- [ ] Production secrets are stored in the deployment secret manager, not in
      source control.
- [ ] Production `PUBLIC_BACKEND_URL` and `FRONTEND_URL` are set before enabling
      add-on availability.
- [ ] Operations has a process for reconciling renewals, failed invoices, expired
      Checkout sessions, and Stripe-side changes until webhooks are enabled.

## 8. Deferred Stripe webhook validation

When Stripe Dashboard access becomes available, configure
`STRIPE_WEBHOOK_SECRET`, register
`https://aisleagents.raccoon-ai.com/api/stripe/webhook`, and subscribe to:

```text
checkout.session.completed
checkout.session.expired
customer.subscription.created
customer.subscription.updated
customer.subscription.deleted
invoice.paid
invoice.payment_failed
```

Then verify successful signed-event processing, retry after a processing failure,
and duplicate-event idempotency. This can be enabled later without changing the
Checkout-return activation flow.

Run the automated regression checks before release:

```bash
cd Backend_Agentstore
python -m unittest discover -p "test_*.py"

cd ../chatbot
CI=true npm test -- --watchAll=false --runTestsByPath \
  src/components/WhatsAppConfiguration.test.js
npm run build
```
