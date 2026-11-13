# WAHA WhatsApp Provider Runbook

The WhatsApp add-on supports either Meta Cloud API or WAHA per workspace. Both
providers use the same agent menu, selected-agent conversation, `/exit`, token
quota, deduplication, outbox, and retry pipeline.

## 1. Deploy WAHA

Run WAHA as a private service and persist `/app/.sessions`. Configure its API
and dashboard authentication. Do not expose the WAHA API directly to the public
internet.

Set the backend URL used for outbound WAHA requests:

```dotenv
WAHA_BASE_URL=http://waha:3000
```

`WAHA_BASE_URL` is deployment-controlled and is intentionally not editable by
company admins. If the backend runs in Docker while WAHA is published on the
Docker host, use `http://host.docker.internal:3000`. Restart the backend after
changing this value.

## 2. Configure the workspace

The company must have an active WhatsApp add-on subscription. As a company
admin, open **Configure WhatsApp** and:

1. Select **WAHA** as the messaging provider.
2. Enter the connected WhatsApp number in E.164 format.
3. Enter a unique WAHA session name for the workspace.
4. Enter an admin or session-scoped WAHA API key with send permission.
5. Save the setup.
6. Copy the generated callback URL and HMAC secret.

The API key is encrypted at rest and is never returned by the configuration
API. Use a separate WAHA session and preferably a session-scoped key for every
workspace.

## 3. Configure the WAHA session webhook

Configure the workspace session with the callback URL and HMAC secret displayed
by Agent Store. Subscribe only to `message` and `message.ack`:

```json
{
  "name": "workspace-41",
  "config": {
    "webhooks": [
      {
        "url": "https://api.example.com/api/whatsapp/webhook/WORKSPACE_KEY",
        "events": ["message", "message.ack"],
        "hmac": {"key": "HMAC_SECRET_FROM_AGENT_STORE"},
        "retries": {
          "policy": "exponential",
          "delaySeconds": 2,
          "attempts": 4
        }
      }
    ]
  }
}
```

Create a session with `POST /api/sessions/`, or update an existing session with
`PUT /api/sessions/{session}`. WAHA treats an update as a full configuration, so
retain any other required session settings.

Start the session, scan its QR code, and wait for `WORKING`. The first valid,
signed webhook event marks the webhook active in Agent Store. Events signed
with another secret or carrying another session name are rejected.

References:

- https://waha.devlike.pro/docs/overview/quick-start/
- https://waha.devlike.pro/docs/how-to/events/
- https://waha.devlike.pro/docs/how-to/send-messages/
- https://waha.devlike.pro/docs/how-to/security/

## 4. Acceptance test

1. Enable WhatsApp on at least two deployed agents.
2. Send a text message from a second WhatsApp account.
3. Confirm Agent Store returns the same numbered agent menu used by Meta.
4. Select one agent and confirm subsequent messages reach only that agent.
5. Send `/exit` and confirm the menu returns.
6. Confirm `message.ack` events update outbound status to sent, delivered, read,
   played, or failed.
7. Redeliver the same inbound event and confirm no duplicate response is sent.
8. Confirm only agent invocations consume workspace tokens.

Only direct text conversations are routed to agents. Messages sent by the
connected account itself and group messages are ignored. Media receives the
existing unsupported-message behavior.
