# TruBot 🤖

**TruBot** is a Telegram-based monitoring and management bot for **TrueNAS SCALE**, with system health, storage pools, application management, and actionable alerts.

> [!IMPORTANT]
> This repository contains source code and example configuration only. Never commit your real Telegram bot token, TrueNAS API key, chat IDs, or `state.json`.

## Features

- **`/status`** — Host, IP address, uptime, CPU load/temperature, memory, storage pools, disk temperatures, and UPS status when configured.
- **`/apps`** — Installed TrueNAS application status.
- **`/alerts`** — Active TrueNAS alerts with acknowledgement. Application-update alerts show an **⬆️ Update** action beside **✅ Acknowledge**.
- **Automatic alert notifications** — TrueNAS alert subscription plus polling fallback; application-update notifications are independently checked via `app.query`, with persistent per-chat deduplication and an update shortcut.
- **`/updates`** — Available application updates, selectable updates, and per-application progress/error messages.
- **`/restart`** — Restart explicitly allowed applications.
- **`/version`** — Display the TruBot image version.

The bot is written in Python and uses the TrueNAS WebSocket API and the Telegram Bot API. No incoming ports or webhook are required.

## Telegram commands

The Telegram command menu and `/help` share one set of descriptions:

| Command | Description |
| --- | --- |
| `/status` | System health |
| `/apps` | Installed apps |
| `/alerts` | Active alerts |
| `/updates` | App updates |
| `/restart <app>` | Restart an app |
| `/version` | Bot version |
| `/help` | List commands |

TruBot prompts, buttons, and status messages use English. Alert content returned by the TrueNAS API is shown as provided by TrueNAS.

## Installation on TrueNAS SCALE

Recommended: use the prebuilt Docker image from GitHub Container Registry rather than a source bind mount.

**Image:** `ghcr.io/szlovakricsi/trubot:latest`

1. Open **TrueNAS → Apps → Install via YAML** (or the appropriate Custom App workflow).
2. Copy [`deploy/truenas-compose.example.yaml`](deploy/truenas-compose.example.yaml) into the TrueNAS editor.
3. Replace **all** placeholder credentials, TrueNAS IP address, and pool path with the values from your own server **in TrueNAS only**. Never save the edited configuration to the public GitHub repository.
4. Ensure that the host directory used for `/data` exists and is writable. For an existing installation, keep the previous `state.json` in the old bot directory so alert state is preserved.
5. Deploy and test `/version`, `/status`, and `/alerts` in Telegram.

**Migration from the old Python image:** Make a backup of the existing TrueNAS application configuration and `state.json`. Stop the old bot before starting the new app: two bot instances using the same Telegram token with long polling can disconnect one another. The new image contains `bot.py` and dependencies; mount the old host directory to `/data`, **not** `/app`. Set `STATE_FILE=/data/state.json`.

### Required environment variables

| Name | Description |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram token provided by BotFather |
| `ALLOWED_CHAT_IDS` | Comma-separated authorized Telegram chat IDs (deny-all if empty) |
| `TRUENAS_URI` | WebSocket endpoint, e.g. `wss://your-truenas-host/api/current` |
| `TRUENAS_USERNAME` | Dedicated TrueNAS API account |
| `TRUENAS_API_KEY` | API key for that account |

### Optional environment variables

| Name | Default | Description |
| --- | --- | --- |
| `TRUENAS_VERIFY_SSL` | `true` | Disable only for a trusted self-signed TLS installation |
| `STATUS_IP` | auto | Override the displayed server IP |
| `ALLOWED_RESTART_APPS` | empty | Comma-separated app allowlist |
| `ALLOW_APP_UPDATES` | `true` | Enable updates from Telegram |
| `UPGRADE_SNAPSHOT_HOSTPATHS` | `true` | Ask TrueNAS to snapshot host paths before app upgrade |
| `ALERT_MIN_LEVEL` | `INFO` | Minimum alert level |
| `ALERT_POLL_SECONDS` | `60` | TrueNAS alert safety poll period; minimum 30 seconds |
| `APP_UPDATE_NOTIFICATIONS` | `true` | Notify Telegram when TrueNAS flags an application update |
| `APP_UPDATE_POLL_SECONDS` | `300` | How often to check `app.query` for updates (seconds; minimum 60) |
| `ALERT_SEND_EXISTING_ON_START` | `false` | Send pre-existing alerts at first startup |
| `STATE_FILE` | `/data/state.json` | Persistent alert notification state |
| `TRUBOT_VERSION` | image-defined | Version reported by `/version` |
| `UPS_NUT_HOST` | TrueNAS hostname | Optional NUT server hostname for live mains/battery mode |
| `UPS_NUT_PORT` | `3493` | NUT server port |

### Permissions

Use a **dedicated, least-privilege TrueNAS account**. Read functions need access to system information, networking, disk/reporting metrics, pool data, apps, and alerts. Restart/update and alert acknowledgement require corresponding write permissions. Do not grant more privileges than necessary.

> [!WARNING]
> TruBot can trigger application updates, restarts, and dismiss TrueNAS alerts. Restrict `ALLOWED_CHAT_IDS` to trusted chat IDs and protect the Telegram bot token and TrueNAS API key.

## Versioning and updates

Current version: **v1.4.0** ([VERSION](VERSION)).

GitHub Actions runs tests, builds and publishes multi-architecture Docker images to **GHCR**:

- Every push to `main` publishes `ghcr.io/szlovakricsi/trubot:latest`, `:edge`, and the version tag in `VERSION` (currently `:v1.4.0`) after successful validation and build.
- A new version also creates a GitHub Release automatically once its Docker build succeeds.
- Pushing a version tag, for example `v1.0.0`, also publishes versioned tags such as `:v1.0.0` and `:1.0.0`.
- TrueNAS can check upstream image changes for custom applications. An available image update appears in TrueNAS Apps; you choose when to install it. Detection depends on TrueNAS polling and a published, accessible GHCR package.

For subsequent releases, update `VERSION` in the same commit as the code changes; the workflow publishes the new versioned image and GitHub release.

**Note:** GitHub Releases and Git tags label versions; pushing a tag does **not** itself update a running TrueNAS container. The GHCR build must succeed, and TrueNAS must pull/redeploy the newer image.

## Local development

```bash
docker build -t trubot:dev .
docker run --rm \
  -e TELEGRAM_BOT_TOKEN="YOUR_TOKEN" \
  -e ALLOWED_CHAT_IDS="YOUR_CHAT_ID" \
  -e TRUENAS_URI="wss://YOUR_SERVER/api/current" \
  -e TRUENAS_USERNAME="YOUR_USER" \
  -e TRUENAS_API_KEY="YOUR_API_KEY" \
  -v "$(pwd)/data:/data" \
  trubot:dev
```

Do not commit shell history or files containing real keys. `/version` returns `dev` for local images unless a build-time version is provided.

## UPS status in `/status`

When the **UPS service is enabled or running**, `/status` adds a section showing:

```text
🔋 UPS: Server UPS
Power: 🟢 Mains
Battery: 94%
Battery voltage: 26.8 V
```

- The TrueNAS API's `service.query` identifies whether UPS monitoring is configured and running; `ups.config` supplies its identifier. `reporting.get_data` supplies the battery percentage and battery voltage where those metrics exist. Unsupported metrics display `N/A`.
- The authoritative power mode comes from NUT `ups.status`: `OL` means **Mains** and `OB` means **Battery**. The bot queries NUT through its read-only TCP protocol, using `UPS_NUT_HOST`/`UPS_NUT_PORT` if configured, or the TrueNAS host from `TRUENAS_URI` by default. If NUT is inaccessible, it displays **Unknown** rather than assuming mains power.
- TrueNAS normally binds NUT locally unless its UPS *Remote Monitor* setting is enabled. A Docker container on a different network may not be able to reach that local-only endpoint. Only enable remote NUT monitoring on a trusted network protected by a firewall; it exposes the NUT service on TCP 3493. Host networking is an alternative if appropriate for your installation.
- The UPS section is hidden when the UPS service is disabled and not running. A configured but stopped UPS service reports **Service stopped**. UPS errors do not break system status.

## Application update notifications

**New in v1.4.0:** TruBot checks TrueNAS `app.query` every **5 minutes** by default. Unlike `alert.list`, `app.query` exposes `image_updates_available` and `upgrade_available` directly. Each newly available application update triggers a Telegram message:

```text
⬆️ App updates available (2)

• sonarr: Docker image update
• radarr: Docker image update
[⬆️ Update]
```

- **Update** opens the normal **One app / Update all** confirmation menu. It never starts an upgrade without confirmation. The button is absent if `ALLOW_APP_UPDATES=false`.
- A new update is announced once per authorized chat; `state.json` remembers which app updates were already announced across restarts. If an app becomes up to date, its notification state clears so a later update is announced again.
- Existing updates found after enabling the feature are notified on the first scan. Successful manual `/updates` lookups do not suppress notifications.
- The native TrueNAS "updates are available" `alert.list` notification is suppressed while proactive app notifications are enabled to prevent duplicate Telegram messages. The TrueNAS alert still exists and may be acknowledged through `/alerts`; synthetic Telegram notices do **not** dismiss TrueNAS alerts.
- **Important:** the 5-minute scan only checks flags already populated by TrueNAS. The TrueNAS image registry check itself normally runs every 24 hours; it can be triggered manually with `midclt -t 600 call app.image.op.check_update`. TruBot does not force expensive registry checks every 5 minutes.
- This feature can be disabled via `APP_UPDATE_NOTIFICATIONS=false`, restoring the older native TrueNAS-alert behavior.

## Compatibility

The image currently uses the upstream `truenas_api_client` **`TS-25.10.3`** release, matching the earlier TrueNAS Telegram installation. Test against your TrueNAS SCALE release before replacing a functioning production bot. The image is built for `linux/amd64` and `linux/arm64` by GitHub Actions.

## License

MIT — see [LICENSE](LICENSE).
