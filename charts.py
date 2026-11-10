"""PNG charts drawn with Pillow (a Hermes core dependency). Messaging gateways attach them inline."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import insights
from .insights import CAT_SQL, FROM_SQL, SPEND_SQL

S = 2  # draw at 2x for sharp text on phones
BG = (250, 250, 248)
INK = (28, 28, 30)
MUTED = (110, 110, 118)
GRID = (226, 226, 222)
BAR = (52, 120, 246)
BAR_SOFT = (167, 196, 250)
OVER = (220, 72, 60)
WARN = (232, 152, 40)
INCOME = (32, 150, 100)


def _font(size, bold=False):
    try:
        return ImageFont.load_default(size=size * S)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _money(c: int, short=False) -> str:
    d = c / 100
    if short and abs(d) >= 1000:
        return f"${d / 1000:,.1f}k"
    return f"${d:,.0f}"


class Canvas:
    def __init__(self, w, h, title, subtitle=""):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w * S, h * S), BG)
        self.d = ImageDraw.Draw(self.img)
        self.text(24, 20, title, 20, INK)
        if subtitle:
            self.text(24, 48, subtitle, 13, MUTED)

    def text(self, x, y, s, size, fill, anchor="la"):
        self.d.text((x * S, y * S), s, font=_font(size), fill=fill, anchor=anchor)

    def rect(self, x0, y0, x1, y1, fill, r=3):
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            return
        self.d.rounded_rectangle((x0 * S, y0 * S, x1 * S, y1 * S), radius=min(r, (x1 - x0) / 2, (y1 - y0) / 2) * S,
                                 fill=fill)

    def line(self, pts, fill, width=1):
        self.d.line([(x * S, y * S) for x, y in pts], fill=fill, width=width * S)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.img.save(path, "PNG", optimize=True)
        return path


def spending(conn, today: date, out: Path, month: str | None = None) -> Path:
    month = month or insights.month_of(today)
    start, end = insights.month_range(month)
    cats = insights.spending_by_category(conn, start, end)[:9]
    budgets = {b["category"]: b for b in insights.budget_status(conn, today, month)["budgets"]}
    total = sum(c["cents"] for c in cats)
    name = f"{calendar.month_name[int(month[5:])]} {month[:4]}"
    partial = month == insights.month_of(today)
    h = 96 + max(1, len(cats)) * 40 + 30
    cv = Canvas(720, h, f"{name} spending", f"{_money(total)} {'so far, through ' + today.strftime('%b %-d') if partial else 'total'}"
                + ("  ·  ticks mark budgets" if budgets else ""))
    left, right = 170, 600
    scale_max = max([c["cents"] for c in cats] + [b["budget_cents"] for b in budgets.values()] + [1])
    y = 92
    for c in cats:
        b = budgets.get(c["category"])
        color = BAR
        if b:
            color = OVER if b["status"] == "over" else WARN if b["status"] == "warn" else BAR
        cv.text(left - 12, y + 9, c["label"], 13, INK, "rm")
        cv.rect(left, y, right, y + 18, (238, 238, 234))
        cv.rect(left, y, left + (right - left) * c["cents"] / scale_max, y + 18, color)
        if b:
            bx = left + (right - left) * b["budget_cents"] / scale_max
            cv.line([(bx, y - 4), (bx, y + 22)], INK, 2)
        label = _money(c["cents"]) + (f" / {_money(b['budget_cents'])}" if b else "")
        cv.text(right + 10, y + 9, label, 12, MUTED if not b or b["status"] == "ok" else color, "lm")
        y += 40
    if not cats:
        cv.text(24, 100, "No spending recorded for this month.", 14, MUTED)
    return cv.save(out)


def trend(conn, today: date, out: Path, months: int = 6) -> Path:
    rows = insights.monthly_totals(conn, today, months)
    avg_rows = [r for r in rows if not r["partial"]]
    avg = sum(r["spend_cents"] for r in avg_rows) // max(1, len(avg_rows))
    cv = Canvas(720, 380, "Monthly spending", f"Average {_money(avg)} a month over {len(avg_rows)} full months  ·  dots show income")
    top, bottom, left, right = 90, 320, 70, 690
    peak = max([r["spend_cents"] for r in rows] + [r["income_cents"] for r in rows] + [1]) * 1.1
    for i in range(5):
        v = peak * i / 4
        yy = bottom - (bottom - top) * i / 4
        cv.line([(left, yy), (right, yy)], GRID)
        cv.text(left - 8, yy, _money(int(v), short=True), 11, MUTED, "rm")
    slot = (right - left) / len(rows)
    pts = []
    for i, r in enumerate(rows):
        cx = left + slot * (i + 0.5)
        hgt = (bottom - top) * r["spend_cents"] / peak
        cv.rect(cx - slot * 0.3, bottom - hgt, cx + slot * 0.3, bottom, BAR_SOFT if r["partial"] else BAR, 4)
        cv.text(cx, bottom - hgt - 8, _money(r["spend_cents"], short=True), 11, INK, "ms")
        m = int(r["month"][5:])
        cv.text(cx, bottom + 16, calendar.month_abbr[m] + (" (so far)" if r["partial"] else ""), 12, MUTED, "mm")
        if r["income_cents"]:
            pts.append((cx, bottom - (bottom - top) * r["income_cents"] / peak))
    for x, yy in pts:
        cv.d.ellipse(((x - 4) * S, (yy - 4) * S, (x + 4) * S, (yy + 4) * S), fill=INCOME)
    return cv.save(out)


def merchant(conn, today: date, out: Path, name: str, months: int = 12) -> Path:
    since = insights.add_months(today.replace(day=1), -(months - 1))
    rows = conn.execute(
        f"SELECT substr(t.date, 1, 7) AS m, SUM(t.amount_cents) AS c, COUNT(*) AS n, MAX(COALESCE(t.merchant, t.name)) AS label"
        f" {FROM_SQL} WHERE {SPEND_SQL} AND COALESCE(t.merchant, t.name) LIKE ? AND t.date >= ? GROUP BY m ORDER BY m",
        (f"%{name}%", since.isoformat()),
    ).fetchall()
    by_month = {r["m"]: r["c"] for r in rows}
    label = rows[-1]["label"] if rows else name
    total = sum(by_month.values())
    keys = [insights.shift_month(insights.month_of(today), -i) for i in range(months - 1, -1, -1)]
    cv = Canvas(720, 340, f"{label}", f"{_money(total)} over {months} months, {sum(r['n'] for r in rows)} charges")
    top, bottom, left, right = 84, 290, 70, 690
    peak = max(list(by_month.values()) + [1]) * 1.15
    slot = (right - left) / len(keys)
    for i in range(4):
        yy = bottom - (bottom - top) * i / 3
        cv.line([(left, yy), (right, yy)], GRID)
        cv.text(left - 8, yy, _money(int(peak * i / 3), short=True), 11, MUTED, "rm")
    for i, k in enumerate(keys):
        cx = left + slot * (i + 0.5)
        v = by_month.get(k, 0)
        hgt = (bottom - top) * v / peak
        cv.rect(cx - slot * 0.32, bottom - hgt, cx + slot * 0.32, bottom, BAR, 3)
        cv.text(cx, bottom + 14, calendar.month_abbr[int(k[5:])], 11, MUTED, "mm")
    return cv.save(out)


def daily(conn, today: date, out: Path) -> Path:
    """Cumulative spend this month against last month and the total budget."""
    month = insights.month_of(today)
    start, end = insights.month_range(month)
    pstart, pend = insights.month_range(insights.shift_month(month, -1))

    def cumulative(s, e, upto):
        rows = dict(conn.execute(
            f"SELECT t.date, SUM(t.amount_cents) {FROM_SQL} WHERE {SPEND_SQL} AND t.date BETWEEN ? AND ? GROUP BY t.date",
            (s.isoformat(), e.isoformat())).fetchall())
        out_, run, d = [], 0, s
        while d <= min(e, upto):
            run += rows.get(d.isoformat(), 0)
            out_.append(run)
            d += timedelta(days=1)
        return out_

    cur = cumulative(start, end, today)
    prev = cumulative(pstart, pend, pend)
    budget = insights.budget_status(conn, today)["total_budget_cents"]
    cv = Canvas(720, 360, "This month vs last", f"{_money(cur[-1] if cur else 0)} spent by {today.strftime('%b %-d')}; "
                f"{_money(prev[min(len(prev), today.day) - 1] if prev else 0)} by the same day last month")
    top, bottom, left, right = 84, 310, 70, 690
    peak = max(prev + cur + [budget, 1]) * 1.08
    days = end.day

    def xy(i, v):
        return left + (right - left) * i / (days - 1), bottom - (bottom - top) * v / peak

    for i in range(5):
        yy = bottom - (bottom - top) * i / 4
        cv.line([(left, yy), (right, yy)], GRID)
        cv.text(left - 8, yy, _money(int(peak * i / 4), short=True), 11, MUTED, "rm")
    if budget:
        y = xy(0, budget)[1]
        cv.line([(left, y), (right, y)], WARN, 1)
        cv.text(right, y - 8, f"Budget {_money(budget)}", 11, WARN, "rs")
    if prev:
        cv.line([xy(i, v) for i, v in enumerate(prev[:days])], (180, 180, 186), 2)
    if len(cur) > 1:
        cv.line([xy(i, v) for i, v in enumerate(cur)], BAR, 3)
    for day in (1, 8, 15, 22, days):
        cv.text(xy(day - 1, 0)[0], bottom + 14, str(day), 11, MUTED, "mm")
    return cv.save(out)


def networth(conn, today: date, out: Path) -> Path:
    from . import wealth

    nw = wealth.networth(conn, today, days=365)
    hist = nw["history"]
    change = nw["change_30d_cents"]
    sub = f"{_money(nw['assets_cents'])} assets, {_money(nw['liabilities_cents'])} owed"
    if change is not None:
        sub += f"  ·  {'+' if change >= 0 else '-'}{_money(abs(change))} in 30 days"
    cv = Canvas(720, 360, f"Net worth {_money(nw['net_cents'])}", sub)
    top, bottom, left, right = 84, 310, 78, 690
    if len(hist) < 2:
        cv.text(24, 110, "Net worth history starts with your first sync. Check back tomorrow.", 14, MUTED)
        return cv.save(out)
    vals = [h["net_cents"] for h in hist]
    lo, hi = min(vals), max(vals)
    pad = max(1, (hi - lo) * 0.15)
    lo, hi = lo - pad, hi + pad

    def xy(i, v):
        return left + (right - left) * i / (len(vals) - 1), bottom - (bottom - top) * (v - lo) / (hi - lo)

    for i in range(5):
        v = lo + (hi - lo) * i / 4
        yy = bottom - (bottom - top) * i / 4
        cv.line([(left, yy), (right, yy)], GRID)
        cv.text(left - 8, yy, _money(int(v), short=True), 11, MUTED, "rm")
    pts = [xy(i, v) for i, v in enumerate(vals)]
    cv.d.polygon([(x * S, y * S) for x, y in pts] + [(right * S, bottom * S), (left * S, bottom * S)],
                 fill=(221, 233, 253))
    cv.line(pts, BAR, 3)
    seen = set()
    for i, h in enumerate(hist):
        m = h["date"][:7]
        if m not in seen and h["date"][8:] <= "07":
            seen.add(m)
            cv.text(xy(i, lo)[0], bottom + 14, calendar.month_abbr[int(m[5:])], 11, MUTED, "mm")
    return cv.save(out)


def allocation(conn, today: date, out: Path) -> Path:
    from . import wealth

    inv = wealth.investments(conn)
    sub = f"{len(inv['holdings'])} holdings"
    if inv["gain_cents"] is not None:
        sub += f"  ·  {'+' if inv['gain_cents'] >= 0 else '-'}{_money(abs(inv['gain_cents']))} ({inv['gain_pct']}%) vs cost"
    rows = inv["allocation"]
    top_holdings = inv["holdings"][:6]
    h = 110 + 34 * len(rows) + 40 + 30 * len(top_holdings) + 20
    cv = Canvas(720, h, f"Investments {_money(inv['total_cents'])}", sub)
    if not rows:
        cv.text(24, 100, "No holdings yet. Connect a brokerage or retirement account.", 14, MUTED)
        return cv.save(out)
    colors = [BAR, (95, 160, 250), INCOME, WARN, (150, 110, 230), (120, 120, 130), OVER]
    x = 24
    for i, a in enumerate(rows):  # one stacked bar for the whole mix
        w = (672) * a["value_cents"] / max(1, inv["total_cents"])
        cv.rect(x, 80, x + w, 102, colors[i % len(colors)], 0)
        x += w
    y = 120
    for i, a in enumerate(rows):
        cv.rect(24, y + 2, 36, y + 14, colors[i % len(colors)], 2)
        cv.text(44, y + 8, a["type"], 13, INK, "lm")
        cv.text(696, y + 8, f"{_money(a['value_cents'])}  ·  {a['pct']}%", 12, MUTED, "rm")
        y += 34
    y += 10
    cv.text(24, y, "Largest holdings", 13, MUTED)
    y += 26
    for hd in top_holdings:
        cv.text(24, y, hd["ticker"] or hd["name"] or "?", 13, INK, "lm")
        cv.text(110, y, (hd["name"] or "")[:44], 12, MUTED, "lm")
        gain = hd["gain_cents"]
        if gain is not None:
            cv.text(696, y, f"{'+' if gain >= 0 else '-'}{_money(abs(gain))}", 12, INCOME if gain >= 0 else OVER, "rm")
        cv.text(610, y, _money(hd["value_cents"] or 0), 12, INK, "rm")
        y += 30
    return cv.save(out)


KINDS = {"spending": spending, "trend": trend, "merchant": merchant, "pace": daily, "networth": networth,
         "investments": allocation}
