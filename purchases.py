"""Item-level purchases (Amazon orders): what was bought, repeat buys, and cost per day of use."""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from statistics import median

from .store import _lock, now_iso

STOP = {"the", "a", "an", "and", "for", "with", "of", "by", "in", "to", "pack", "set", "count", "ct", "oz", "fl",
        "inch", "in", "x", "new", "premium"}
MERCHANT_SQL = ("(LOWER(COALESCE(t.merchant, t.name, '')) LIKE '%amazon%'"
                " OR LOWER(COALESCE(t.name, '')) LIKE '%amzn%')")


def product_key(name: str, asin: str | None = None) -> str:
    """Same product, same key: the ASIN when known, else the first meaningful words of the title."""
    if asin and asin.strip():
        return f"asin:{asin.strip().upper()}"
    words = [w for w in re.findall(r"[a-z]+", name.lower().split(",")[0]) if w not in STOP and len(w) > 1]
    return "name:" + " ".join(words[:5])


def add_orders(conn, orders: list[dict], source: str = "amazon") -> dict:
    """Store orders. Each order: {order_id, date, total?, items: [{name, price?, quantity?, asin?}]}.
    Line amounts are scaled so they sum to the order total (tax and shipping spread by price)."""
    added = 0
    with _lock, conn:
        for o in orders:
            items = [i for i in o.get("items") or [] if (i.get("name") or "").strip()]
            if not items:
                continue
            day = o["date"][:10]
            prices = [round(float(i["price"]) * 100) if i.get("price") not in (None, "") else None for i in items]
            total = round(float(o["total"]) * 100) if o.get("total") not in (None, "") else None
            if all(p is not None for p in prices) and sum(prices) > 0:
                lines = prices if total is None else _scale(prices, total)
            elif total is not None:
                lines = _scale([1] * len(items), total)
            else:
                continue
            oid = str(o.get("order_id") or hashlib.sha1(f"{day}{total}{items[0]['name']}".encode()).hexdigest()[:12])
            for n, (item, c) in enumerate(zip(items, lines)):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO purchases(id, source, order_id, order_date, product, product_key, quantity,"
                    " cents, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (f"{source}:{oid}:{n}", source, oid, day, item["name"].strip(),
                     product_key(item["name"], item.get("asin")), int(item.get("quantity") or 1), c, now_iso()))
                added += cur.rowcount
    matched = match(conn)
    return {"added": added, "matched_orders": matched}


def _scale(weights: list[int], total: int) -> list[int]:
    s = sum(weights)
    out = [total * w // s for w in weights]
    out[-1] += total - sum(out)
    return out


def match(conn) -> int:
    """Tie each unmatched order to the Amazon card charge with the same total, within 10 days after the order."""
    orders = conn.execute(
        "SELECT order_id, MIN(order_date) AS d, SUM(cents) AS c FROM purchases WHERE txn_id IS NULL"
        " GROUP BY order_id ORDER BY d").fetchall()
    taken = {r[0] for r in conn.execute("SELECT DISTINCT txn_id FROM purchases WHERE txn_id IS NOT NULL")}
    n = 0
    with _lock, conn:
        for o in orders:
            d = date.fromisoformat(o["d"])
            rows = conn.execute(
                f"SELECT t.id, t.date FROM transactions t WHERE {MERCHANT_SQL} AND ABS(t.amount_cents - ?) <= 1"
                " AND t.date BETWEEN ? AND ? ORDER BY t.date",
                (o["c"], (d - timedelta(days=2)).isoformat(), (d + timedelta(days=10)).isoformat())).fetchall()
            hit = next((r["id"] for r in rows if r["id"] not in taken), None)
            if hit:
                conn.execute("UPDATE purchases SET txn_id=? WHERE order_id=?", (hit, o["order_id"]))
                taken.add(hit)
                n += 1
    return n


def set_lifespan(conn, key: str, days: int | None, source: str = "you") -> None:
    with _lock, conn:
        conn.execute("INSERT INTO products(key, lifespan_days, lifespan_source) VALUES(?,?,?)"
                     " ON CONFLICT(key) DO UPDATE SET lifespan_days=excluded.lifespan_days,"
                     " lifespan_source=excluded.lifespan_source", (key, days, source if days else None))


def hide(conn, key: str, hidden: bool = True) -> None:
    with _lock, conn:
        conn.execute("INSERT INTO products(key, hidden) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET hidden=excluded.hidden",
                     (key, int(hidden)))


def items_for(conn, txn_ids: list[str]) -> dict[str, list[str]]:
    if not txn_ids:
        return {}
    out: dict[str, list[str]] = {}
    q = ",".join("?" * len(txn_ids))
    for r in conn.execute(f"SELECT txn_id, product FROM purchases WHERE txn_id IN ({q}) ORDER BY id", txn_ids):
        out.setdefault(r[0], []).append(r[1])
    return out


def repeats(conn, today: date) -> list[dict]:
    """Things bought again and again: two or more orders of the same product, at least a week apart on average."""
    groups: dict[str, list] = {}
    for r in conn.execute("SELECT p.* FROM purchases p LEFT JOIN products k ON k.key = p.product_key"
                          " WHERE COALESCE(k.hidden, 0) = 0 ORDER BY p.order_date"):
        groups.setdefault(r["product_key"], []).append(r)
    out = []
    for key, rows in groups.items():
        by_day: dict[str, int] = {}
        for r in rows:
            by_day[r["order_date"]] = by_day.get(r["order_date"], 0) + r["cents"]
        days = sorted(by_day)
        if len(days) < 2:
            continue
        gaps = [(date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(days, days[1:])]
        every = int(median(gaps))
        if every < 7:
            continue
        avg = round(sum(by_day.values()) / len(by_day))
        nxt = date.fromisoformat(days[-1]) + timedelta(days=every)
        out.append({
            "key": key, "name": rows[-1]["product"], "orders": len(days), "every_days": every,
            "typical_cents": avg, "per_day_cents": round(avg / every), "yearly_cents": round(avg * 365 / every),
            "last_date": days[-1], "next_date": nxt.isoformat(),
            "due": nxt <= today + timedelta(days=7),
        })
    out.sort(key=lambda r: -r["yearly_cents"])
    return out


def summary(conn, today: date, limit: int = 60) -> dict:
    rep = {r["key"]: r for r in repeats(conn, today)}
    life = {r["key"]: r for r in conn.execute("SELECT * FROM products")}
    rows = conn.execute("SELECT * FROM purchases ORDER BY order_date DESC, id").fetchall()
    items, daily = [], 0
    for r in rows:
        k = life.get(r["product_key"])
        if k and k["hidden"]:
            continue
        days, src = (k["lifespan_days"], k["lifespan_source"]) if k and k["lifespan_days"] else \
            ((rep[r["product_key"]]["every_days"], "rebuy") if r["product_key"] in rep else (None, None))
        per_day = round(r["cents"] / days) if days else None
        active = bool(days) and date.fromisoformat(r["order_date"]) + timedelta(days=days) >= today
        if active:
            daily += per_day
        items.append({"id": r["id"], "key": r["product_key"], "date": r["order_date"], "name": r["product"],
                      "quantity": r["quantity"], "cents": r["cents"], "order_id": r["order_id"], "txn_id": r["txn_id"],
                      "lifespan_days": days, "lifespan_source": src, "per_day_cents": per_day, "in_use": active})
    year_ago = (today - timedelta(days=365)).isoformat()
    return {
        "repeats": list(rep.values()),
        "items": items[:limit],
        "daily_cents": daily, "yearly_cents": daily * 365,
        "spent_12m_cents": sum(r["cents"] for r in rows if r["order_date"] >= year_ago),
        "orders": len({r["order_id"] for r in rows}),
        "unmatched_orders": len({r["order_id"] for r in rows if not r["txn_id"]}),
        "items_total": len(rows),
    }
