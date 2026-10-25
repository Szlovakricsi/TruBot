# TruBot 🤖

**TruBot** is a Telegram-based monitoring and management bot for **TrueNAS SCALE**, with system health, storage pools, application management, and actionable alerts.

> [!IMPORTANT]
> This repository contains source code and example configuration only. Never commit your real Telegram bot token, TrueNAS API key, chat IDs, or `state.json`.

## Features

- **`/status`** — Host, IP address, uptime, CPU load/temperature, memory, pool capacity/health, and average pool disk temperatures.
- **`/apps`** — Installed TrueNAS application status.
- **`/alerts`** — Active TrueNAS alerts with acknowledgement. Application-update alerts show an **⬆️ Frissítés** action beside **✅ Nyugtázás**.
- **Automatic alert notifications** — Event subscription with polling fallback, deduplication, and per-alert acknowledgement. App-update alerts offer a direct shortcut to the one/all updates menu.
- **`/updates`** — Available application updates, selectable updates, and per-application progress/error messages.
- **`/restart`** — Restart explicitly allowed applications.
- **`/version`** — Display the TruBot image version.

The bot is written in Python and uses the TrueNAS WebSocket API and the Telegram Bot API. No incoming ports or webhook are required.

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
| `ALERT_POLL_SECONDS` | `60` | Safety poll period; minimum 30 seconds |
| `ALERT_SEND_EXISTING_ON_START` | `false` | Send pre-existing alerts at first startup |
| `STATE_FILE` | `/data/state.json` | Persistent alert notification state |
| `TRUBOT_VERSION` | image-defined | Version reported by `/version` |

### Permissions

Use a **dedicated, least-privilege TrueNAS account**. Read functions need access to system information, networking, disk/reporting metrics, pool data, apps, and alerts. Restart/update and alert acknowledgement require corresponding write permissions. Do not grant more privileges than necessary.

> [!WARNING]
> TruBot can trigger application updates, restarts, and dismiss TrueNAS alerts. Restrict `ALLOWED_CHAT_IDS` to trusted chat IDs and protect the Telegram bot token and TrueNAS API key.

## Versioning and updates

Current version: **v1.1.0** ([VERSION](VERSION)).

GitHub Actions runs tests, builds and publishes multi-architecture Docker images to **GHCR**:

- Every push to `main` publishes `ghcr.io/szlovakricsi/trubot:latest`, `:edge`, and the version tag in `VERSION` (currently `:v1.1.0`) after successful validation and build.
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

## Application update notifications (v1.1.0)

If TrueNAS sends an **application update available** alert, both automatically delivered notifications and `/alerts` show **✅ Nyugtázás** and **⬆️ Frissítés** buttons. The update button is hidden when `ALLOW_APP_UPDATES=false`.

- **Nyugtázás** dismisses the alert, without installing updates.
- **Frissítés** opens a separate Telegram message showing the *current* updateable apps from TrueNAS.
- Choose **1️⃣ Egy app** or **♻️ Összes**, then confirm the selection to initiate the existing TrueNAS update flow.
- The original alert stays visible and is not automatically acknowledged or removed when requesting app updates.

## Compatibility

The image currently uses the upstream `truenas_api_client` **`TS-25.10.3`** release, matching the earlier TrueNAS Telegram installation. Test against your TrueNAS SCALE release before replacing a functioning production bot. The image is built for `linux/amd64` and `linux/arm64` by GitHub Actions.

## License

MIT — see [LICENSE](LICENSE).
