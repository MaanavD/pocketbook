from datetime import date, datetime

import pytest

from pocketbook import alerts, demo, insights, store
from pocketbook.plaid import Plaid, save_sync

TODAY = date(2026, 11, 9)


@pytest.fixture
def conn():
    c = store.memory()
    demo.load(c, TODAY)
    return c


def tx(i, d, amount, merchant, cat="FOOD_AND_DRINK", detailed=None, account="a1", pending=False, pending_id=None):
    return {"transaction_id": i, "account_id": account, "date": d, "amount": amount, "name": merchant,
            "merchant_name": merchant, "pending": pending, "pending_transaction_id": pending_id,
            "personal_finance_category": {"primary": cat, "detailed": detailed}}


ACCTS = [{"account_id": "a1", "name": "Card", "type": "credit", "balances": {"current": 100}},
         {"account_id": "a2", "name": "Checking", "type": "depository", "balances": {"current": 1000}}]


def fresh():
    c = store.memory()
    c.execute("INSERT INTO items(item_id, institution) VALUES('i1', 'Bank')")
    return c


def test_spend_excludes_transfers_income_and_card_payments():
    c = fresh()
    save_sync(c, "i1", ACCTS, [
        tx("1", "2026-11-02", 50, "Cafe"),
        tx("2", "2026-11-02", -20, "Cafe"),  # refund nets out
        tx("3", "2026-11-03", 900, "Visa", "LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", "a2"),
        tx("4", "2026-11-03", 300, "Savings", "TRANSFER_OUT", account="a2"),
        tx("5", "2026-11-04", -2000, "Payroll", "INCOME", account="a2"),
        tx("6", "2026-11-05", 400, "Car loan", "LOAN_PAYMENTS", "LOAN_PAYMENTS_CAR_PAYMENT", "a2"),
    ], [], [], "c1")
    start, end = insights.month_range("2026-11")
    assert insights.total_spend(c, start, end) == 3000 + 40000
    assert insights.total_income(c, start, end) == 200000


def test_sync_modifies_and_removes():
    c = fresh()
    save_sync(c, "i1", ACCTS, [tx("p1", "2026-11-02", 10, "Cafe", pending=True)], [], [], "c1")
    save_sync(c, "i1", [], [tx("t1", "2026-11-03", 12, "Cafe", pending_id="p1")], [], [{"transaction_id": "p1"}], "c2")
    rows = c.execute("SELECT id, amount_cents FROM transactions").fetchall()
    assert [tuple(r) for r in rows] == [("t1", 1200)]
    assert c.execute("SELECT cursor FROM items").fetchone()[0] == "c2"


def test_budget_proposals_use_median_of_three_full_months():
    c = fresh()
    adds = []
    for i, (m, amt) in enumerate([("2026-08", 300), ("2026-09", 410), ("2026-10", 350), ("2026-11", 999)]):
        adds.append(tx(f"g{i}", f"{m}-05", amt, "Grocer"))
    save_sync(c, "i1", ACCTS, adds, [], [], "c")
    props = insights.propose_budgets(c, TODAY)
    assert props == [{"category": "FOOD_AND_DRINK", "label": "Food & drink", "proposed_cents": 35000,
                      "basis": "Median of Aug $300.00, Sep $410.00, Oct $350.00"}]
    assert insights.budget_status(c, TODAY)["budgets"] == []
    insights.approve_budgets(c)
    b = insights.budget_status(c, TODAY)["budgets"][0]
    assert (b["budget_cents"], b["spent_cents"], b["status"]) == (35000, 99900, "over")


def test_approve_subset_and_set(conn):
    insights.propose_budgets(conn, TODAY)
    assert insights.approve_budgets(conn, ["FOOD_AND_DRINK"]) == ["FOOD_AND_DRINK"]
    status = insights.budget_status(conn, TODAY)
    assert [b["category"] for b in status["budgets"]] == ["FOOD_AND_DRINK"]
    insights.set_budget(conn, "TRAVEL", 50000)
    assert {b["category"] for b in insights.budget_status(conn, TODAY)["budgets"]} == {"FOOD_AND_DRINK", "TRAVEL"}


def test_recurring_detects_subscriptions_and_price_change(conn):
    rec = {r["merchant"]: r for r in insights.recurring(conn, TODAY)}
    assert rec["Netflix"]["cadence"] == "monthly"
    assert (rec["Netflix"]["previous_cents"], rec["Netflix"]["last_cents"]) == (1799, 2299)
    assert rec["Mission Street Apartments"]["next_date"] == "2026-12-01"
    assert "Trader Joe's" not in rec  # frequent but irregular amounts and gaps


def test_demo_alerts_fire_once(conn):
    insights.propose_budgets(conn, TODAY)
    insights.approve_budgets(conn)
    found = alerts.detect(conn, TODAY, {})
    kinds = sorted(a["kind"] for a in found)
    assert kinds == ["bill", "bill", "budget", "budget", "duplicate", "large", "subscription", "unusual"]
    assert len(alerts.record(conn, found)) == 8
    assert alerts.record(conn, alerts.detect(conn, TODAY, {})) == []  # same conditions, no repeats
    first = alerts.take_unsent(conn, {"quiet_hours": ""}, datetime(2026, 11, 9, 12))
    assert len(first["alerts"]) == 8
    assert alerts.take_unsent(conn, {"quiet_hours": ""}, datetime(2026, 11, 9, 13))["alerts"] == []


def test_fixed_bills_do_not_trigger_pace_alerts(conn):
    insights.propose_budgets(conn, TODAY)
    insights.approve_budgets(conn)
    titles = [a["title"] for a in alerts.detect(conn, TODAY, {})]
    assert not any("Rent" in t for t in titles)


def test_settings_turn_alert_kinds_off(conn):
    found = alerts.detect(conn, TODAY, {"alert_unusual": False, "alert_bills": False, "large_purchase_dollars": 0})
    assert sorted(a["kind"] for a in found) == ["subscription"]


def test_new_charge_alerts_after_backfill(conn):
    alerts.record(conn, alerts.detect(conn, TODAY, {}))
    demo.charge(conn, "Coinbase", 750, "TRANSFER_OUT", TODAY)  # transfers are not spending
    assert alerts.record(conn, alerts.detect(conn, TODAY, {})) == []
    demo.charge(conn, "Delta Air Lines", 640, "TRAVEL", TODAY)
    new = alerts.record(conn, alerts.detect(conn, TODAY, {}))
    assert [a["title"] for a in new] == ["$640.00 at Delta Air Lines"]


def test_posted_charge_does_not_realert_after_pending():
    c = fresh()
    save_sync(c, "i1", ACCTS, [tx("p1", "2026-11-08", 700, "Store", "GENERAL_MERCHANDISE", pending=True)], [], [], "c")
    assert len(alerts.record(c, alerts.detect(c, TODAY, {}))) == 1
    save_sync(c, "i1", [], [tx("t1", "2026-11-09", 700, "Store", "GENERAL_MERCHANDISE", pending_id="p1")], [],
              [{"transaction_id": "p1"}], "c2")
    assert alerts.record(c, alerts.detect(c, TODAY, {})) == []


def test_quiet_hours_hold_alerts(conn):
    alerts.record(conn, alerts.detect(conn, TODAY, {}))
    held = alerts.take_unsent(conn, {"quiet_hours": "22:00-08:00"}, datetime(2026, 11, 9, 23, 30))
    assert held["alerts"] == [] and held["held"] > 0
    assert alerts.take_unsent(conn, {"quiet_hours": "22:00-08:00"}, datetime(2026, 11, 10, 8, 0))["alerts"]


def test_category_names_resolve():
    assert insights.category_key("Food & drink") == "FOOD_AND_DRINK"
    assert insights.category_key("shopping") == "GENERAL_MERCHANDISE"
    assert insights.category_key("TRAVEL") == "TRAVEL"


class FakePlaid(Plaid):
    def __init__(self, responses):
        vault = {}
        super().__init__("sandbox", "cid", "sec", secrets={
            "get": vault.get, "save": vault.__setitem__, "remove": lambda k: vault.pop(k, None)})
        self.vault, self.responses, self.calls = vault, responses, []

    def call(self, path, body):
        self.calls.append((path, body))
        r = self.responses[path]
        return r.pop(0) if isinstance(r, list) else r


def test_link_exchange_and_paginated_sync():
    c = store.memory()
    p = FakePlaid({
        "/link/token/create": {"link_token": "lt", "hosted_link_url": "https://hosted.plaid.com/x"},
        "/link/token/get": {"link_sessions": [{"results": {"item_add_results": [
            {"public_token": "pub", "institution": {"name": "Chase"}}]}}]},
        "/item/public_token/exchange": {"access_token": "access-1", "item_id": "item-1"},
        "/transactions/sync": [
            {"added": [tx("1", "2026-11-01", 5, "A")], "modified": [], "removed": [], "next_cursor": "c1",
             "has_more": True, "accounts": ACCTS},
            {"added": [tx("2", "2026-11-02", 6, "B")], "modified": [], "removed": [], "next_cursor": "c2",
             "has_more": False, "accounts": ACCTS},
        ],
    })
    assert p.start_link(c)["url"].startswith("https://hosted.plaid.com")
    assert p.finish_link(c) == [{"item_id": "item-1", "institution": "Chase"}]
    env = c.execute("SELECT token_env FROM items").fetchone()[0]
    assert p.vault[env] == "access-1"
    assert "access-1" not in str([tuple(r) for r in c.execute("SELECT * FROM items")])  # token stays out of the DB
    res = p.sync(c)
    assert (res["added"], res["errors"]) == (2, [])
    assert c.execute("SELECT cursor FROM items").fetchone()[0] == "c2"
    assert p.calls[-1][1]["cursor"] == "c1"
