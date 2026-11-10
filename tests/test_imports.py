from datetime import date

import pytest

from pocketbook import imports, insights, purchases, store

TODAY = date(2026, 11, 10)

MONARCH = """Date,Merchant,Category,Account,Original Statement,Notes,Amount,Tags
2026-10-03,Trader Joe's,Groceries,Chase Sapphire (...1234),TRADER JOE S #123,,-54.20,
2026-10-03,Trader Joe's,Groceries,Chase Sapphire (...1234),TRADER JOE S #123,,-54.20,
2026-10-05,Acme Corp,Paychecks,Checking (...9999),ACME DIRECT DEP,,3150.00,
2026-10-06,Chase,Credit Card Payment,Checking (...9999),PAYMENT TO CHASE,,-800.00,
2026-10-07,Blue Bottle,Coffee Shops,Chase Sapphire (...1234),BLUE BOTTLE,,-6.50,
2026-10-08,Petco,Pets,Chase Sapphire (...1234),PETCO 1234,,-31.00,
"""
MINT = """"Date","Description","Original Description","Amount","Transaction Type","Category","Account Name","Labels","Notes"
"10/03/2026","Safeway","SAFEWAY #1","42.10","debit","Groceries","Visa","",""
"10/04/2026","Payroll","ACME","2000.00","credit","Paycheck","Checking","",""
"""
YNAB = """Account,Flag,Date,Payee,Category Group/Category,Category Group,Category,Memo,Outflow,Inflow,Cleared
Checking,,10/03/2026,Shell,Auto: Gas,Auto,Gas,,$40.00,$0.00,Cleared
Checking,,10/04/2026,Employer,Inflow: Ready to Assign,Inflow,Ready to Assign,,$0.00,"$1,500.00",Cleared
"""
BANK = """Transaction Date,Post Date,Description,Category,Type,Amount,Memo
10/03/2026,10/04/2026,UBER *TRIP,Travel,Sale,-23.45,
10/05/2026,10/05/2026,REFUND AMAZON,Shopping,Return,12.00,
10/06/2026,10/06/2026,NETFLIX.COM,Bills & Utilities,Sale,-15.49,
"""
AMAZON = """Website,Order ID,Order Date,Purchase Order Number,Currency,Unit Price,Unit Price Tax,Shipping Charge,Total Discounts,Total Owed,Shipment Item Subtotal,Shipment Item Subtotal Tax,ASIN,Product Condition,Quantity,Payment Instrument Type,Order Status,Shipment Status,Ship Date,Shipping Option,Shipping Address,Billing Address,Carrier Name & Tracking Number,Product Name,Gift Message,Gift Sender Name,Gift Recipient Contact Details,Item Serial Number
Amazon.com,111-1,2026-08-01T10:00:00Z,,USD,15.99,1.38,0,0,17.37,,,B01,New,1,Visa,Closed,Shipped,,,,,,"Utopia Towels Cotton Washcloths, 24 Pack",,,,
Amazon.com,111-2,2026-10-03T10:00:00Z,,USD,15.99,1.38,0,0,17.37,,,B01,New,1,Visa,Closed,Shipped,,,,,,"Utopia Towels Cotton Washcloths, 24 Pack",,,,
Amazon.com,111-2,2026-10-03T10:00:00Z,,USD,20.00,1.73,0,0,21.73,,,B02,New,1,Visa,Closed,Shipped,,,,,,Kindle Case,,,,
Amazon.com,111-3,2026-10-05T10:00:00Z,,USD,9.99,0.86,0,0,10.85,,,B03,New,1,Visa,Cancelled,Cancelled,,,,,,Cable,,,,
"""


@pytest.fixture
def conn():
    return store.memory()


def spend(conn):
    return insights.total_spend(conn, date(2026, 10, 1), date(2026, 10, 31))


def test_monarch(conn):
    res = imports.import_text(conn, MONARCH, "monarch.csv")
    assert res["format"] == "monarch" and res["added"] == 6 and res["spending_was_negative"]
    # duplicates on one day stay; card payment and paycheck are not spending; unknown "Pets" becomes its own category
    assert spend(conn) == 5420 * 2 + 650 + 3100
    assert insights.total_income(conn, date(2026, 10, 1), date(2026, 10, 31)) == 315000
    cats = {t["merchant"]: t["category"] for t in insights.find_transactions(conn, limit=10)}
    assert cats["Petco"] == "PETS" and cats["Blue Bottle"] == "FOOD_AND_DRINK" and cats["Trader Joe's"] == "GROCERIES"
    accts = {a["name"]: a["type"] for a in insights.accounts(conn)}
    assert accts["Chase Sapphire (...1234)"] == "credit" and accts["Checking (...9999)"] == "depository"
    again = imports.import_text(conn, MONARCH, "monarch.csv")
    assert again["added"] == 0 and again["already_had"] == 6


def test_mint_ynab_bank(conn):
    assert imports.import_text(conn, MINT)["format"] == "mint"
    assert imports.import_text(conn, YNAB)["format"] == "ynab"
    res = imports.import_text(conn, BANK)
    assert res["format"] == "bank" and res["spending_was_negative"]
    # 42.10 Safeway + 40 Shell + 23.45 Uber + 15.49 Netflix - 12 refund
    assert spend(conn) == 4210 + 4000 + 2345 + 1549 - 1200
    assert insights.total_income(conn, date(2026, 10, 1), date(2026, 10, 31)) == 350000


def test_imports_do_not_count_as_plaid(conn):
    from pocketbook import service
    imports.import_text(conn, BANK)
    assert not service.has_live_items(conn)
    assert imports.remove(conn, "bank") == 3 and spend(conn) == 0


def test_amazon_orders_match_and_repeat(conn):
    imports.import_text(conn, MONARCH)
    # card charges for the two orders, a day after ordering
    from pocketbook.plaid import save_sync
    save_sync(conn, "x", [{"account_id": "c", "name": "Card", "type": "credit"}], [
        {"transaction_id": "t1", "account_id": "c", "date": "2026-08-02", "amount": 17.37, "name": "AMAZON MKTPL",
         "merchant_name": "Amazon", "personal_finance_category": {"primary": "GENERAL_MERCHANDISE"}},
        {"transaction_id": "t2", "account_id": "c", "date": "2026-10-04", "amount": 39.10, "name": "AMZN Mktp US",
         "merchant_name": "Amazon", "personal_finance_category": {"primary": "GENERAL_MERCHANDISE"}}], [], [], None)
    res = imports.import_text(conn, AMAZON, "Order History.csv")
    assert res["format"] == "amazon" and res["orders"] == 2 and res["skipped_rows"] == 1 and res["matched_orders"] == 2
    t2 = insights.find_transactions(conn, ids=["t2"])[0]
    assert t2["items"] == ["Utopia Towels Cotton Washcloths, 24 Pack", "Kindle Case"]
    s = purchases.summary(conn, TODAY)
    (rep,) = s["repeats"]
    assert rep["every_days"] == 63 and rep["typical_cents"] == 1737 and rep["yearly_cents"] == round(1737 * 365 / 63)
    case = next(i for i in s["items"] if i["name"] == "Kindle Case")
    assert case["per_day_cents"] is None
    purchases.set_lifespan(conn, case["key"], 365)
    case = next(i for i in purchases.summary(conn, TODAY)["items"] if i["name"] == "Kindle Case")
    assert case["per_day_cents"] == round(2173 / 365) and case["in_use"]


def test_browser_orders_split_total(conn):
    res = purchases.add_orders(conn, [{"order_id": "A", "date": "2026-11-01", "total": 30.00,
                                       "items": [{"name": "Face towels", "price": 10}, {"name": "Cleanser", "price": 15}]}])
    assert res["added"] == 2
    lines = sorted(r[0] for r in conn.execute("SELECT cents FROM purchases"))
    assert lines == [1200, 1800]  # tax spread by price
    purchases.add_orders(conn, [{"order_id": "A", "date": "2026-11-01", "total": 30.00,
                                 "items": [{"name": "Face towels", "price": 10}, {"name": "Cleanser", "price": 15}]}])
    assert conn.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 2


def test_unknown_csv(conn):
    with pytest.raises(ValueError):
        imports.import_text(conn, "foo,bar\n1,2\n")
