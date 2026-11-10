"""HTTP API for the Finance tab (Desktop and web dashboard). Mounted at /api/plugins/pocketbook/."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


def _package():
    """Hermes loads this file on its own, so import the plugin package from the folder above."""
    name = "pocketbook_dashboard_pkg"
    if name not in sys.modules:
        root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(name, root / "__init__.py",
                                                      submodule_search_locations=[str(root)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return name


_PKG = _package()
alerts = importlib.import_module(f"{_PKG}.alerts")
demo = importlib.import_module(f"{_PKG}.demo")
imports = importlib.import_module(f"{_PKG}.imports")
purchases = importlib.import_module(f"{_PKG}.purchases")
insights = importlib.import_module(f"{_PKG}.insights")
service = importlib.import_module(f"{_PKG}.service")
store = importlib.import_module(f"{_PKG}.store")
wealth = importlib.import_module(f"{_PKG}.wealth")
PlaidError = importlib.import_module(f"{_PKG}.plaid").PlaidError

router = APIRouter()


async def _run(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except PlaidError as e:
        raise HTTPException(400, str(e)) from None
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from None


def _cents(v):
    """Dollars from a request body to cents; a bad number is a 400, not a stored wrong amount."""
    try:
        return None if v is None else store.cents(v)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


def _summary():
    conn = store.connect()
    t = store.today()
    s = service.settings()
    p = service.plaid(s)
    cats = {*insights.LABELS, *(r[0] for r in conn.execute(f"SELECT DISTINCT {insights.CAT_SQL} {insights.FROM_SQL}"))}
    month = insights.month_of(t)
    start, end = insights.month_range(month)
    pstart, pend = insights.month_range(insights.shift_month(month, -1))
    return {
        "spending": {"this": insights.spending_by_category(conn, start, t),
                     "last": insights.spending_by_category(conn, pstart, pend),
                     "merchants": insights.spending_by_merchant(conn, start, t, limit=8)},
        "overview": insights.overview(conn, t),
        "budgets": insights.budget_status(conn, t),
        "months": insights.monthly_totals(conn, t, 6),
        "pace": _pace(conn, t),
        "recurring": insights.recurring(conn, t),
        "alerts": alerts.listing(conn, limit=20),
        "accounts": insights.accounts(conn),
        "recent": insights.find_transactions(conn, limit=12),
        "networth": wealth.networth(conn, t, days=365),
        "investments": wealth.investments(conn),
        "goals": wealth.goals(conn, t),
        "rules": wealth.list_rules(conn),
        "purchases": purchases.summary(conn, t),
        "imports": imports.sources(conn),
        "categories": {k: insights.label(k) for k in sorted(cats)},
        "setup": {"plaid_configured": p.configured, "plaid_env": p.env,
                  "linking": bool(store.get_meta(conn, "pending_link_token")),
                  "live_items": service.has_live_items(conn)},
    }


def _pace(conn, t):
    month = insights.month_of(t)
    start, end = insights.month_range(month)
    pstart, pend = insights.month_range(insights.shift_month(month, -1))

    def daily(s, e):
        rows = conn.execute(
            f"SELECT t.date, SUM(t.amount_cents) {insights.FROM_SQL} WHERE {insights.SPEND_SQL}"
            " AND t.date BETWEEN ? AND ? GROUP BY t.date", (s.isoformat(), e.isoformat())).fetchall()
        by_day = {int(r[0][8:]): r[1] for r in rows}
        run, out = 0, []
        for d in range(1, e.day + 1):
            run += by_day.get(d, 0)
            out.append(run)
        return out

    return {"days": end.day, "today": t.day, "current": daily(start, end)[: t.day], "previous": daily(pstart, pend)}


@router.get("/summary")
async def summary():
    return await _run(_summary)


@router.get("/transactions")
async def transactions(query: str = "", category: str = "", start: str = "", end: str = "",
                       ids: str = "", limit: int = 100):
    def go():
        return insights.find_transactions(
            store.connect(), query=query or None, category=category or None, start=start or None,
            end=end or None, ids=[i for i in ids.split(",") if i][:500] or None, limit=max(1, min(limit, 500)))
    return {"transactions": await _run(go)}


class BudgetBody(BaseModel):
    category: str | None = None
    amount: float | None = None
    categories: list[str] | None = None


@router.post("/budgets/propose")
async def propose():
    return {"proposed": await _run(lambda: insights.propose_budgets(store.connect(), store.today()))}


@router.post("/budgets/approve")
async def approve(body: BudgetBody):
    return {"approved": await _run(lambda: insights.approve_budgets(store.connect(), body.categories))}


@router.post("/budgets/set")
async def set_budget(body: BudgetBody):
    if not body.category:
        raise HTTPException(400, "category required")
    await _run(insights.set_budget, store.connect(), body.category, _cents(body.amount) if body.amount else None)
    return {"ok": True}


class KeysBody(BaseModel):
    keys: list[str] | None = None


@router.post("/alerts/dismiss")
async def dismiss(body: KeysBody):
    return {"dismissed": await _run(alerts.dismiss, store.connect(), body.keys)}


@router.post("/sync")
async def sync():
    def go():
        conn = store.connect()
        result = service.sync(conn, force=True)
        alerts.record(conn, alerts.detect(conn, store.today(), service.settings()))
        return result
    return await _run(go)


@router.post("/connect")
async def connect_bank():
    return await _run(lambda: service.plaid().start_link(store.connect()))


@router.post("/connect/finish")
async def finish_bank():
    def go():
        conn = store.connect()
        added = service.plaid().finish_link(conn)
        return {"added": added, "sync": service.sync(conn, force=True)}
    return await _run(go)


@router.post("/test-bank")
async def test_bank():
    def go():
        conn = store.connect()
        added = service.plaid().sandbox_link(conn)
        return {"added": added, "sync": service.sync(conn, force=True)}
    return await _run(go)


class GoalBody(BaseModel):
    action: str = "add"
    id: int | None = None
    name: str | None = None
    target: float | None = None
    target_date: str | None = None
    account_id: str | None = None
    saved: float | None = None


@router.post("/goals")
async def goals(body: GoalBody):
    def go():
        conn = store.connect()
        c = _cents
        if body.action == "add":
            wealth.add_goal(conn, body.name or "Goal", c(body.target) or 0, body.target_date or None,
                            body.account_id or None, c(body.saved) or 0)
        elif body.action == "update":
            wealth.update_goal(conn, body.id, name=body.name, target_cents=c(body.target),
                               target_date=body.target_date, saved_cents=c(body.saved))
        elif body.action == "remove":
            wealth.remove_goal(conn, body.id)
        return {"goals": wealth.goals(conn, store.today())}
    return await _run(go)


class RuleBody(BaseModel):
    action: str = "add"
    id: int | None = None
    match: str | None = None
    category: str | None = None


@router.post("/rules")
async def rules(body: RuleBody):
    def go():
        conn = store.connect()
        if body.action == "add":
            wealth.add_rule(conn, body.match or "", insights.category_key(body.category or ""))
        elif body.action == "remove":
            wealth.remove_rule(conn, body.id)
        return {"rules": wealth.list_rules(conn)}
    return await _run(go)


class RecatBody(BaseModel):
    id: str
    category: str
    all_from_merchant: bool = False


@router.post("/transactions/category")
async def recategorize(body: RecatBody):
    def go():
        conn = store.connect()
        return {"updated": insights.recategorize(conn, body.id, insights.category_key(body.category),
                                                 body.all_from_merchant)}
    return await _run(go)


class DemoBody(BaseModel):
    action: str = "load"


@router.post("/demo")
async def sample_data(body: DemoBody):
    def go():
        conn = store.connect()
        if body.action == "clear":
            demo.clear(conn)
            return {"cleared": True}
        info = demo.load(conn, store.today())
        insights.propose_budgets(conn, store.today())
        return info
    return await _run(go)


class ImportBody(BaseModel):
    filename: str = "import.csv"
    text: str


@router.post("/import")
async def import_csv(body: ImportBody):
    return await _run(lambda: imports.import_text(store.connect(), body.text, body.filename))


class LifespanBody(BaseModel):
    key: str
    days: int | None = None
    hidden: bool | None = None


@router.post("/purchases/lifespan")
async def lifespan(body: LifespanBody):
    def go():
        conn = store.connect()
        if body.hidden is not None:
            purchases.hide(conn, body.key, body.hidden)
        else:
            purchases.set_lifespan(conn, body.key, body.days, "you")
        return {"ok": True}
    return await _run(go)
