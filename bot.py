import asyncio
import html
import ipaddress
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from truenas_api_client import Client

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("truenas-telegram-bot")


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
BOT_VERSION = os.environ.get("TRUBOT_VERSION", "dev").strip()

TRUENAS_URI = os.environ.get(
    "TRUENAS_URI", "wss://127.0.0.1/api/current"
).strip()
TRUENAS_USERNAME = os.environ["TRUENAS_USERNAME"].strip()
TRUENAS_API_KEY = os.environ["TRUENAS_API_KEY"].strip()

VERIFY_SSL = os.environ.get("TRUENAS_VERIFY_SSL", "true").lower() in {
    "1", "true", "yes", "on"
}

ALLOWED_CHAT_IDS = {
    int(x.strip())
    for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",")
    if x.strip()
}

ALLOWED_RESTART_APPS = {
    x.strip().lower()
    for x in os.environ.get("ALLOWED_RESTART_APPS", "").split(",")
    if x.strip()
}

ALLOW_APP_UPDATES = os.environ.get("ALLOW_APP_UPDATES", "true").lower() in {
    "1", "true", "yes", "on"
}
UPGRADE_SNAPSHOT_HOSTPATHS = os.environ.get(
    "UPGRADE_SNAPSHOT_HOSTPATHS", "true"
).lower() in {"1", "true", "yes", "on"}

# INFO is the lowest normal TrueNAS alert level, therefore INFO = all alerts.
ALERT_MIN_LEVEL = os.environ.get("ALERT_MIN_LEVEL", "INFO").upper().strip()
ALERT_POLL_SECONDS = max(30, int(os.environ.get("ALERT_POLL_SECONDS", "60")))
ALERT_SEND_EXISTING_ON_START = os.environ.get(
    "ALERT_SEND_EXISTING_ON_START", "false"
).lower() in {"1", "true", "yes", "on"}

STATE_FILE = Path(os.environ.get("STATE_FILE", "/app/state.json"))

# Optional override. If empty, /status derives the primary address from
# TRUENAS_URI or the active TrueNAS interfaces.
STATUS_IP = os.environ.get("STATUS_IP", "").strip()

ALERT_LEVELS = {
    "INFO": 10,
    "NOTICE": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
    "ALERT": 60,
    "EMERGENCY": 70,
}

ALERT_ICONS = {
    "INFO": "ℹ️",
    "NOTICE": "🟡",
    "WARNING": "⚠️",
    "ERROR": "🔴",
    "CRITICAL": "🚨",
    "ALERT": "🚨",
    "EMERGENCY": "🚨",
}


# ---------------------------------------------------------------------------
# TrueNAS connection
# ---------------------------------------------------------------------------

def open_truenas() -> Client:
    c = Client(
        uri=TRUENAS_URI,
        verify_ssl=VERIFY_SSL,
        call_timeout=300,
    )
    auth = c.call(
        "auth.login_ex",
        {
            "mechanism": "API_KEY_PLAIN",
            "username": TRUENAS_USERNAME,
            "api_key": TRUENAS_API_KEY,
            "login_options": {"user_info": False},
        },
    )
    if not isinstance(auth, dict) or auth.get("response_type") != "SUCCESS":
        c.close()
        raise RuntimeError(f"TrueNAS API authentication failed: {auth}")
    return c


def tn_call(method: str, *params: Any, job: bool = False) -> Any:
    c = open_truenas()
    try:
        return c.call(method, *params, job=job)
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_allowed(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.id in ALLOWED_CHAT_IDS)


async def require_allowed(update: Update) -> bool:
    if is_allowed(update):
        return True
    chat = update.effective_chat
    if chat:
        log.warning("Unauthorized Telegram chat attempted access: %s", chat.id)
        if update.effective_message:
            await update.effective_message.reply_text(
                "Nincs jogosultság ehhez a bothoz."
            )
    return False


def human_bytes(value: int | float | None) -> str:
    if value is None:
        return "?"
    value = float(value)
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    idx = 0
    while abs(value) >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    return f"{value:.1f} {units[idx]}"


def percent(part: int | float | None, total: int | float | None) -> str:
    try:
        if not total:
            return "?"
        return f"{(float(part) / float(total)) * 100:.0f}%"
    except Exception:
        return "?"


def format_uptime(seconds: int | float | None) -> str:
    try:
        seconds = max(0, int(float(seconds or 0)))
    except Exception:
        seconds = 0
    days, rest = divmod(seconds, 86400)
    hours = rest // 3600
    if days:
        return f"{days} nap {hours} óra"
    return f"{hours} óra"


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        x = float(value)
        if x != x:  # NaN
            return None
        return x
    except Exception:
        return None


def average(values: list[float | int | None]) -> float | None:
    vals = [float(x) for x in values if x is not None]
    return sum(vals) / len(vals) if vals else None


def get_primary_ip(interfaces: list[dict]) -> str:
    if STATUS_IP:
        return STATUS_IP

    try:
        parsed = urlparse(TRUENAS_URI)
        host = parsed.hostname
        if host:
            ipaddress.ip_address(host)
            return host
    except Exception:
        pass

    candidates = []
    for iface in interfaces:
        state = iface.get("state") or {}
        if str(state.get("link_state", "")).upper() not in {
            "LINK_STATE_UP", "UP", ""
        }:
            continue
        for alias in state.get("aliases") or []:
            if str(alias.get("type", "")).upper() != "INET":
                continue
            address = str(alias.get("address", ""))
            try:
                ip = ipaddress.ip_address(address)
            except Exception:
                continue
            if ip.is_loopback or ip.is_link_local:
                continue
            candidates.append(address)

    return candidates[0] if candidates else "?"


def disk_temperature_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return safe_float(value)
    if isinstance(value, dict):
        for key in ("temperature", "temp", "current"):
            if key in value:
                return safe_float(value[key])
    return None


def latest_reporting_values(graph: dict) -> dict[str, float]:
    legend = [str(x) for x in graph.get("legend", [])]
    data = graph.get("data") or []

    for point in reversed(data):
        if isinstance(point, dict):
            result = {}
            for k, v in point.items():
                fv = safe_float(v)
                if fv is not None:
                    result[str(k)] = fv
            if result:
                return result

        if isinstance(point, (list, tuple)):
            vals = [safe_float(v) for v in point]
            if any(v is not None for v in vals):
                return {
                    legend[i] if i < len(legend) else str(i): v
                    for i, v in enumerate(vals)
                    if v is not None
                }

    return {}


def reporting_fallback_sync() -> tuple[int | None, int | None, float | None]:
    """Return (memory_total, memory_available, average_cpu_temp)."""
    try:
        result = tn_call(
            "reporting.get_data",
            [{"name": "memory"}, {"name": "cputemp"}],
            {"unit": "HOUR", "page": 1, "aggregate": False},
        )
    except Exception:
        log.exception("reporting.get_data fallback failed")
        return None, None, None

    mem_total = None
    mem_available = None
    cpu_temp = None

    for graph in result or []:
        name = str(graph.get("name", "")).lower()
        vals = latest_reporting_values(graph)

        if name == "memory":
            lowered = {k.lower(): v for k, v in vals.items()}

            # Netdata memory chart commonly exposes used/cached/free/buffers.
            used = sum(
                v for k, v in lowered.items()
                if any(token in k for token in ("used", "cached", "buffers"))
                and "swap" not in k
            )
            free = sum(
                v for k, v in lowered.items()
                if "free" in k and "swap" not in k
            )

            if used or free:
                mem_total = int(used + free)
                mem_available = int(free)

        elif name == "cputemp":
            cpu_temp = average(list(vals.values()))

    return mem_total, mem_available, cpu_temp


def realtime_snapshot_sync(timeout: float = 7.0) -> dict | None:
    """Get one reporting.realtime event.

    TrueNAS exposes physical RAM total/available and per-core CPU temperatures
    through this event source. We subscribe, wait for one sample, then unsubscribe.
    """
    c = open_truenas()
    got = threading.Event()
    result: dict[str, Any] = {}
    payload = c.event_payload()

    def callback(mtype: str, *, fields=None, **message):
        if isinstance(fields, dict):
            result["fields"] = fields
            got.set()

    sub_id = None
    try:
        topic = 'reporting.realtime:{"interval":2}'
        sub_id = c.subscribe(topic, callback, payload)
        if not got.wait(timeout):
            return None
        return result.get("fields")
    finally:
        try:
            if sub_id:
                c.unsubscribe(sub_id)
        except Exception:
            pass
        c.close()


def pool_disk_temperatures_sync(pools: list[dict]) -> dict[str, float | None]:
    try:
        disks = tn_call(
            "disk.query",
            [],
            {"extra": {"pools": True}},
        )
    except Exception:
        log.exception("disk.query failed")
        return {str(p.get("name")): None for p in pools}

    names = [
        str(d.get("name"))
        for d in disks or []
        if d.get("name")
    ]

    temps: dict[str, Any] = {}
    if names:
        try:
            temps = tn_call("disk.temperatures", names, False) or {}
        except Exception:
            log.exception("disk.temperatures failed")

    by_pool: dict[str, list[float]] = {}
    for disk in disks or []:
        pool = disk.get("pool")
        name = disk.get("name")
        if not pool or not name:
            continue
        temp = disk_temperature_value(temps.get(str(name)))
        if temp is not None:
            by_pool.setdefault(str(pool), []).append(temp)

    return {
        str(p.get("name")): average(by_pool.get(str(p.get("name")), []))
        for p in pools
    }


def get_status_data_sync() -> dict:
    c = open_truenas()
    try:
        info = c.call("system.info")
        state = c.call("system.state")
        pools = c.call("pool.query", [], {})
        interfaces = c.call("interface.query", [], {})
    finally:
        c.close()

    try:
        realtime = realtime_snapshot_sync()
    except Exception:
        log.exception("reporting.realtime failed")
        realtime = None

    mem_total = info.get("physmem")
    mem_available = None
    cpu_temp = None

    if realtime:
        memory = realtime.get("memory") or {}
        mem_total = memory.get("physical_memory_total") or mem_total
        mem_available = memory.get("physical_memory_available")

        cpu = realtime.get("cpu") or {}
        core_temps = []
        aggregate_temp = None
        for key, core in cpu.items():
            if not isinstance(core, dict):
                continue
            temp = safe_float(core.get("temp"))
            if temp is None:
                continue
            if str(key).lower() == "cpu":
                aggregate_temp = temp
            else:
                core_temps.append(temp)
        cpu_temp = average(core_temps)
        if cpu_temp is None:
            cpu_temp = aggregate_temp

    if mem_available is None or cpu_temp is None:
        fb_total, fb_available, fb_cpu_temp = reporting_fallback_sync()
        mem_total = mem_total or fb_total
        mem_available = mem_available if mem_available is not None else fb_available
        cpu_temp = cpu_temp if cpu_temp is not None else fb_cpu_temp

    mem_used = None
    if mem_total is not None and mem_available is not None:
        mem_used = max(0, int(mem_total) - int(mem_available))

    pool_temps = pool_disk_temperatures_sync(pools)

    return {
        "info": info,
        "state": state,
        "pools": pools,
        "ip": get_primary_ip(interfaces),
        "mem_total": mem_total,
        "mem_used": mem_used,
        "cpu_temp": cpu_temp,
        "pool_temps": pool_temps,
    }


# ---------------------------------------------------------------------------
# App management
# ---------------------------------------------------------------------------

def restart_app_sync(app_name: str) -> None:
    c = open_truenas()
    try:
        c.call("app.stop", app_name, job=True)
        c.call("app.start", app_name, job=True)
    finally:
        c.close()


def get_updateable_apps_sync() -> list[dict]:
    apps = tn_call("app.query", [], {})
    result = []
    for app in apps:
        if app.get("upgrade_available") or app.get("image_updates_available"):
            result.append(app)
    return sorted(result, key=lambda x: str(x.get("name", "")).lower())


def upgrade_app_sync(app: dict) -> None:
    name = app["name"]
    if app.get("upgrade_available"):
        tn_call(
            "app.upgrade",
            name,
            {
                "app_version": "latest",
                "values": {},
                "snapshot_hostpaths": UPGRADE_SNAPSHOT_HOSTPATHS,
            },
            job=True,
        )
        return

    if app.get("image_updates_available"):
        tn_call(
            "app.pull_images",
            name,
            {"redeploy": True},
            job=True,
        )
        return

    raise RuntimeError(f"Nincs elérhető frissítés: {name}")


def update_label(app: dict) -> str:
    name = str(app.get("name", "?"))
    current = str(app.get("human_version") or app.get("version") or "?")
    latest = str(app.get("latest_version") or "új image")
    if app.get("upgrade_available"):
        return f"{name}: {current} → {latest}"
    return f"{name}: Docker image frissítés"


# ---------------------------------------------------------------------------
# Alert state / notification
# ---------------------------------------------------------------------------

def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        log.exception("Could not read state file")
    return {"alert_fingerprints": [], "alerts_initialized": False}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        log.exception("Could not save state file")


def alert_level_allowed(level: str) -> bool:
    minimum = ALERT_LEVELS.get(ALERT_MIN_LEVEL, ALERT_LEVELS["INFO"])
    current = ALERT_LEVELS.get(str(level).upper(), 0)
    return current >= minimum


def alert_fingerprint(alert: dict) -> str:
    """Stable alert identity.

    TrueNAS updates ``last_occurrence`` when the same alert fires again.  The
    old implementation included that timestamp in the fingerprint, so the bot
    treated the same still-active alert as a brand-new one and sent it again.
    ``uuid`` is the stable identifier exposed by alert.list.
    """
    uuid_value = str(alert.get("uuid") or "").strip()
    if uuid_value:
        return f"uuid:{uuid_value}"

    alert_id = str(alert.get("id") or "").strip()
    if alert_id:
        return f"id:{alert_id}"

    return "fallback:" + "|".join([
        str(alert.get("klass") or ""),
        str(alert.get("key") or ""),
        str(alert.get("level") or ""),
        str(alert.get("text") or ""),
    ])


def alert_was_seen(alert: dict, seen: set[str]) -> bool:
    """Check both the new stable fingerprint and the previous state format.

    This keeps an existing state.json compatible after upgrading the bot, so
    active alerts are not all re-sent once after the code update.
    """
    fp = alert_fingerprint(alert)
    if fp in seen:
        return True

    uuid_value = str(alert.get("uuid") or alert.get("id") or "").strip()
    if uuid_value:
        legacy_prefix = f"{uuid_value}|"
        if any(str(item).startswith(legacy_prefix) for item in seen):
            return True

    return False


def format_alert_text(alert: dict) -> str:
    """Return the fully rendered TrueNAS alert text as plain text."""
    formatted = alert.get("formatted")
    if formatted:
        value = str(formatted)
        value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
        value = re.sub(r"</p\s*>", "\n", value, flags=re.IGNORECASE)
        value = re.sub(r"<[^>]+>", "", value)
        return html.unescape(value).strip()

    text = str(alert.get("text", "")).strip()
    args = alert.get("args")
    if text and isinstance(args, dict):
        try:
            return text % args
        except Exception:
            log.debug("Could not interpolate alert text: %r args=%r", text, args)

    return text


def is_app_update_available_alert(alert: dict) -> bool:
    """Identify only the TrueNAS 'updates are available' informational alert."""
    text = str(alert.get("text") or "").lower().strip()
    return text.startswith("updates are available for ")


def suppress_app_update_alerts(application: Application, seconds: int = 300) -> None:
    """Suppress update-available alerts briefly while/after bot-driven upgrades."""
    application.bot_data["app_update_alert_suppress_until"] = max(
        float(application.bot_data.get("app_update_alert_suppress_until", 0.0)),
        time.monotonic() + seconds,
    )


def app_update_alert_is_suppressed(application: Application, alert: dict) -> bool:
    if not is_app_update_available_alert(alert):
        return False
    until = float(application.bot_data.get("app_update_alert_suppress_until", 0.0))
    return time.monotonic() < until


def alert_button(uuid_value: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Nyugtázás",
            callback_data=f"alert:dismiss:{uuid_value}",
        )
    ]])


async def send_alert(application: Application, alert: dict) -> None:
    if app_update_alert_is_suppressed(application, alert):
        log.info(
            "Suppressing app-update availability alert during/after bot-driven upgrade: %s",
            alert.get("uuid") or alert.get("id"),
        )
        return

    level = str(alert.get("level", "INFO")).upper()
    text = format_alert_text(alert)
    uuid_value = str(alert.get("uuid", ""))

    message = (
        f"{ALERT_ICONS.get(level, '•')} TrueNAS {level}\n\n"
        f"{text}"
    )

    markup = alert_button(uuid_value) if uuid_value else None
    for chat_id in ALLOWED_CHAT_IDS:
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text=message[:4000],
                reply_markup=markup,
            )
        except Exception:
            log.exception("Could not send alert to chat %s", chat_id)


async def refresh_and_send_alerts(application: Application) -> None:
    lock = application.bot_data.setdefault("alert_refresh_lock", asyncio.Lock())
    async with lock:
        try:
            alerts = await asyncio.to_thread(tn_call, "alert.list")
        except Exception:
            log.exception("alert.list failed")
            return

        state = load_state()
        initialized = bool(state.get("alerts_initialized", False))
        seen = set(state.get("alert_fingerprints", []))

        active = [
            a for a in (alerts or [])
            if not a.get("dismissed")
            and alert_level_allowed(str(a.get("level", "INFO")))
        ]

        if not initialized:
            state["alerts_initialized"] = True
            if not ALERT_SEND_EXISTING_ON_START:
                seen.update(alert_fingerprint(a) for a in active)
                state["alert_fingerprints"] = list(seen)[-2000:]
                save_state(state)
                log.info(
                    "Alert notifier initialized with %d existing alerts",
                    len(active),
                )
                return

        for alert in active:
            fp = alert_fingerprint(alert)
            if alert_was_seen(alert, seen):
                # Migrate old timestamp-based fingerprints to the stable form.
                seen.add(fp)
                continue
            await send_alert(application, alert)
            seen.add(fp)

        state["alert_fingerprints"] = list(seen)[-2000:]
        save_state(state)


def alert_subscription_worker(application: Application, loop: asyncio.AbstractEventLoop) -> None:
    """Persistent TrueNAS alert event subscription.

    Every alert collection event immediately triggers a full alert refresh.
    The safety poll in parallel covers reconnect windows.
    """
    while True:
        c = None
        try:
            c = open_truenas()
            payload = c.event_payload()

            def callback(mtype: str, **message):
                try:
                    asyncio.run_coroutine_threadsafe(
                        refresh_and_send_alerts(application),
                        loop,
                    )
                except Exception:
                    log.exception("Could not queue alert refresh")

            c.subscribe("alert.list", callback, payload)
            log.info("Subscribed to TrueNAS alert.list events")

            # event is set if the server ends the subscription
            payload["event"].wait()
            if payload.get("error"):
                log.warning("Alert subscription ended: %s", payload.get("error"))
        except Exception:
            log.exception("Alert subscription connection failed")
        finally:
            try:
                if c:
                    c.close()
            except Exception:
                pass

        time.sleep(5)


async def alert_safety_poll(application: Application) -> None:
    await asyncio.sleep(5)
    await refresh_and_send_alerts(application)
    while True:
        await asyncio.sleep(ALERT_POLL_SECONDS)
        await refresh_and_send_alerts(application)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed(update):
        return
    await update.effective_message.reply_text(
        "TruBot – TrueNAS Telegram bot\n\n"
        "/status - rendszer, RAM, CPU, poolok, lemezhőmérséklet\n"
        "/apps - TrueNAS Apps állapota\n"
        "/alerts - aktív riasztások + nyugtázás\n"
        "/updates - alkalmazásfrissítések\n"
        "/restart <app> - engedélyezett app újraindítása\n"
        "/version - TruBot verzió\n"
        "/help - parancsok"
    )


async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed(update):
        return
    await update.effective_message.reply_text(f"🤖 TruBot {BOT_VERSION}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed(update):
        return

    msg = await update.effective_message.reply_text("Lekérdezés…")
    try:
        data = await asyncio.to_thread(get_status_data_sync)
        info = data["info"]
        state = str(data["state"])
        pools = data["pools"]

        lines = [
            f"🖥️ {info.get('hostname', 'TrueNAS')}",
            f"Állapot: {'🟢' if state == 'READY' else '🟠'} {state}",
            f"IP: {data['ip']}",
            f"Verzió: {info.get('version', '?')}",
            f"Uptime: {format_uptime(info.get('uptime_seconds'))}",
        ]

        mem_total = data.get("mem_total")
        mem_used = data.get("mem_used")
        if mem_total is not None and mem_used is not None:
            lines.append(
                f"RAM: {human_bytes(mem_used)} / {human_bytes(mem_total)} "
                f"({percent(mem_used, mem_total)})"
            )
        else:
            lines.append(f"RAM összesen: {human_bytes(mem_total)} | használt: ?")

        loadavg = info.get("loadavg") or []
        load_text = " / ".join(
            f"{float(x):.2f}" for x in loadavg[:3]
        ) if loadavg else "?"

        cpu_temp = data.get("cpu_temp")
        temp_text = f"{cpu_temp:.1f} °C" if cpu_temp is not None else "n/a"
        lines.append(f"Load (1/5/15p): {load_text} | CPU átlag: {temp_text}")

        lines += ["", "💾 Poolok:"]
        if not pools:
            lines.append("– nincs találat")
        else:
            for pool in pools:
                name = str(pool.get("name", "?"))
                status = str(pool.get("status", "?"))
                icon = "🟢" if status == "ONLINE" else "🔴"
                size = pool.get("size")
                used = pool.get("allocated")
                pool_temp = data["pool_temps"].get(name)
                ptemp = f"{pool_temp:.1f} °C" if pool_temp is not None else "n/a"

                lines.append(f"{icon} {name}: {status}")
                lines.append(
                    f"   Tárhely: {human_bytes(used)} / {human_bytes(size)} "
                    f"({percent(used, size)})"
                )
                lines.append(f"   Lemezek átlaghőm.: {ptemp}")

        await msg.edit_text("\n".join(lines)[:4000])
    except Exception as exc:
        log.exception("status failed")
        await msg.edit_text(f"❌ TrueNAS API hiba:\n{exc}")


async def cmd_apps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed(update):
        return

    msg = await update.effective_message.reply_text("Appok lekérdezése…")
    try:
        apps = await asyncio.to_thread(tn_call, "app.query", [], {})
        apps = sorted(apps, key=lambda a: str(a.get("name", "")).lower())

        lines = ["📦 TrueNAS Apps", ""]
        for app in apps:
            state = str(app.get("state", "?"))
            icon = {
                "RUNNING": "🟢",
                "STOPPED": "⚪",
                "STOPPING": "🟠",
                "DEPLOYING": "🟡",
                "CRASHED": "🔴",
            }.get(state, "❔")
            update_mark = (
                " ⬆️"
                if app.get("upgrade_available")
                or app.get("image_updates_available")
                else ""
            )
            lines.append(f"{icon} {app.get('name', '?')}: {state}{update_mark}")

        await msg.edit_text("\n".join(lines)[:4000])
    except Exception as exc:
        log.exception("apps failed")
        await msg.edit_text(f"❌ TrueNAS API hiba:\n{exc}")


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed(update):
        return

    msg = await update.effective_message.reply_text("Riasztások lekérdezése…")
    try:
        alerts = await asyncio.to_thread(tn_call, "alert.list")
        active = [a for a in alerts if not a.get("dismissed")]

        if not active:
            await msg.edit_text("✅ Nincs aktív TrueNAS riasztás.")
            return

        await msg.edit_text(f"⚠️ Aktív TrueNAS riasztások: {len(active)}")

        for alert in active[:20]:
            level = str(alert.get("level", "INFO")).upper()
            text = format_alert_text(alert)
            uuid_value = str(alert.get("uuid", ""))
            await update.effective_message.reply_text(
                f"{ALERT_ICONS.get(level, '•')} {level}\n\n{text}"[:4000],
                reply_markup=alert_button(uuid_value) if uuid_value else None,
            )

        if len(active) > 20:
            await update.effective_message.reply_text(
                f"…és még {len(active) - 20} aktív riasztás."
            )
    except Exception as exc:
        log.exception("alerts failed")
        await msg.edit_text(f"❌ TrueNAS API hiba:\n{exc}")


async def callback_alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if not is_allowed(update):
        await query.edit_message_text("Nincs jogosultság.")
        return

    data = query.data or ""
    if not data.startswith("alert:dismiss:"):
        return

    uuid_value = data.split(":", 2)[2]
    try:
        await asyncio.to_thread(tn_call, "alert.dismiss", uuid_value)
        original = query.message.text or "TrueNAS riasztás"
        if "✅ Nyugtázva" not in original:
            original += "\n\n✅ Nyugtázva"
        await query.edit_message_text(original[:4000])
    except Exception as exc:
        log.exception("alert dismiss failed")
        await query.answer(f"Nyugtázási hiba: {exc}", show_alert=True)


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed(update):
        return

    if not context.args:
        allowed = ", ".join(sorted(ALLOWED_RESTART_APPS)) or "(nincs beállítva)"
        await update.effective_message.reply_text(
            "Használat: /restart <app>\n"
            f"Engedélyezett appok: {allowed}"
        )
        return

    app = context.args[0].strip().lower()
    if app not in ALLOWED_RESTART_APPS:
        await update.effective_message.reply_text(
            f"❌ A(z) '{app}' app nincs az újraindítható appok listáján."
        )
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Igen, újraindítás", callback_data=f"restart:{app}"),
        InlineKeyboardButton("❌ Mégse", callback_data="restart_cancel"),
    ]])
    await update.effective_message.reply_text(
        f"Biztosan újraindítsam ezt az appot?\n\n{app}",
        reply_markup=keyboard,
    )


async def callback_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if not is_allowed(update):
        await query.edit_message_text("Nincs jogosultság.")
        return

    data = query.data or ""
    if data == "restart_cancel":
        await query.edit_message_text("Újraindítás megszakítva.")
        return

    app = data.split(":", 1)[1].lower()
    if app not in ALLOWED_RESTART_APPS:
        await query.edit_message_text("Ez az app nincs engedélyezve.")
        return

    await query.edit_message_text(f"🔄 {app} újraindítása…")
    try:
        await asyncio.to_thread(restart_app_sync, app)
        await query.edit_message_text(f"✅ {app} újraindítása befejeződött.")
    except Exception as exc:
        log.exception("restart failed")
        await query.edit_message_text(f"❌ Újraindítási hiba:\n{exc}")


def format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    if minutes:
        return f"{minutes}p {seconds}mp"
    return f"{seconds}mp"


def short_error(exc: Exception, limit: int = 180) -> str:
    value = " ".join(str(exc).split()) or exc.__class__.__name__
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value


def render_update_progress(
    apps: list[dict],
    status: list[dict[str, Any]],
    started_at: float,
    finished: bool = False,
) -> str:
    total = len(apps)
    done_count = sum(1 for item in status if item.get("state") in {"ok", "failed"})
    failed_count = sum(1 for item in status if item.get("state") == "failed")
    remaining = total - done_count

    if finished:
        header = "🏁 Frissítés befejezve"
    else:
        header = f"⏳ Alkalmazásfrissítés: {done_count}/{total} kész"

    lines = [header, ""]
    for idx, (app, item) in enumerate(zip(apps, status), start=1):
        name = str(app.get("name", "?"))
        state = item.get("state", "waiting")
        if state == "ok":
            lines.append(
                f"✅ {idx}/{total} {name} – kész ({format_elapsed(item.get('elapsed', 0))})"
            )
        elif state == "failed":
            lines.append(
                f"❌ {idx}/{total} {name} – HIBA ({format_elapsed(item.get('elapsed', 0))})"
            )
            if item.get("error"):
                lines.append(f"   ↳ {item['error']}")
        elif state == "running":
            lines.append(f"🔄 {idx}/{total} {name} – frissítés folyamatban…")
        else:
            lines.append(f"▫️ {idx}/{total} {name} – várakozik")

    lines += [""]
    if finished:
        ok_count = done_count - failed_count
        lines.append(f"✅ Sikeres: {ok_count}   ❌ Hibás: {failed_count}")
        lines.append(f"⏱ Összes idő: {format_elapsed(time.monotonic() - started_at)}")
    else:
        lines.append(f"⏳ Hátralévő: {remaining}")

    result = "\n".join(lines)
    if len(result) > 4000:
        result = result[:3990] + "\n…"
    return result


async def cmd_updates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_allowed(update):
        return

    if not ALLOW_APP_UPDATES:
        await update.effective_message.reply_text(
            "❌ Az appfrissítés le van tiltva."
        )
        return

    msg = await update.effective_message.reply_text("Frissítések keresése…")
    try:
        apps = await asyncio.to_thread(get_updateable_apps_sync)
        context.application.bot_data["update_candidates"] = apps

        if not apps:
            await msg.edit_text("✅ Minden alkalmazás naprakész.")
            return

        lines = [f"⬆️ Elérhető frissítések: {len(apps)}", ""]
        lines += [f"• {update_label(app)}" for app in apps]

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("1️⃣ Egy app", callback_data="updates:one"),
                InlineKeyboardButton("♻️ Összes", callback_data="updates:all"),
            ],
            [InlineKeyboardButton("❌ Mégse", callback_data="updates:cancel")],
        ])
        await msg.edit_text("\n".join(lines)[:3500], reply_markup=keyboard)
    except Exception as exc:
        log.exception("updates failed")
        await msg.edit_text(f"❌ Frissítéslista hiba:\n{exc}")


async def callback_updates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if not is_allowed(update):
        await query.edit_message_text("Nincs jogosultság.")
        return

    if not ALLOW_APP_UPDATES:
        await query.edit_message_text("Az appfrissítés le van tiltva.")
        return

    data = query.data or ""
    apps = context.application.bot_data.get("update_candidates")

    if data == "updates:cancel":
        await query.edit_message_text("Frissítés megszakítva.")
        return

    if not apps:
        try:
            apps = await asyncio.to_thread(get_updateable_apps_sync)
            context.application.bot_data["update_candidates"] = apps
        except Exception as exc:
            await query.edit_message_text(f"❌ Nem sikerült lekérni az appokat:\n{exc}")
            return

    if not apps:
        await query.edit_message_text("✅ Már nincs elérhető frissítés.")
        return

    if data == "updates:one":
        rows = [[
            InlineKeyboardButton(
                f"⬆️ {app.get('name', '?')}",
                callback_data=f"updates:select:{idx}",
            )
        ] for idx, app in enumerate(apps)]
        rows.append([InlineKeyboardButton("❌ Mégse", callback_data="updates:cancel")])
        await query.edit_message_text(
            "Melyik alkalmazást szeretnéd frissíteni?",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("updates:select:"):
        try:
            idx = int(data.rsplit(":", 1)[1])
            app = apps[idx]
        except Exception:
            await query.edit_message_text("❌ Érvénytelen alkalmazás.")
            return

        context.application.bot_data["selected_update_app"] = app
        await query.edit_message_text(
            f"Frissítsem ezt az alkalmazást?\n\n{update_label(app)}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Frissítés", callback_data="updates:confirm_one"),
                InlineKeyboardButton("❌ Mégse", callback_data="updates:cancel"),
            ]]),
        )
        return

    if data == "updates:confirm_one":
        app = context.application.bot_data.get("selected_update_app")
        if not app:
            await query.edit_message_text("❌ Nincs kiválasztott alkalmazás.")
            return

        await query.edit_message_text(f"⏳ Frissítés folyamatban:\n{app.get('name', '?')}")
        suppress_app_update_alerts(context.application, 600)
        try:
            await asyncio.to_thread(upgrade_app_sync, app)
            suppress_app_update_alerts(context.application, 300)
            await query.edit_message_text(
                f"✅ Frissítés befejeződött:\n{app.get('name', '?')}"
            )
        except Exception as exc:
            suppress_app_update_alerts(context.application, 300)
            log.exception("single app update failed")
            await query.edit_message_text(
                f"❌ Frissítési hiba ({app.get('name', '?')}):\n{exc}"
            )
        return

    if data == "updates:all":
        lines = ["Az összes elérhető frissítés telepítése:", ""]
        lines += [f"• {app.get('name', '?')}" for app in apps]
        await query.edit_message_text(
            "\n".join(lines)[:3500],
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"✅ Igen, mind ({len(apps)})",
                    callback_data="updates:confirm_all",
                ),
                InlineKeyboardButton("❌ Mégse", callback_data="updates:cancel"),
            ]]),
        )
        return

    if data == "updates:confirm_all":
        started_at = time.monotonic()
        suppress_app_update_alerts(context.application, 600)
        status: list[dict[str, Any]] = [
            {"state": "waiting"} for _ in apps
        ]

        await query.edit_message_text(
            render_update_progress(apps, status, started_at)
        )

        for idx, app in enumerate(apps):
            name = str(app.get("name", "?"))
            app_started = time.monotonic()
            status[idx] = {"state": "running"}
            await query.edit_message_text(
                render_update_progress(apps, status, started_at)
            )

            try:
                # app.upgrade/app.pull_images are TrueNAS jobs. With job=True
                # this returns only when that application's job has finished.
                await asyncio.to_thread(upgrade_app_sync, app)
                status[idx] = {
                    "state": "ok",
                    "elapsed": time.monotonic() - app_started,
                }
            except Exception as exc:
                log.exception("update failed: %s", name)
                status[idx] = {
                    "state": "failed",
                    "elapsed": time.monotonic() - app_started,
                    "error": short_error(exc),
                }

            # Immediate feedback after every app, including failures, while
            # continuing with the remaining applications.
            suppress_app_update_alerts(context.application, 300)
            await query.edit_message_text(
                render_update_progress(apps, status, started_at)
            )

        # TrueNAS can refresh/recreate the update-availability alert shortly
        # after the last app job finishes. Keep only that alert muted briefly;
        # other TrueNAS alerts still pass through normally.
        suppress_app_update_alerts(context.application, 300)
        await query.edit_message_text(
            render_update_progress(apps, status, started_at, finished=True)
        )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        ("status", "Rendszer, RAM, CPU, poolok és hőmérsékletek"),
        ("apps", "TrueNAS Apps állapota"),
        ("alerts", "Aktív riasztások és nyugtázás"),
        ("updates", "Alkalmazásfrissítések"),
        ("restart", "App újraindítása"),
        ("version", "TruBot verzió"),
        ("help", "Parancsok"),
    ])

    loop = asyncio.get_running_loop()
    threading.Thread(
        target=alert_subscription_worker,
        args=(application, loop),
        daemon=True,
        name="truenas-alert-subscription",
    ).start()

    application.create_task(alert_safety_poll(application))


def main() -> None:
    if not ALLOWED_CHAT_IDS:
        log.warning(
            "ALLOWED_CHAT_IDS is empty. The bot will reject all commands and "
            "cannot send alerts."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("version", cmd_version))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("apps", cmd_apps))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("updates", cmd_updates))
    app.add_handler(CommandHandler("restart", cmd_restart))

    app.add_handler(CallbackQueryHandler(callback_alert, pattern=r"^alert:dismiss:"))
    app.add_handler(CallbackQueryHandler(callback_restart, pattern=r"^restart"))
    app.add_handler(CallbackQueryHandler(callback_updates, pattern=r"^updates:"))

    log.info("Starting Telegram long polling")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
