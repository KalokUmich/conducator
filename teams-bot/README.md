# Conductor Teams Bot — Packaging & Deployment Guide

A Microsoft Teams bot that forwards `@mention` messages from Teams channels and
chats to the Conductor backend. This folder contains the Teams app package
builder — the manifest, icons, and a `build.py` script that assembles the
sideloadable `.zip`.

> **Phase 1 (connectivity proof, currently shipped)**: no AI logic — the backend
> only logs the incoming `Activity` payload and returns `200`. Goal is to prove
> the pipe works end-to-end before layering AI.
>
> **Phase 2 (planned)**: JWT validation, Graph API history reads, AI summarization,
> Adaptive Card replies. See `docs/TEAMS_BOT_DEPLOYMENT.md` for the full two-phase
> plan and DevOps / backend split.

---

## 1. Architecture at a glance

Teams apps have **no client-side logic**. The manifest we ship here is just a
pointer; every decision about what the bot does happens in the backend.

```
User types "@Conductor hello" in a Teams channel
        │
        ▼
Microsoft Bot Framework Connector (cloud, managed by MS)
        │   routes by botId → Azure Bot Service resource
        │   reads the "Messaging endpoint" configured on that resource
        ▼
POST https://<our-host>/api/integrations/teams/bot/messages
  body: JSON "Activity"   auth: Bearer JWT signed by Bot Framework
        │
        ▼
backend/app/integrations/teams/router.py  ← 100% of the business logic
```

Per Teams privacy rules, we only see:
- messages in a channel **where the bot is @mentioned**, or
- all messages in a **1:1 chat** with the bot.

Reading arbitrary channel history requires the Graph API
`ChatMessage.Read.All` permission (already granted) and an app-only token —
that is Phase 2 work.

---

## 2. Prerequisites

### 2.1 On the Azure / Microsoft side (IT / DevOps)

Ask IT/DevOps to complete these. They need Azure subscription access + Teams
Service Admin role.

1. **Azure AD App Registration** with Microsoft Graph `ChatMessage.Read.All`
2. **Azure Bot Service resource** (F0 tier) bound to that App Registration
3. **Microsoft Teams channel enabled** on the Bot Service (`Channels → Microsoft Teams → Commercial`)
4. **Messaging endpoint** on the Bot Service set to:
   ```
   https://<your-public-host>/api/integrations/teams/bot/messages
   ```
5. **Upload custom apps** allowed in your Teams app setup policy
   (`Teams Admin Center → Teams apps → Setup policies`)

See `docs/TEAMS_BOT_DEPLOYMENT.md` for the IT-facing deployment details.

### 2.2 On your local dev box

- Conductor backend running on `localhost:8000` (`make run-backend`)
- A public HTTPS tunnel pointing at port 8000:
  ```bash
  # Stable ngrok reserved domain (recommended for Phase 1)
  ngrok http --domain=<your-reserved-subdomain>.ngrok.app 8000

  # Or Cloudflare Tunnel
  cloudflared tunnel --url http://localhost:8000
  ```
- Your **App Registration Client ID** populated in
  `config/conductor.secrets.local.yaml` under `teams.app_id`. `make package-teams-bot`
  reads it from there automatically.

### 2.3 Self-test the tunnel before packaging

```bash
curl https://<tunnel-host>/api/integrations/teams/health
```

Expected: HTTP 200 + JSON containing `"configured": { "app_id_present": true, ... }`.
A 502 means the tunnel is down; a 404 means the Teams router was not registered
in `main.py`.

---

## 3. Build the app package

### Recommended: Makefile target

```bash
# Set this once per shell session (or add to ~/.zshrc)
export TEAMS_TUNNEL_HOST=<your-reserved-subdomain>.ngrok.app

make package-teams-bot
```

Output: `teams-bot/build/conductor-teams-app.zip`.

What the target does:
- Reads `app_id` from `config/conductor.secrets.local.yaml` (`teams.app_id`)
- Requires `TEAMS_TUNNEL_HOST` as an env or make var (bare host, no `https://`)
- Invokes `build.py` which:
  1. Generates placeholder PNG icons (192×192 color, 32×32 outline)
  2. Renders `manifest.template.json` with `{{BOT_ID}}`, `{{TUNNEL_HOST}}`,
     `{{MANIFEST_ID}}`, `{{VERSION}}`
  3. Zips the three files at the zip root

The manifest `id` GUID is generated once and persisted to `teams-bot/.manifest-id`
so subsequent builds update the same Teams app rather than spawning a new one.

### One-shot override

```bash
make package-teams-bot TEAMS_TUNNEL_HOST=kalok-test.ngrok.app

# Use a different bot (e.g. a prod App Registration)
make package-teams-bot \
  TEAMS_BOT_ID=<other-client-id> \
  TEAMS_TUNNEL_HOST=conductor-bot.fintern.ai
```

### Direct script usage

```bash
cd teams-bot
python3 build.py \
  --bot-id <Application (client) ID from Azure> \
  --tunnel-host <host without https://> \
  [--version 1.0.1] \
  [--manifest-id <uuid>]
```

---

## 4. Validate the package before uploading

**Always validate in the Teams Developer Portal first** — the sideload
experience gives useless errors like "Manifest parsing error message unavailable",
but the Developer Portal lists every field that fails validation.

1. Go to https://dev.teams.microsoft.com/apps (sign in with your M365 account)
2. Click **Import app** → select `teams-bot/build/conductor-teams-app.zip`
3. If errors appear, fix `manifest.template.json` (see §7 below), rebuild, re-import

Only sideload to the Teams client once the Developer Portal import is clean.

---

## 5. Install in Teams

Three possible paths depending on your tenant's policies. Try them in order.

### Path A — Personal sideload (simplest, if your tenant allows it)

Requires `Upload custom apps` enabled in your Teams setup policy.

1. **Teams desktop client** (sideloading is flaky in the web client)
2. Left rail `Apps` → bottom-left `Manage your apps` → `Upload an app` →
   `Upload a custom app`
3. Select `teams-bot/build/conductor-teams-app.zip`
4. After the detail page appears:
   - `Add to a team` → pick a test team → pick a channel, OR
   - `Add to a chat` → pick a 1:1 or group chat

If the `Upload a custom app` option is missing, the setup policy blocks
sideloading — see §6.1.

### Path B — Admin uploads + adds you manually (ACM tenants)

If your tenant uses **App Centric Management** (most enterprise tenants after
late 2024), the admin does both the upload and the install for you.

Ask IT:

```
1. Upload:
   Teams Admin Center → Teams apps → Manage apps → Actions → Upload new app
   → select conductor-teams-app.zip

2. Approve:
   Open the uploaded app → set Publishing status to Allowed

3. Install for me:
   On the same app → click "Edit installs"
   → add my account under Selected users and groups → Apply
   (ACM's install step also grants availability — no separate permission
   policy change needed.)

4. Verify prerequisite:
   Teams apps → Manage apps → Org-wide app settings → Custom apps
   → "Let users install and use available apps by default" must be ON
```

Propagation to your client is documented as **up to 24 hours** but a full
Teams restart typically surfaces the install within minutes.
Reference: [Microsoft — Preinstall Teams apps](https://learn.microsoft.com/en-us/microsoftteams/install-teams-apps)

### Path C — Admin adds the bot directly to a chat

Simplest path for single-user testing when ACM propagation is stalled. The
admin manually starts a chat with the bot (or an existing chat) and adds the
bot as a participant. Messages in that chat then flow to your backend
immediately.

This sidesteps the entire catalog/permission/install machinery. Great for
Phase 1 smoke testing. Not suitable for broad rollout.

---

## 6. Trigger the bot and verify

### 6.1 @mention inside a channel or group chat

In the channel/chat where the bot was added, type:

```
@Conductor
```

Teams shows a dropdown — **select the bot from the dropdown**, not just the
typed text. The bot's name will highlight as a chip (blue pill). Then type
your message and send:

```
[Conductor (Dev)] hello
```

### 6.2 1:1 chat with the bot

No `@mention` needed — every message in the chat goes to the bot. Just type
and send.

### 6.3 Expected backend log

Switch to the terminal running `make run-backend`. Within ~2 seconds of
sending, you should see:

```
INFO [app.integrations.teams.router] [teams] Activity received
  type=message from='Your Name' conversation=19:xxx@thread.tacv2
  text='hello' auth_header=True
```

**`auth_header=True` is the key** — it proves Microsoft is actually
signing JWTs and Phase 2 has everything it needs for token validation.

---

## 7. Manifest editing gotchas

Teams' validator is strict and the error messages from the client are often
cryptic. These are landmines we already hit — avoid them when editing
`manifest.template.json`.

### Will reject your upload

| Field | Rule | Notes |
|---|---|---|
| `version` | MUST NOT start with `0`. `1.0.0` OK, `0.1.0` rejected | Teams enforces SemVer ≥ 1.0.0 |
| `version` | MUST increment on every update to an installed app | Teams uses it to detect upgrades |
| `id` | Plain lowercase GUID, no braces, no prefix | Validator: "product ID must be plain GUID" |
| `manifestVersion` | Must match the schema referenced by `$schema` | Currently on `1.19` |
| `packageName` | **Removed** in manifest 1.19+. Do **not** add it back | Validator: "property not defined" |
| `permissions` (top-level) | Deprecated since 1.13, moved under `authorization` | We omit it entirely |
| Non-ASCII characters | Avoid em-dash (`—`), smart quotes in `name`/`description` | Some clients silently fail to parse |

### Subtle but valid

| Field | Note |
|---|---|
| `validDomains` | Bare hostnames only — no `https://`, no path, no trailing slash |
| `developer.*Url` | Must be valid URIs; Teams does **not** verify the pages exist |
| `icons.color` | 192×192 PNG, full-bleed color (no transparency) |
| `icons.outline` | 32×32 PNG, white shape on transparent background |
| `bots[].scopes` | Only `personal`, `team`, `groupChat` allowed |

### Bumping the manifest schema version

When Teams releases a new manifest version:

1. Update `"$schema"` URL in `manifest.template.json` (the version segment)
2. Update `"manifestVersion"` to match
3. `make package-teams-bot TEAMS_TUNNEL_HOST=...`
4. Re-validate in the Developer Portal — new versions often remove fields

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Developer Portal: schema errors | Manifest field violation | See §7; re-read the exact error — usually a removed/renamed field |
| Client: "Manifest parsing error message unavailable" | Schema error with unhelpful client-side message | Always validate via Developer Portal (§4) before sideloading |
| `Upload a custom app` menu missing | Tenant setup policy blocks sideloading | Ask IT to enable it; or go via Path B / Path C |
| Upload returns "pending for approval" | Tenant has a custom app submission policy | IT approves in `Admin Center → Manage apps` |
| Admin approved but app not visible in client | ACM propagation delay, or `Let users install and use available apps by default` is off | See Path B checklist; full Teams restart; wait ≤ 24h |
| `@mention` visible in Teams, no request hits backend | Messaging endpoint URL on Bot Service wrong, or tunnel down | Curl the `/health` endpoint to confirm tunnel; verify URL in Azure Portal |
| Backend receives request but `auth_header=False` | Teams channel not enabled on the Bot Service | IT: `Bot resource → Channels → Microsoft Teams → Commercial → Apply` |
| `@Conductor` dropdown doesn't include the bot | Bot not installed in this conversation's scope | Re-add via Path A/B/C |

---

## 9. Folder contents

```
teams-bot/
├── README.md               This file
├── manifest.template.json  Placeholder-substituted manifest shipped in the zip
├── build.py                Stdlib-only builder: icons + manifest + zip
├── .gitignore              Excludes build/ and .manifest-id from git
└── build/                  (gitignored) generated artefacts
    ├── manifest.json
    ├── color.png           192×192 solid indigo (placeholder)
    ├── outline.png         32×32 white circle on transparent (placeholder)
    └── conductor-teams-app.zip
```

The indigo/white placeholder icons are fine for Phase 1 testing. For Phase 2
production rollout, replace with designed assets from Product. The current
`build.py` regenerates icons on every run; swap it to copy committed assets
when Phase 2 icons land.

---

## 10. Related docs

- `docs/TEAMS_BOT_DEPLOYMENT.md` — Full two-phase plan, DevOps vs backend split
- `backend/app/integrations/teams/router.py` — The receiving endpoint and
  module docstring with the full data-flow explanation
- `backend/app/config.py` — `TeamsSettings` and `TeamsSecretsConfig` models
