# Slack Integration Setup Guide

## Overview

The Axiometica AIR integrates with Slack in two directions:

**Outbound notifications** — the platform posts to a Slack channel automatically when:
- A new **critical or high severity** incident is created
- An incident reaches a **terminal state** (resolved, deployed, rolled back, rejected)
- A **remediation approval** is requested
- A new **event storm** is detected

**Inbound chat (ChatOps)** — operators can talk directly to the AI Ops Assistant from any Slack channel or DM:
- Query active incidents, approvals, MTTR, risk scores
- Ask about runbooks and remediation history
- Say `approve INC0042` or `reject INC0042` — the bot replies with interactive Confirm / Cancel buttons
- Clicking Confirm writes the decision directly to the platform (role-gated: viewers can query but cannot act)

---

## Step 1 — Create the Slack App

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** → **From Scratch**
3. Name it (e.g. `AIOps Assistant`) and select your workspace
4. Click **Create App**

---

## Step 2 — Add OAuth Scopes

In your app's left sidebar: **OAuth & Permissions** → scroll to **Scopes** → **Bot Token Scopes** → add:

| Scope | Purpose |
|---|---|
| `chat:write` | Post and update messages |
| `app_mentions:read` | Receive @bot mentions |
| `im:read` | Receive direct messages |
| `im:write` | Send direct messages |
| `users:read` | Look up user profiles |
| `users:read.email` | Map Slack user → platform role |

> **Note:** `im:history` is **not** required. The bot answers each message using live platform
> data (incidents, approvals, MTTR) — it does not read previous Slack messages to build context.

---

## Step 3 — Install the App

**OAuth & Permissions** → **Install to Workspace** → **Allow**

Copy the **Bot User OAuth Token** (starts with `xoxb-`).

---

## Step 4 — Choose Inbound Mode

Inbound chat (receiving messages from Slack) requires one of two approaches.
Pick the one that fits your deployment.

### Option A — Socket Mode (recommended for self-hosted / local)

No public URL required. The backend opens an outbound WebSocket to Slack.

**4a.** In your Slack app: **Settings** → **Socket Mode** → Enable

> ⚠️ **Do this first.** If Socket Mode is not enabled before you configure Event Subscriptions,
> Slack requires a Request URL and will silently revert the "Enable Events" toggle when you save
> without one.

**4b.** **Basic Information** → scroll to **App-Level Tokens** → **Generate Token and Scopes**
- Name: `socket-mode` (or anything)
- Scope: `connections:write`
- Click **Generate**
- Copy the **App-Level Token** (starts with `xapp-`)

**4c.** **Event Subscriptions** → Enable → subscribe to **Bot Events**:
- `app_mention`
- `message.im`

> You do **not** need to set a Request URL in Socket Mode. With Socket Mode enabled, Slack shows
> a note on this page confirming the URL is optional and delivers events through the WebSocket.

**4d.** **Interactivity & Shortcuts** → Enable
> No Request URL needed for Socket Mode either — Slack routes button clicks through the same socket.

**4e.** Reinstall the app after any scope change (**OAuth & Permissions** → **Reinstall**).

---

### Option B — Events API / Webhook (requires a public URL)

Use this if the backend is deployed to a cloud host with a public HTTPS URL,
or locally via [ngrok](https://ngrok.com).

**If using ngrok locally:**
```bash
winget install ngrok
ngrok config add-authtoken <your-token>   # free at ngrok.com
ngrok http 8000
# Copy the https://xxxx.ngrok-free.app URL
```

**4a.** **Event Subscriptions** → Enable → **Request URL**:
```
https://<your-host>/api/webhooks/slack/events
```
Slack will send a challenge request; the platform handles it automatically.

Subscribe to **Bot Events**: `app_mention`, `message.im`

**4b.** **Interactivity & Shortcuts** → Enable → **Request URL**:
```
https://<your-host>/api/webhooks/slack/actions
```

**4c.** Reinstall the app after any scope change.

> **Note:** The free ngrok tier assigns a new URL on every restart.
> You must update the Request URL in your Slack app each time.
> A paid ngrok plan or a cloud deployment gives a stable URL.

---

## Step 5 — Configure the Platform

Open the platform UI → **Settings** → **Slack ChatOps**.

| Field | Value |
|---|---|
| Enable Slack ChatOps | ✓ On |
| Bot Token | `xoxb-…` (from Step 3) |
| Signing Secret | From **Basic Information → App Credentials** |
| App-Level Token | `xapp-…` (Socket Mode only — leave blank for webhook mode) |
| Default Channel | Channel for outbound notifications, e.g. `#incidents` |

Click **Save Slack Settings**, then **Test Connection** to verify the bot token works.

### Notification toggles

| Toggle | Default | What it controls |
|---|---|---|
| New Incident Notifications | Off | Critical / high severity incidents only |
| Incident Resolved Notifications | On | Terminal states: resolved, deployed, rolled back, rejected |
| Approval Required Notifications | On | New pending approvals (with Confirm / Cancel buttons) |
| Event Storm Notifications | On | New event storm detected |

---

## Step 6 — Activate Socket Mode (if using Option A)

After saving the App-Level Token, restart the backend:

```bash
docker compose restart backend
```

Check the logs for confirmation:

```bash
docker logs agentic_os_backend 2>&1 | grep SlackSocket
# Expected: [SlackSocket] ✓ Connected — listening for @mentions and DMs (no public URL needed)
```

> Socket Mode starts automatically on every subsequent restart as long as the token is saved.
> If you clear or change the token, restart the backend again.

---

## Step 7 — Invite the Bot to Channels

For the bot to post outbound notifications or receive @mentions in a channel,
it must be a member:

```
/invite @AIOps-Assistant
```

For DMs: no invite needed — users can DM the bot directly.

---

## Using the Integration

### Outbound notifications (automatic)

Once configured, the platform posts to the **Default Channel** automatically.
No operator action required.

Example messages:

```
🔴 *New CRITICAL Incident: INC0042*
High CPU on prod-api-gateway
State: `triaging`
Risk Score: *91/100*

⏳ *Approval Required: INC0042*
[CRITICAL] High CPU on prod-api-gateway
Risk Score: *91/100*
Action: `restart_service` on `prod-api-01`
Blast Radius: 2 | Est. Recovery: 5m
A remediation action is waiting for your approval.

✅ *Incident Resolved: INC0042*
[CRITICAL] High CPU on prod-api-gateway
Risk Score: *91/100*
Outcome: `service_restart_successful`
```

### Inbound chat (ChatOps)

@mention the bot in any channel it belongs to, or send it a DM:

```
@AIOps-Assistant what incidents are open right now?
@AIOps-Assistant show me INC0042
@AIOps-Assistant approve INC0042
@AIOps-Assistant reject INC0042 — false positive, CPU spike was a deploy
```

When you request an approve or reject, the bot replies with **✓ Confirm** and **✗ Cancel** buttons.
Clicking Confirm writes the decision to the platform immediately.

### Role enforcement

The bot maps your Slack account's email to your platform role:

| Role | Can query | Can approve / reject |
|---|---|---|
| `admin` / `itom_admin` / `operator` | ✓ | ✓ |
| `viewer` | ✓ | ✗ |
| Not registered on platform | ✗ | ✗ |

> Unregistered users receive an *"Access restricted"* message and no platform data is returned.
> Contact your ITOM Admin to add users via **Settings → Users**.

---

## Troubleshooting

### "No Slack bot token configured"
The token was not saved. Go to **Settings → Slack ChatOps**, clear the Bot Token field, paste the `xoxb-…` token, and click **Save**.

### "invalid_auth" from Slack API
The bot token is wrong or revoked. Regenerate it in your Slack app under **OAuth & Permissions** and save the new value.

### "not_in_channel" when posting notifications
The bot is not a member of the default channel. Run `/invite @<bot-name>` in that channel.

### Socket Mode not connecting after saving App-Level Token
The token is read at startup. Restart the backend after saving:
```bash
docker compose restart backend
```
Then check logs: `docker logs agentic_os_backend 2>&1 | grep SlackSocket`

### "slack-sdk not installed"
The Docker image was not rebuilt after `slack-sdk` was added to `requirements.txt`. Run:
```bash
docker compose build backend && docker compose up -d backend
```

### Bot responds but action buttons do nothing
Interactivity is not enabled or the Request URL is wrong (webhook mode only).
Check **Interactivity & Shortcuts** in your Slack app. In Socket Mode, no URL is needed — just ensure Socket Mode is enabled.

### "Enable Events" toggle keeps reverting to Off
Event Subscriptions requires a Request URL when Socket Mode is **not** enabled. If you enable
events without a URL, Slack silently turns it back off on save.

Fix: enable **Socket Mode first** (**Settings → Socket Mode → On**), then return to
**Event Subscriptions** and enable events — no URL needed once Socket Mode is active.

### Duplicate notifications
The deduplication set is per-process. If the backend is running with multiple workers
and restarts frequently, a single incident could generate two "resolved" messages in rare
cases. This resolves itself — subsequent restarts will not re-notify for already-terminal incidents.
