"""Alert rules. Each alert has a stable key, so a condition fires once no matter how often we check."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from statistics import median

from . import insights, wealth
from .insights import CAT_SQL, FROM_SQL, SPEND_SQL, label
from .store import _lock, fmt, now_iso

DEFAULTS = {
    "alert_budget": True,
    "alert_large": True,
    "alert_unusual": True,
    "alert_subscriptions": True,
    "alert_bills": True,
    "alert_goals": True,
    "large_purchase_dollars": 500,
    "quiet_hours": "22:00-08:00",
    "lookback_days": 7,
}

KIND_SETTING = {
    "budget": "alert_budget",
    "large": "alert_large",
    "unusual": "alert_unusual",
    "duplicate": "alert_unusual",
    "new_merchant": "alert_unusual",
    "subscription": "alert_subscriptions",
    "bill": "alert_bills",
    "goal": "alert_goals",
    "connection": None,  # always on: broken data makes every other number wrong
}


def _alert(kind, key, severity, title, detail, txn_ids=()):
    return {"kind": kind, "key": f"{kind}:{key}", "severity": severity, "title": title,
            "detail": detail, "txn_ids": list(txn_ids)}


def _recent_spend(conn, since: date):
    return conn.execute(
        f"SELECT t.*, COALESCE(t.merchant, t.name) AS m, {CAT_SQL} AS cat {FROM_SQL}"
        f" WHERE {SPEND_SQL} AND t.amount_cents > 0 AND t.date >= ? ORDER BY t.date",
        (since.isoformat(),),
    ).fetchall()


def _day(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%b %-d")


def _root(t) -> str:
    """A posted charge replaces its pending twin; key both on the pending id so we alert once."""
    return t["pending_id"] or t["id"]


def detect(conn, today: date, settings: dict) -> list[dict]:
    s = {**DEFAULTS, **{k: v for k, v in settings.items() if v is not None}}
    since = today - timedelta(days=int(s["lookback_days"]))
    large = int(float(s["large_purchase_dollars"]) * 100)
    out: list[dict] = []
    recent = _recent_spend(conn, since)
    oldest = conn.execute("SELECT MIN(date) FROM transactions").fetchone()[0]
    history_days = (today - date.fromisoformat(oldest)).days if oldest else 0
    flagged: set[str] = set()

    for t in recent:
        amt, m = t["amount_cents"], t["m"]
        if large and amt >= large:
            out.append(_alert("large", _root(t), "info", f"{fmt(amt)} at {m}",
                              f"Above your {fmt(large)} large-purchase threshold. {label(t['cat'])}, {_day(t['date'])}.",
                              [t["id"]]))
            flagged.add(t["id"])
            continue
        prior = conn.execute(
            f"SELECT t.amount_cents FROM transactions t WHERE COALESCE(t.merchant, t.name) = ? AND t.id != ?"
            f" AND t.date < ? AND t.amount_cents > 0 AND t.pending = 0 AND t.date >= ?",
            (m, t["id"], t["date"], (today - timedelta(days=365)).isoformat()),
        ).fetchall()
        prior = [p[0] for p in prior if p[0] is not None]
        if len(prior) >= 3:
            med = median(prior)
            if amt >= max(2.5 * med, med + 5000):
                out.append(_alert("unusual", _root(t), "warn", f"{fmt(amt)} at {m} is unusually high",
                                  f"You usually spend about {fmt(int(med))} there ({len(prior)} charges in the past year).",
                                  [t["id"]]))
                flagged.add(t["id"])
        elif not prior and history_days >= 60 and amt >= 15000:
            seen = conn.execute(
                "SELECT 1 FROM transactions WHERE COALESCE(merchant, name) = ? AND id != ? AND date < ? LIMIT 1",
                (m, t["id"], t["date"]),
            ).fetchone()
            if not seen:
                out.append(_alert("new_merchant", _root(t), "warn", f"First charge from {m}: {fmt(amt)}",
                                  f"No earlier charges from this merchant in {history_days} days of history.",
                                  [t["id"]]))
                flagged.add(t["id"])

    # Same merchant, same amount, within a day, and not a pending/posted pair.
    for i, a in enumerate(recent):
        for b in recent[i + 1:]:
            if (a["m"] == b["m"] and a["amount_cents"] == b["amount_cents"] and a["amount_cents"] >= 500
                    and abs((date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days) <= 1
                    and b["pending_id"] != a["id"] and a["pending_id"] != b["id"]
                    and a["account_id"] == b["account_id"]):
                ids = sorted([_root(a), _root(b)])
                out.append(_alert("duplicate", "|".join(ids), "warn",
                                  f"Possible double charge: {fmt(a['amount_cents'])} at {a['m']} twice",
                                  f"Both charged to the same account on {_day(a['date'])}"
                                  f"{'' if a['date'] == b['date'] else ' and ' + _day(b['date'])}.",
                                  [a["id"], b["id"]]))

    for r in insights.recurring(conn, today):
        prev, last = r["previous_cents"], r["last_cents"]
        if last >= prev * 1.05 and last - prev >= 100 and r["last_date"] >= (today - timedelta(days=10)).isoformat():
            yearly = (last - prev) * {"weekly": 52, "biweekly": 26, "monthly": 12, "quarterly": 4}.get(r["cadence"], 1)
            out.append(_alert("subscription", f"{r['merchant']}:{r['last_date']}", "warn",
                              f"{r['merchant']} went up {fmt(last - prev)}",
                              f"Now {fmt(last)} {r['cadence']}, was {fmt(prev)}: about {fmt(yearly)} more a year.",
                              [r["last_id"]]))
        due = date.fromisoformat(r["next_date"])
        if today <= due <= today + timedelta(days=3) and r["typical_cents"] >= 2000:
            when = "today" if due == today else "tomorrow" if due == today + timedelta(days=1) else due.strftime("%a %b %-d")
            out.append(_alert("bill", f"{r['merchant']}:{r['next_date']}", "info",
                              f"{r['merchant']} due {when}: about {fmt(r['last_cents'])}",
                              f"Charged {r['cadence']} {r['count']} times; last on {_day(r['last_date'])}."))

    status = insights.budget_status(conn, today)
    for b in status["budgets"]:
        for level, sev, word in ((100, "warn", "over"), (80, "info", "at 80% of")):
            # 80% only matters when the month's pace says you will go over.
            if b["pct"] >= level and (level == 100 or b["projected_cents"] > b["budget_cents"]):
                left = "" if level == 100 else f" {fmt(b['remaining_cents'])} left with {status['days_in_month'] - status['days_elapsed']} days to go."
                over = f" Over by {fmt(-b['remaining_cents'])}." if level == 100 else ""
                out.append(_alert("budget", f"{b['category']}:{status['month']}:{level}", sev,
                                  f"{b['label']} is {word} budget",
                                  f"{fmt(b['spent_cents'])} of {fmt(b['budget_cents'])} this month.{over}{left}"))
                break

    for g in wealth.goals(conn, today):
        if g["status"] == "done":
            out.append(_alert("goal", f"{g['id']}:done", "info", f"Goal reached: {g['name']}",
                              f"{fmt(g['current_cents'])} of {fmt(g['target_cents'])}."))
        elif g["status"] in ("behind", "overdue"):
            out.append(_alert("goal", f"{g['id']}:behind:{today:%Y-%m}", "info", f"{g['name']} is behind",
                              f"{fmt(g['current_cents'])} of {fmt(g['target_cents'])}; the pace to {date.fromisoformat(g['target_date']):%b %-d, %Y} "
                              f"called for {fmt(g['expected_cents'])} by now. About {fmt(g['needed_per_month_cents'])}"
                              f" a month gets it back on track."))

    for item in conn.execute("SELECT * FROM items WHERE status != 'ok'").fetchall():
        out.append(_alert("connection", f"{item['item_id']}:{item['error']}", "warn",
                          f"{item['institution'] or 'A bank'} needs attention",
                          f"Sync stopped: {item['error']}. Reconnect it from the Finance tab or ask me to relink it."))

    return [a for a in out if (KIND_SETTING[a["kind"]] is None or s.get(KIND_SETTING[a["kind"]]))]


def record(conn, found: list[dict]) -> list[dict]:
    """Store alerts we have not seen before. Returns only the new ones."""
    new = []
    stamp = now_iso()
    with _lock:
        for a in found:
            cur = conn.execute(
                "INSERT OR IGNORE INTO alerts(key, kind, severity, title, detail, txn_ids, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (a["key"], a["kind"], a["severity"], a["title"], a["detail"], json.dumps(a["txn_ids"]), stamp),
            )
            if cur.rowcount:
                new.append(a)
        conn.commit()
    return new


def in_quiet_hours(spec: str, now: datetime) -> bool:
    if not spec or "-" not in spec:
        return False
    start_s, end_s = spec.split("-", 1)
    start = datetime.strptime(start_s.strip(), "%H:%M").time()
    end = datetime.strptime(end_s.strip(), "%H:%M").time()
    t = now.time()
    return start <= t < end if start < end else t >= start or t < end


def take_unsent(conn, settings: dict, now: datetime) -> dict:
    """Alerts waiting to be sent. Marks them sent unless quiet hours hold them back."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM alerts WHERE notified_at IS NULL AND dismissed_at IS NULL ORDER BY created_at, key"
    ).fetchall()]
    quiet = settings.get("quiet_hours", DEFAULTS["quiet_hours"])
    if rows and in_quiet_hours(quiet, now):
        return {"alerts": [], "held": len(rows), "reason": f"quiet hours ({quiet})"}
    with _lock:
        conn.executemany("UPDATE alerts SET notified_at=? WHERE key=?", [(now_iso(), r["key"]) for r in rows])
        conn.commit()
    return {"alerts": [_public(r) for r in rows], "held": 0}


def _public(r: dict) -> dict:
    return {"key": r["key"], "kind": r["kind"], "severity": r["severity"], "title": r["title"],
            "detail": r["detail"], "txn_ids": json.loads(r["txn_ids"] or "[]"),
            "created_at": r["created_at"], "dismissed": bool(r.get("dismissed_at"))}


def listing(conn, include_dismissed=False, limit=50) -> list[dict]:
    sql = "SELECT * FROM alerts"
    if not include_dismissed:
        sql += " WHERE dismissed_at IS NULL"
    sql += " ORDER BY severity = 'warn' DESC, created_at DESC, key LIMIT ?"
    return [_public(dict(r)) for r in conn.execute(sql, (limit,)).fetchall()]


def dismiss(conn, keys: list[str] | None) -> int:
    with _lock:
        if keys:
            n = conn.execute(
                f"UPDATE alerts SET dismissed_at=? WHERE key IN ({','.join('?' * len(keys))})",
                [now_iso(), *keys],
            ).rowcount
        else:
            n = conn.execute("UPDATE alerts SET dismissed_at=? WHERE dismissed_at IS NULL", (now_iso(),)).rowcount
        conn.commit()
    return n
