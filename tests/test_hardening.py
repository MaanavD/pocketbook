"""Regression tests for defects found in the pre-real-data review. All data here is synthetic."""
import io
import json
import stat
import urllib.error
from datetime import date, datetime

import pytest

from pocketbook import alerts, demo, imports, insights, plaid, purchases, service, store, tools, wealth
from pocketbook.plaid import Plaid, PlaidError, save_sync

TODAY = date(2026, 11, 9)
ACCT = [{"account_id": "a1", "name": "Card", "type": "credit", "balances": {"current": 100}}]


def tx(i, d, amount, merchant):
    return {"transaction_id": i, "account_id": "a1", "date": d, "amount": amount, "name": merchant,
            "merchant_name": merchant, "personal_finance_category": {"primary": "FOOD_AND_DRINK"}}


class FakePlaid(Plaid):
    """Each path maps to a response, a list of responses, or an exception to raise."""

    def __init__(self, responses):
        vault = {}
        super().__init__("sandbox", "cid", "sec", secrets={
            "get": vault.get, "save": vault.__setitem__, "remove": lambda k: vault.pop(k, None)})
        self.vault, self.responses, self.calls = vault, responses, []

    def call(self, path, body):
        self.calls.append((path, body))
        r = self.responses[path]
        r = r.pop(0) if isinstance(r, list) else r
        if isinstance(r, Exception):
            raise r
        return r


def linked(responses, items=("i1",)):
    c = store.memory()
    p = FakePlaid(responses)
    for i in items:
        p.vault[f"TOKEN_{i}"] = f"access-{i}"
        c.execute("INSERT INTO items(item_id, institution, token_env) VALUES(?,?,?)", (i, f"Bank {i}", f"TOKEN_{i}"))
    return c, p


@pytest.fixture
def tool_db(monkeypatch):
    c = store.memory()
    monkeypatch.setattr(tools, "connect", lambda: c)
    monkeypatch.setattr(tools, "_cron_check", lambda: None)
    return c


# ---------- cents ----------

def test_cents_round_instead_of_truncating():
    assert [store.cents(v) for v in (19.99, 0.29, "1234.56", 4.35, -19.99, 1.005)] == [1999, 29, 123456, 435, -1999, 101]
    for bad in (float("nan"), float("inf"), "abc", None, 1e15):
        with pytest.raises(ValueError):
            store.cents(bad)


def test_tool_amounts_keep_their_cents(tool_db):
    tools.budgets({"action": "set", "category": "travel", "amount": 19.99})  # was stored as 1998
    assert tool_db.execute("SELECT amount_cents FROM budgets").fetchone()[0] == 1999
    tools.goals({"action": "add", "name": "Bike", "target": 1234.56, "saved": 0.29})
    assert tuple(tool_db.execute("SELECT target_cents, saved_cents FROM goals").fetchone()) == (123456, 29)


# ---------- storage ----------

def test_data_folder_and_database_are_owner_only(tmp_path):
    for name in ("data.db", "data.db-wal"):
        (tmp_path / name).write_text("")
        (tmp_path / name).chmod(0o644)
    tmp_path.chmod(0o755)
    store.secure(tmp_path)
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
    assert {stat.S_IMODE(f.stat().st_mode) for f in tmp_path.iterdir()} == {0o600}


def test_failed_migration_step_leaves_nothing_behind(monkeypatch):
    c = store.memory()
    monkeypatch.setattr(store, "_SCHEMA", store._SCHEMA + ["CREATE TABLE half (a); CREATE TABLE meta (a);"])
    with pytest.raises(Exception):
        store.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(store._SCHEMA) - 1
    assert not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'half'").fetchone()


# ---------- sample data never touches real data ----------

def test_clearing_samples_keeps_the_users_own_goal():
    c = store.memory()
    demo.load(c, TODAY)
    mine = wealth.add_goal(c, "Emergency fund", 500000, saved_cents=100000)["id"]  # same name as the sample goal
    demo.clear(c)
    assert [r["id"] for r in c.execute("SELECT id FROM goals")] == [mine]
    assert c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_clear_without_samples_changes_nothing():
    c = store.memory()
    insights.set_budget(c, "TRAVEL", 50000)
    wealth.add_goal(c, "Japan trip", 600000, saved_cents=1000)
    demo.clear(c)
    assert c.execute("SELECT COUNT(*) FROM budgets").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 1


def test_samples_and_real_data_refuse_to_mix():
    real = store.memory()
    imports.import_text(real, "Date,Description,Amount\n2026-10-03,Cafe,-5.00\n")
    with pytest.raises(ValueError, match="empty"):
        demo.load(real, TODAY)
    assert real.execute("SELECT COUNT(*) FROM networth").fetchone()[0] == 0  # no invented history written

    sample = store.memory()
    demo.load(sample, TODAY)
    p = FakePlaid({"/link/token/create": {"link_token": "lt", "hosted_link_url": "https://hosted.plaid.com/x"}})
    for mix in (lambda: imports.import_text(sample, "Date,Description,Amount\n2026-10-03,Cafe,-5.00\n"),
                lambda: p.start_link(sample), lambda: p.sandbox_link(sample),
                lambda: p.add_item(sample, "public", "Bank")):
        with pytest.raises(ValueError, match="Sample data"):
            mix()
    assert p.calls == []  # refused before any request to Plaid


# ---------- Plaid ----------

def test_failed_disconnect_keeps_the_access_token():
    c, p = linked({"/item/remove": PlaidError("NETWORK", "timed out")})
    with pytest.raises(PlaidError, match="kept"):
        p.remove_item(c, "i1")
    assert p.vault["TOKEN_i1"] == "access-i1"  # was deleted, leaving a live item nothing could remove
    assert c.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    p.responses["/item/remove"] = PlaidError("ITEM_NOT_FOUND", "gone")
    p.remove_item(c, "i1")
    assert p.vault == {} and c.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_disconnect_works_for_imports_without_a_token():
    c = store.memory()
    imports.import_text(c, "Date,Description,Amount\n2026-10-03,Cafe,-5.00\n")
    p = FakePlaid({})
    p.remove_item(c, "import-bank")
    assert p.calls == [] and c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_link_keeps_banks_that_worked_when_one_exchange_fails():
    c = store.memory()
    p = FakePlaid({
        "/link/token/get": {"link_sessions": [{"results": {"item_add_results": [
            {"public_token": "pub1", "institution": {"name": "Chase"}},
            {"public_token": "pub2", "institution": {"name": "Amex"}}]}}]},
        "/item/public_token/exchange": [{"access_token": "access-1", "item_id": "item-1"},
                                        PlaidError("INVALID_PUBLIC_TOKEN", "expired")],
    })
    store.set_meta(c, "pending_link_token", "lt")
    assert p.finish_link(c) == [{"item_id": "item-1", "institution": "Chase"}]
    assert store.get_meta(c, "pending_link_token") == ""  # a retry would fail on the spent token


def test_unsaved_access_token_is_not_left_open_at_plaid():
    c = store.memory()
    p = FakePlaid({"/item/public_token/exchange": {"access_token": "access-1", "item_id": "item-1"},
                   "/item/remove": {}})
    p.secrets["save"] = lambda k, v: None  # Hermes refused the write
    with pytest.raises(PlaidError, match="TOKEN_NOT_SAVED"):
        p.add_item(c, "pub", "Chase")
    assert p.calls[-1] == ("/item/remove", {"access_token": "access-1"})
    assert c.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_link_url_must_be_plaid_https():
    c = store.memory()
    for url in ("http://hosted.plaid.com/x", "https://plaid.com.evil.example/x", "javascript:alert(1)", None):
        p = FakePlaid({"/link/token/create": {"link_token": "lt", "hosted_link_url": url}})
        with pytest.raises(PlaidError, match="BAD_LINK"):
            p.start_link(c)
    assert store.get_meta(c, "pending_link_token") is None


def test_sync_that_never_settles_saves_nothing():
    mutation = PlaidError("TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION", "changed")
    page = {"added": [tx("1", "2026-11-01", 5, "A")], "modified": [], "removed": [], "next_cursor": "c1",
            "has_more": True, "accounts": ACCT}
    c, p = linked({"/transactions/sync": [dict(page), mutation, dict(page), mutation, dict(page), mutation]})
    res = p.sync(c)
    assert res["items"] == 0 and len(res["errors"]) == 1
    assert c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0  # was saved as a finished sync
    assert c.execute("SELECT cursor, status FROM items").fetchone()[:] == (None, "error")


def test_one_unreadable_response_does_not_stop_other_banks():
    good = {"added": [tx("1", "2026-11-01", 5, "A")], "modified": [], "removed": [], "next_cursor": "c1",
            "has_more": False, "accounts": ACCT}
    c, p = linked({"/transactions/sync": [{"added": [], "has_more": False}, good]}, items=("i1", "i2"))
    res = p.sync(c)
    assert (res["items"], res["added"], len(res["errors"])) == (1, 1, 1)
    assert store.get_meta(c, "last_sync_attempt")
    assert dict(c.execute("SELECT item_id, status FROM items").fetchall()) == {"i1": "error", "i2": "ok"}


def test_reads_retry_but_token_exchange_is_sent_once(monkeypatch):
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise TimeoutError("timed out")  # not a URLError; used to escape as a crash
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(plaid.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(plaid.time, "sleep", lambda s: None)
    p = Plaid("sandbox", "cid", "sec", secrets={"get": lambda k: None})
    assert p.call("/transactions/sync", {}) == {"ok": True} and len(calls) == 3
    calls.clear()
    with pytest.raises(PlaidError, match="NETWORK"):
        p.call("/item/public_token/exchange", {})
    assert len(calls) == 1


def test_plaid_errors_never_carry_the_secret(monkeypatch):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(
            b'{"error_code": "INVALID_API_KEYS", "error_message": "invalid client_id or secret provided"}'))

    monkeypatch.setattr(plaid.urllib.request, "urlopen", urlopen)
    with pytest.raises(PlaidError) as e:
        Plaid("Production ", "client-id-value", "secret-value", secrets={"get": lambda k: None}).call("/accounts/get", {})
    assert e.value.code == "INVALID_API_KEYS"
    assert "secret-value" not in str(e.value) and "client-id-value" not in str(e.value)


# ---------- imports and purchases ----------

def test_import_skips_impossible_dates_and_amounts():
    c = store.memory()
    res = imports.import_text(c, "Date,Description,Amount\n2026-10-03,Cafe,-5.00\n2026-13-45,Bad date,-5.00\n"
                                 "2026-10-04,Bad amount,nan\n2026-10-05,Bad amount,inf\n")
    assert (res["added"], res["skipped_rows"]) == (1, 3)
    alerts.detect(c, date(2026, 10, 6), {})  # a stored 2026-13-45 used to crash every check from here on


def test_import_path_reads_only_csv_files(tmp_path):
    c = store.memory()
    secret = tmp_path / "id_rsa"
    secret.write_text("Date,Description,Amount\n2026-10-03,Cafe,-5.00\n")
    (tmp_path / "pipe.csv").mkdir()
    big = tmp_path / "big.csv"
    big.write_bytes(b"x" * (imports.MAX_BYTES + 1))
    for path, why in ((secret, "Only .csv"), (tmp_path / "pipe.csv", "No file"), (tmp_path / "none.csv", "No file"),
                      (big, "20 MB")):
        with pytest.raises(ValueError, match=why):
            imports.import_path(c, str(path))
    with pytest.raises(ValueError, match="Unknown format"):
        imports.import_text(c, "Date,Description,Amount\n2026-10-03,Cafe,-5.00\n", fmt="../x")
    assert c.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_removing_an_import_clears_its_purchase_links_and_amazon_can_be_removed():
    c = store.memory()
    imports.import_text(c, "Date,Description,Amount\n2026-10-04,AMAZON MKTPL,-17.37\n")
    purchases.add_orders(c, [{"order_id": "111-1", "date": "2026-10-03", "total": 17.37,
                              "items": [{"name": "Towels", "price": 15.99}]}])
    assert c.execute("SELECT txn_id FROM purchases").fetchone()[0]
    imports.remove(c, "import-bank")  # the item id works as well as the source name
    assert c.execute("SELECT txn_id FROM purchases").fetchone()[0] is None
    assert imports.remove(c, "amazon") == 1 and c.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 0


def test_bad_order_date_or_lifespan_is_refused():
    c = store.memory()
    with pytest.raises(ValueError):
        purchases.add_orders(c, [{"order_id": "1", "date": "last Tuesday", "total": 5, "items": [{"name": "Soap"}]}])
    assert c.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 0
    with pytest.raises(ValueError):
        purchases.set_lifespan(c, "name:soap", 10 ** 10)  # overflowed date math in every later summary
    purchases.summary(c, TODAY)


# ---------- goals, alerts, tools ----------

def test_goal_updates_cannot_break_the_overview():
    c = store.memory()
    gid = wealth.add_goal(c, "Trip", 300000, "2027-06-01", saved_cents=1000)["id"]
    for bad in ({"target_cents": 0}, {"target_date": "next summer"}, {"account_id": "nope"}):
        with pytest.raises(ValueError):
            wealth.update_goal(c, gid, **bad)
    wealth.update_goal(c, gid, target_date="")  # clears the date
    assert wealth.goals(c, TODAY)[0]["status"] == "no_date"
    insights.overview(c, TODAY)


def test_mistyped_quiet_hours_do_not_block_alerts():
    c = store.memory()
    alerts.record(c, [{"kind": "large", "key": "large:1", "severity": "info", "title": "t", "detail": "d", "txn_ids": []}])
    assert len(alerts.take_unsent(c, {"quiet_hours": "10pm-8am"}, datetime(2026, 11, 9, 23))["alerts"]) == 1


def test_scheduled_runs_cannot_dismiss_alerts_or_write(tool_db, monkeypatch):
    alerts.record(tool_db, [{"kind": "large", "key": "large:1", "severity": "info", "title": "t", "detail": "d",
                             "txn_ids": []}])
    monkeypatch.setattr(tools, "_cron_check", lambda: object())
    for handler, args in ((tools.alerts_tool, {"action": "dismiss"}), (tools.budgets, {"action": "set", "category": "x", "amount": 1}),
                          (tools.goals, {"action": "remove", "id": 1}), (tools.rules, {"action": "add", "match": "a", "category": "b"}),
                          (tools.categorize, {"transaction_id": "1", "category": "x"}), (tools.import_file, {"path": "/x.csv"}),
                          (tools.accounts, {"action": "disconnect", "item_id": "i"}),
                          (tools.purchases_tool, {"action": "hide", "key": "k"})):
        assert "not allowed in scheduled runs" in json.loads(handler(args))["error"]
    assert tool_db.execute("SELECT dismissed_at FROM alerts").fetchone()[0] is None


def test_negative_limit_is_not_unlimited(tool_db):
    tool_db.execute("INSERT INTO items(item_id, institution) VALUES('i1', 'Bank')")
    save_sync(tool_db, "i1", ACCT, [tx(str(i), "2026-11-01", 5 + i, "Cafe") for i in range(5)], [], [], "c")
    assert json.loads(tools.transactions({"limit": -1}))["count"] == 1  # SQLite treats LIMIT -1 as no limit


def test_chart_kind_is_checked_before_it_names_a_file():
    with pytest.raises(ValueError, match="unknown chart"):
        service.chart("../../../tmp/x")


def test_samples_refuse_standalone_user_settings():
    c = store.memory()
    goal = wealth.add_goal(c, "Emergency fund", 500000, saved_cents=100000)
    before = dict(c.execute("SELECT * FROM goals WHERE id = ?", (goal["id"],)).fetchone())
    with pytest.raises(ValueError, match="empty"):
        demo.load(c, TODAY)
    assert dict(c.execute("SELECT * FROM goals WHERE id = ?", (goal["id"],)).fetchone()) == before
    assert c.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_clear_samples_preserves_user_budget():
    c = store.memory()
    demo.load(c, TODAY)
    insights.set_budget(c, "TRAVEL", 50000)
    demo.clear(c)
    assert c.execute("SELECT amount_cents FROM budgets WHERE category = ?", ("TRAVEL",)).fetchone()[0] == 50000


def test_full_refresh_includes_loans_missing_from_transaction_accounts():
    loan = {"account_id": "loan", "name": "Loan", "type": "loan", "balances": {"current": 2345.67}}
    c, p = linked({
        "/transactions/sync": {"accounts": ACCT, "added": [], "modified": [], "removed": [],
                               "has_more": False, "next_cursor": "latest"},
        "/accounts/get": {"accounts": [*ACCT, loan]},
    })
    result = p.sync(c, full=True)
    assert result["errors"] == []
    assert c.execute("SELECT current_cents FROM accounts WHERE account_id = ?", ("loan",)).fetchone()[0] == 234567
    assert c.execute("SELECT liabilities_cents FROM networth ORDER BY date DESC LIMIT 1").fetchone()[0] == 244567


def test_full_refresh_clears_empty_holdings_only_for_the_refreshed_bank():
    invest = {"account_id": "invest", "name": "Brokerage", "type": "investment", "balances": {"current": 0}}
    c, p = linked({
        "/transactions/sync": {"accounts": ACCT, "added": [], "modified": [], "removed": [],
                               "has_more": False, "next_cursor": "latest"},
        "/accounts/get": {"accounts": [invest]},
        "/investments/holdings/get": {"holdings": [], "securities": []},
    })
    wealth.save_holdings(c, [
        {"account_id": "invest", "security_id": "s1", "quantity": 1, "institution_value": 50},
        {"account_id": "other-bank", "security_id": "s2", "quantity": 1, "institution_value": 75}], [])
    assert p.sync(c, full=True)["errors"] == []
    assert [r[0] for r in c.execute("SELECT account_id FROM holdings")] == ["other-bank"]


def test_manual_refresh_bypasses_interval_and_requests_all_accounts(monkeypatch):
    c, p = linked({
        "/transactions/sync": {"accounts": ACCT, "added": [], "modified": [], "removed": [],
                               "has_more": False, "next_cursor": "latest"},
        "/accounts/get": {"accounts": ACCT},
    })
    store.set_meta(c, "last_sync_attempt", store.now_iso())
    monkeypatch.setattr(service, "settings", lambda: {"sync_minutes": 30})
    monkeypatch.setattr(service, "plaid", lambda settings: p)
    assert "skipped" in service.sync(c)
    assert service.sync(c, force=True)["errors"] == []
    assert any(path == "/accounts/get" for path, body in p.calls)


def test_full_refresh_preserves_holdings_when_response_is_incomplete():
    invest = {"account_id": "invest", "name": "Brokerage", "type": "investment", "balances": {"current": 50}}
    c, p = linked({
        "/transactions/sync": {"accounts": [], "has_more": False, "next_cursor": "latest"},
        "/accounts/get": {"accounts": [invest]},
        "/investments/holdings/get": {},
    })
    wealth.save_holdings(c, [{"account_id": "invest", "security_id": "s1", "quantity": 1, "institution_value": 50}], [])
    result = p.sync(c, full=True)
    assert c.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 1
    assert any("BAD_RESPONSE" in note for note in result["notes"])
