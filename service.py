"""Shared operations used by agent tools, the slash command, the CLI and the Finance tab."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import alerts, charts, insights
from .plaid import Plaid
from .store import PLUGIN_ID, connect, get_meta, now_iso, today

SETTING_DEFAULTS = {**alerts.DEFAULTS, "plaid_env": "sandbox", "sync_minutes": 30}


def settings() -> dict:
    """Plugin settings from config.yaml (plugins.entries.pocketbook.settings), over defaults."""
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly()
        raw = ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID, {}).get("settings") or {}
    except Exception:
        raw = {}
    return {**SETTING_DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}


def plaid(s: dict | None = None) -> Plaid:
    return Plaid((s or settings()).get("plaid_env", "sandbox"))


def has_live_items(conn) -> bool:
    return bool(conn.execute("SELECT 1 FROM items WHERE item_id != 'demo' LIMIT 1").fetchone())


def sync(conn=None, force: bool = False) -> dict:
    conn = conn or connect()
    s = settings()
    if not has_live_items(conn):
        return {"skipped": "no bank connected"}
    last = get_meta(conn, "last_sync_attempt")
    if not force and last:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        if age < timedelta(minutes=int(s.get("sync_minutes") or 30)):
            return {"skipped": f"synced {int(age.total_seconds() // 60)} min ago"}
    result = plaid(s).sync(conn)
    _notify_ui("synced", result)
    return result


def check(conn=None, now: datetime | None = None) -> dict:
    """Sync if due, detect alert conditions, and hand back only alerts not sent before."""
    conn = conn or connect()
    s = settings()
    synced = sync(conn)
    new = alerts.record(conn, alerts.detect(conn, today(), s))
    if new:
        _notify_ui("alerts", {"new": len(new)})
    out = alerts.take_unsent(conn, s, now or datetime.now())
    out["sync"] = synced
    out["new_since_last_check"] = len(new)
    return out


def chart(kind: str, month: str | None = None, merchant: str | None = None, conn=None) -> Path:
    from plugins.plugin_storage import plugin_data_dir

    conn = conn or connect()
    out = plugin_data_dir(PLUGIN_ID) / "charts" / f"{kind}-{now_iso().replace(':', '')[:15]}.png"
    if kind == "merchant":
        if not merchant:
            raise ValueError("merchant chart needs a merchant name")
        return charts.merchant(conn, today(), out, merchant)
    if kind == "spending":
        return charts.spending(conn, today(), out, month)
    if kind not in charts.KINDS:
        raise ValueError(f"unknown chart {kind!r}; use one of {', '.join(charts.KINDS)}")
    return charts.KINDS[kind](conn, today(), out)


def summary_text(conn=None) -> str:
    """Plain-text snapshot for /money and digests. No model involved."""
    from .store import fmt

    conn = conn or connect()
    o = insights.overview(conn, today())
    if not o["accounts"]:
        return ("No accounts yet. Add your Plaid keys in Settings → Plugins → pocketbook, then run "
                "`hermes pocketbook connect`, or try sample data with `hermes pocketbook demo`.")
    lines = [f"{datetime.strptime(o['month'], '%Y-%m'):%B}: {fmt(o['spent_cents'])} spent "
             f"(same point last month: {fmt(o['spent_same_point_last_month_cents'])})"]
    b = o["budget"]
    if b["total_budget_cents"]:
        lines.append(f"Budgets: {fmt(b['total_spent_cents'])} of {fmt(b['total_budget_cents'])}")
        for x in b["budgets"]:
            if x["status"] != "ok":
                lines.append(f"  {x['label']}: {fmt(x['spent_cents'])} of {fmt(x['budget_cents'])}"
                             f" ({'over' if x['status'] == 'over' else 'on pace to go over'})")
    elif b["proposed"]:
        lines.append(f"{len(b['proposed'])} proposed budgets are waiting for your approval.")
    for bill in o["upcoming_bills"][:4]:
        lines.append(f"Due {bill['next_date']}: {bill['merchant']} ~{fmt(bill['last_cents'])}")
    lines.append(f"Cash {fmt(o['cash_cents'])} · card balances {fmt(o['credit_owed_cents'])} · "
                 f"{o['open_alerts']} open alerts")
    if o["demo"]:
        lines.append("(sample data)")
    return "\n".join(lines)


def _notify_ui(event: str, payload: dict) -> None:
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
        broadcast_plugin_event(PLUGIN_ID, event, payload)
    except Exception:
        pass  # no dashboard listening
