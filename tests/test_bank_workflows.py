from datetime import date
import pytest
from pocketbook import store, insights, wealth
from pocketbook.plaid import PlaidError
from test_hardening import linked, FakePlaid, ACCT, tx

TODAY = date(2026, 11, 10)


def test_legacy_sandbox_token_is_never_sent_to_production():
    c, p = linked({})
    p.env = "production"
    p.vault["TOKEN_i1"] = "access-sandbox-test-secret"
    result = p.sync(c)
    assert p.calls == [], "a sandbox token must not be submitted to the production API"
    assert result["items"] == 0 and not result["errors"]
    assert result["skipped_items"]


def test_pending_link_cannot_be_finished_in_a_different_environment():
    c = store.memory()
    store.set_meta(c, "pending_link_token", "link-sandbox-test-token")
    p = FakePlaid({"/link/token/get": {}})
    p.env = "production"
    with pytest.raises(PlaidError, match="environment"):
        p.finish_link(c)
    assert p.calls == []


def test_bank_toggle_keeps_history_but_excludes_totals():
    from pocketbook import connections
    c, p = linked({})
    from pocketbook.plaid import save_sync
    save_sync(c, "i1", ACCT, [tx("t1", TODAY.isoformat(), 25, "Cafe")], [], [], "c1")
    connections.set_bank(c, "i1", False)
    assert insights.overview(c, TODAY)["spent_cents"] == 0
    assert wealth.networth(c, TODAY)["net_cents"] == 0
    assert c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    connections.set_bank(c, "i1", True)
    assert insights.overview(c, TODAY)["spent_cents"] == 2500


def test_account_preferences_survive_sync_and_are_independent():
    from pocketbook import connections
    from pocketbook.plaid import save_sync
    c, p = linked({})
    save_sync(c, "i1", ACCT, [tx("t1", TODAY.isoformat(), 25, "Cafe")], [], [], "c1")
    connections.set_account(c, "a1", name="My card", include_spending=False, include_networth=True)
    save_sync(c, "i1", ACCT, [], [], [], "c2")
    assert insights.accounts(c)[0]["name"] == "My card"
    assert insights.overview(c, TODAY)["spent_cents"] == 0
    assert wealth.networth(c, TODAY)["net_cents"] == -10000


def test_closed_account_loses_balance_not_transaction_history():
    from pocketbook.plaid import save_sync
    c, _ = linked({})
    save_sync(c, 'i1', ACCT, [tx('t1', TODAY.isoformat(), 25, 'Cafe')], [], [], 'c1')
    save_sync(c, 'i1', [], [], [], [], 'c2', complete_accounts=False)
    assert wealth.networth(c, TODAY)['net_cents'] == -10000
    save_sync(c, 'i1', [], [], [], [], 'c3', complete_accounts=True)
    assert wealth.networth(c, TODAY)['net_cents'] == 0
    assert insights.overview(c, TODAY)['spent_cents'] == 2500
    assert c.execute('SELECT closed FROM accounts').fetchone()[0] == 1


def test_production_selection_excludes_sandbox_everywhere():
    from pocketbook import connections, alerts
    from pocketbook.plaid import save_sync
    c, p = linked({})
    p.vault['TOKEN_i1'] = 'access-sandbox-synthetic'
    save_sync(c, 'i1', ACCT, [tx('t1', TODAY.isoformat(), 1000, 'Cafe')], [], [], 'c1')
    alerts.record(c, [{'key':'large:t1', 'kind':'large', 'severity':'warn', 'title':'Large', 'detail':'Test', 'txn_ids':['t1']}])
    connections.prepare(c, 'production', p.vault.get)
    assert insights.find_transactions(c) == []
    assert insights.monthly_totals(c, TODAY)[-1]['spend_cents'] == 0
    assert wealth.networth(c, TODAY)['net_cents'] == 0
    assert alerts.listing(c) == []
    assert connections.catalog(c, 'production')[0]['environment_match'] is False
    connections.prepare(c, 'sandbox', p.vault.get)
    assert len(insights.find_transactions(c)) == 1
    assert len(alerts.listing(c)) == 1


def test_budget_manual_edit_cannot_be_overwritten_by_old_proposal():
    from pocketbook.plaid import save_sync
    c, _ = linked({})
    save_sync(c, 'i1', ACCT, [tx(str(m), f'2026-{m:02d}-10', 100, 'Cafe') for m in (8,9,10)], [], [], 'c1')
    insights.propose_budgets(c, TODAY)
    insights.set_budget(c, 'FOOD_AND_DRINK', 12345)
    assert insights.approve_budgets(c) == []
    assert c.execute('SELECT amount_cents FROM budgets').fetchone()[0] == 12345


def test_old_budget_proposals_are_invalid_after_bank_toggle():
    from pocketbook import connections
    from pocketbook.plaid import save_sync
    c, _ = linked({})
    save_sync(c, 'i1', ACCT, [tx(str(m), f'2026-{m:02d}-10', 100, 'Cafe') for m in (8,9,10)], [], [], 'c1')
    assert insights.propose_budgets(c, TODAY)
    connections.set_bank(c, 'i1', False)
    assert insights.budget_status(c, TODAY)['proposed'] == []
    with pytest.raises(ValueError, match='Account selection changed'):
        insights.approve_budgets(c)
    assert insights.propose_budgets(c, TODAY) == []


def test_search_treats_percent_and_underscore_literally():
    from pocketbook.plaid import save_sync
    c, _ = linked({})
    save_sync(c, 'i1', ACCT, [tx('1', TODAY.isoformat(), 25, '100%_Cafe'), tx('2', TODAY.isoformat(), 25, 'Elsewhere')], [], [], 'c1')
    assert [t['id'] for t in insights.find_transactions(c, query='%_')] == ['1']
    assert insights.find_transactions(c, account_id='missing') == []


def test_networth_history_does_not_mix_account_selections():
    from pocketbook import connections
    from pocketbook.plaid import save_sync
    c, _ = linked({})
    save_sync(c, 'i1', ACCT, [], [], [], 'c1')
    wealth.snapshot(c, TODAY)
    original = wealth.networth(c, TODAY)['history']
    connections.set_bank(c, 'i1', False)
    assert all(p['net_cents'] == 0 for p in wealth.networth(c, TODAY)['history'])
    connections.set_bank(c, 'i1', True)
    assert wealth.networth(c, TODAY)['history'] == original


def test_expired_signin_never_calls_plaid_and_cancel_preserves_banks():
    c, p = linked({})
    store.set_meta(c, 'pending_link_token', 'link-sandbox-synthetic')
    store.set_meta(c, 'pending_link_expires', '2020-01-01T00:00:00Z')
    with pytest.raises(PlaidError, match='expired'):
        p.finish_link(c)
    assert p.calls == []
    p.cancel_link(c)
    assert p.link_status(c) is None
    assert c.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 1
    assert p.vault['TOKEN_i1'] == 'access-i1'


def test_repair_uses_update_mode_without_creating_duplicate_bank():
    c, p = linked({'/link/token/create': {'link_token':'link-sandbox-repair', 'hosted_link_url':'https://secure.plaid.com/link/synthetic'},
                   '/link/token/get': {'link_sessions':[{'on_success': {'metadata':{}}}]}})
    p.start_link(c, item_id='i1')
    payload = p.calls[0][1]
    assert payload['access_token'] == 'access-i1'
    assert not {'products','optional_products','transactions'} & payload.keys()
    result = p.finish_link(c)
    assert result[0]['repaired'] is True
    assert c.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 1
    assert all(path != '/item/public_token/exchange' for path, _ in p.calls)
    assert p.vault['TOKEN_i1'] == 'access-i1'


def test_repair_and_public_token_exchange_block_wrong_environment():
    c, p = linked({})
    p.env = 'production'
    p.vault['TOKEN_i1'] = 'access-sandbox-synthetic'
    with pytest.raises(PlaidError, match='environment'):
        p.start_link(c, item_id='i1')
    with pytest.raises(PlaidError, match='environment'):
        p.add_item(c, 'public-sandbox-synthetic', 'Test')
    assert p.calls == []


def test_incomplete_account_response_keeps_previous_balances_and_cursor():
    from pocketbook.plaid import save_sync
    c, p = linked({'/transactions/sync': {'accounts': [], 'added': [], 'modified': [], 'removed': [], 'has_more': False, 'next_cursor': 'bad'}, '/accounts/get': {}})
    save_sync(c, 'i1', ACCT, [], [], [], 'good')
    result = p.sync(c, full=True)
    assert result['errors']
    assert c.execute('SELECT cursor FROM items').fetchone()[0] == 'good'
    assert c.execute('SELECT closed FROM accounts').fetchone()[0] == 0
    assert wealth.networth(c, TODAY)['net_cents'] == -10000


@pytest.mark.parametrize('field', ['exit','on_exit'])
@pytest.mark.parametrize('error,code', [(None,'LINK_CANCELLED'),({'error_code':'INVALID_CREDENTIALS'},'LINK_FAILED')])
def test_modern_and_legacy_hosted_link_exits_do_not_wait_forever(field,error,code):
    c = store.memory()
    store.set_meta(c,'pending_link_token','link-sandbox-synthetic')
    p = FakePlaid({'/link/token/get':{'link_sessions':[{'finished_at':'2026-11-10T00:00:00Z',field:{'error':error}}]}})
    with pytest.raises(PlaidError) as exc:
        p.finish_link(c)
    assert exc.value.code == code


def test_chat_bank_controls_and_cron_guard(monkeypatch):
    import json
    from pocketbook import tools
    from pocketbook.plaid import save_sync
    c, p = linked({})
    save_sync(c,'i1',ACCT,[],[],[],'c1')
    monkeypatch.setattr(tools,'connect',lambda:c)
    monkeypatch.setattr(tools.service,'plaid',lambda:p)
    monkeypatch.setattr(tools,'_cron_check',lambda:None)
    result=json.loads(tools.accounts({'action':'set_bank','item_id':'i1','enabled':False}))
    assert result['connections'][0]['enabled'] is False
    assert result['accounts'] == []
    tools.accounts({'action':'set_bank','item_id':'i1','enabled':True})
    tools.accounts({'action':'set_account','account_id':'a1','name':'Personal card','include_networth':False})
    assert c.execute('SELECT custom_name,include_networth FROM accounts').fetchone()[:] == ('Personal card',0)
    monkeypatch.setattr(tools,'_cron_check',lambda:{'job':'synthetic'})
    result=json.loads(tools.accounts({'action':'set_bank','item_id':'i1','enabled':False}))
    assert 'error' in result
    assert c.execute('SELECT enabled FROM items').fetchone()[0] == 1


def test_rule_patterns_match_percent_and_underscore_literally():
    from pocketbook.plaid import save_sync
    c,_=linked({})
    save_sync(c,'i1',ACCT,[tx('1',TODAY.isoformat(),25,'100%_Cafe'),tx('2',TODAY.isoformat(),25,'Elsewhere')],[],[],'c1')
    wealth.add_rule(c,'%_','custom')
    assert [(r['id'],r['category']) for r in insights.find_transactions(c,category='custom')] == [('1','custom')]


def test_reproposing_exposes_new_amounts_for_active_budgets():
    from pocketbook.plaid import save_sync
    c,_=linked({})
    save_sync(c,'i1',ACCT,[tx(str(m),f'2026-{m:02d}-10',100,'Cafe') for m in (8,9,10)],[],[],'c1')
    insights.set_budget(c,'FOOD_AND_DRINK',5000)
    insights.propose_budgets(c,TODAY)
    status=insights.budget_status(c,TODAY)
    assert status['budgets'][0]['budget_cents'] == 5000
    assert status['proposed'][0]['proposed_cents'] == 10000
    insights.approve_budgets(c)
    assert insights.budget_status(c,TODAY)['budgets'][0]['budget_cents'] == 10000
    assert insights.budget_status(c,TODAY)['proposed'] == []


def test_v3_migration_preserves_existing_records():
    import sqlite3
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    for sql in store._SCHEMA[:3]:
        c.executescript(sql)
    c.execute('PRAGMA user_version=3')
    c.execute("INSERT INTO items(item_id,institution) VALUES('i','Old bank')")
    c.execute("INSERT INTO accounts(account_id,item_id,name,type,current_cents) VALUES('a','i','Old checking','depository',12345)")
    c.execute("INSERT INTO networth(date,assets_cents,liabilities_cents) VALUES('2026-01-01',12345,0)")
    c.execute("INSERT INTO budgets(category,amount_cents,status) VALUES('FOOD_AND_DRINK',12345,'active')")
    c.commit()
    store.migrate(c)
    assert c.execute('SELECT amount_cents FROM budgets').fetchone()[0] == 12345
    assert tuple(c.execute('SELECT enabled,environment FROM items').fetchone()) == (1,None)
    assert tuple(c.execute('SELECT include_spending,include_networth,custom_name FROM accounts').fetchone()) == (1,1,None)
    assert tuple(c.execute('SELECT date,assets_cents,scope FROM networth').fetchone()) == ('2026-01-01',12345,'')
    assert wealth.balances(c)['cash_cents'] == 12345


def test_bank_toggle_skips_network_refresh_and_preserves_token():
    from pocketbook import connections
    c,p=linked({})
    connections.set_bank(c,'i1',False)
    result=p.sync(c,full=True)
    assert p.calls == []
    assert not result['errors'] and result['items'] == 0
    assert result['skipped_items'][0]['reason'] == 'turned off'
    assert p.vault['TOKEN_i1'] == 'access-i1'


def test_foreign_currency_is_visible_in_controls_but_not_usd_totals():
    from pocketbook import connections
    from pocketbook.plaid import save_sync
    c,_=linked({})
    acct=[{'account_id':'a1','name':'Euro account','type':'depository','balances':{'current':1000,'iso_currency_code':'EUR'}}]
    save_sync(c,'i1',acct,[tx('1',TODAY.isoformat(),25,'Cafe')],[],[],'c1')
    assert insights.overview(c,TODAY)['spent_cents'] == 0
    assert wealth.networth(c,TODAY)['net_cents'] == 0
    assert connections.catalog(c,'sandbox')[0]['accounts'][0]['currency'] == 'EUR'


def test_excluded_account_pauses_linked_goal_and_holdings():
    from pocketbook import connections
    from pocketbook.plaid import save_sync
    c,_=linked({})
    acct=[{'account_id':'a1','name':'Brokerage','type':'investment','balances':{'current':1000}}]
    save_sync(c,'i1',acct,[],[],[],'c1')
    wealth.add_goal(c,'Future',200000,account_id='a1')
    wealth.save_holdings(c,[{'account_id':'a1','security_id':'s','quantity':1,'institution_value':1000}],[{'security_id':'s','name':'Synthetic ETF','type':'etf'}])
    assert wealth.investments(c)['total_cents'] == 100000
    connections.set_account(c,'a1',include_networth=False)
    assert wealth.investments(c)['total_cents'] == 0
    assert wealth.goals(c,TODAY)[0]['status'] == 'paused'
    assert c.execute('SELECT COUNT(*) FROM holdings').fetchone()[0] == 1


def test_reopened_signin_ignores_an_old_cancelled_session():
    c=store.memory()
    store.set_meta(c,'pending_link_token','link-sandbox-synthetic')
    p=FakePlaid({'/link/token/get':{'link_sessions':[
        {'started_at':'2026-11-10T02:00:00Z','finished_at':None},
        {'started_at':'2026-11-10T01:00:00Z','finished_at':'2026-11-10T01:01:00Z','exit':{'error':None}}]}})
    with pytest.raises(PlaidError) as exc:
        p.finish_link(c)
    assert exc.value.code == 'LINK_PENDING'


@pytest.mark.parametrize('missing', [True,False])
def test_unconfirmed_disconnect_keeps_connection_and_history(missing):
    from pocketbook.plaid import save_sync
    c,p=linked({'/item/remove':PlaidError('INVALID_ACCESS_TOKEN','Wrong client or environment')})
    save_sync(c,'i1',ACCT,[tx('1',TODAY.isoformat(),25,'Cafe')],[],[],'c1')
    if missing:
        p.vault.clear()
    with pytest.raises(PlaidError):
        p.remove_item(c,'i1')
    assert c.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0] == 1
    if not missing:
        assert p.vault['TOKEN_i1'] == 'access-i1'


@pytest.mark.parametrize('cleanup_fails',[False,True])
def test_secret_write_exception_attempts_plaid_cleanup(cleanup_fails):
    c=store.memory()
    p=FakePlaid({'/item/public_token/exchange':{'item_id':'i','access_token':'access-sandbox-new'},
                 '/item/remove':PlaidError('NETWORK','offline') if cleanup_fails else {}})
    def fail(*args):raise OSError('synthetic unwritable secret store')
    p.secrets['save']=fail
    with pytest.raises(PlaidError) as exc:
        p.add_item(c,'public-sandbox-synthetic','Test')
    assert exc.value.code == 'TOKEN_NOT_SAVED'
    assert '/item/remove' == p.calls[-1][0]
    assert ('could not be confirmed' in str(exc.value)) is cleanup_fails
    assert c.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 0


def test_spending_and_networth_selections_have_independent_history_scopes():
    from pocketbook import connections
    from pocketbook.plaid import save_sync
    c,_=linked({})
    save_sync(c,'i1',ACCT,[],[],[],'c1')
    net_scope=connections.scope_key(c)
    spending_scope=connections.scope_key(c,'spending')
    connections.set_account(c,'a1',include_spending=False)
    assert connections.scope_key(c) == net_scope
    assert connections.scope_key(c,'spending') != spending_scope
    connections.set_account(c,'a1',include_spending=True,include_networth=False)
    assert connections.scope_key(c) != net_scope
    assert connections.scope_key(c,'spending') == spending_scope


def test_turning_off_one_bank_leaves_other_banks_working():
    from pocketbook import connections
    from pocketbook.plaid import save_sync
    other=[{'account_id':'a2','name':'Checking','type':'depository','balances':{'current':500}}]
    c,p=linked({'/transactions/sync':{'accounts':other,'added':[],'modified':[],'removed':[],'has_more':False,'next_cursor':'new'},
                '/accounts/get':{'accounts':other}},items=('i1','i2'))
    save_sync(c,'i1',ACCT,[tx('1',TODAY.isoformat(),25,'Cafe')],[],[],'old1')
    t=tx('2',TODAY.isoformat(),50,'Grocer');t['account_id']='a2'
    save_sync(c,'i2',other,[t],[],[],'old2')
    connections.set_bank(c,'i1',False)
    assert insights.overview(c,TODAY)['spent_cents'] == 5000
    assert wealth.networth(c,TODAY)['net_cents'] == 50000
    result=p.sync(c,full=True)
    assert result['items'] == 1 and not result['errors']
    assert {body['access_token'] for _,body in p.calls} == {'access-i2'}
    assert c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0] == 2
    assert c.execute("SELECT cursor FROM items WHERE item_id='i1'").fetchone()[0] == 'old1'


def test_invalid_transaction_date_range_returns_an_error():
    c=store.memory()
    with pytest.raises(ValueError,match='start date'):
        insights.find_transactions(c,start='2026-11-30',end='2026-11-01')
    with pytest.raises(ValueError):
        insights.find_transactions(c,start='2026-13-01')


def test_hidden_alerts_do_not_crowd_out_visible_alerts():
    from pocketbook import connections,alerts
    from pocketbook.plaid import save_sync
    c,_=linked({},items=('i1','i2'))
    other=[{'account_id':'a2','name':'Checking','type':'depository','balances':{'current':500}}]
    save_sync(c,'i1',ACCT,[tx('1',TODAY.isoformat(),25,'Cafe')],[],[],'c1')
    t=tx('2',TODAY.isoformat(),50,'Grocer');t['account_id']='a2'
    save_sync(c,'i2',other,[t],[],[],'c2')
    alerts.record(c,[{'key':'hidden','kind':'large','severity':'warn','title':'Hidden','detail':'Synthetic','txn_ids':['1']},
                     {'key':'shown','kind':'large','severity':'info','title':'Shown','detail':'Synthetic','txn_ids':['2']}])
    connections.set_bank(c,'i1',False)
    assert [a['key'] for a in alerts.listing(c,limit=1)] == ['shown']


def test_second_bank_signin_preserves_the_pending_session_without_network_call():
    c=store.memory()
    store.set_meta(c,'pending_link_token','link-sandbox-original')
    p=FakePlaid({})
    with pytest.raises(PlaidError) as exc:
        p.start_link(c)
    assert exc.value.code == 'LINK_IN_PROGRESS'
    assert p.calls == []
    assert store.get_meta(c,'pending_link_token') == 'link-sandbox-original'


def test_cli_can_clear_a_stuck_signin(monkeypatch,capsys):
    import argparse
    from pocketbook import cli
    c,p=linked({})
    store.set_meta(c,'pending_link_token','link-sandbox-original')
    monkeypatch.setattr(cli,'connect',lambda:c)
    monkeypatch.setattr(cli.service,'plaid',lambda:p)
    parser=argparse.ArgumentParser();cli.setup(parser)
    cli._run(parser.parse_args(['cancel-link']))
    assert store.get_meta(c,'pending_link_token') == ''
    assert c.execute('SELECT COUNT(*) FROM items').fetchone()[0] == 1
    assert 'Close the Plaid browser tab' in capsys.readouterr().out
    assert p.calls == []


@pytest.mark.parametrize("force", [False, True])
def test_new_bank_sync_recalculates_recommendations_without_changing_approved_budget(monkeypatch,force):
    from pocketbook import service
    from pocketbook.plaid import save_sync
    c,p=linked({})
    save_sync(c,'i1',ACCT,[tx(str(m),f'2026-{m:02d}-10',100,'Cafe') for m in (8,9,10)],[],[],'old')
    insights.set_budget(c,'FOOD_AND_DRINK',7500)
    def synced(conn,full=False):
        assert full is force
        conn.execute("INSERT INTO items(item_id,institution,token_env) VALUES('i2','New bank','TOKEN_i2')")
        second=[{'account_id':'a2','name':'New card','type':'credit','balances':{'current':50}}]
        more=[{**tx(f'new{m}',f'2026-{m:02d}-10',200,'Cafe'), 'account_id':'a2'} for m in (8,9,10)]
        save_sync(conn,'i2',second,more,[],[],'new')
        return {'items':2,'errors':[]}
    monkeypatch.setattr(p,'sync',synced)
    monkeypatch.setattr(service,'plaid',lambda *args:p)
    monkeypatch.setattr(service,'settings',lambda:{})
    monkeypatch.setattr(service,'today',lambda:TODAY)
    monkeypatch.setattr(service,'_notify_ui',lambda *args:None)
    result=service.sync(c,force=force)
    status=insights.budget_status(c,TODAY)
    assert status['budgets'][0]['budget_cents'] == 7500
    assert status['proposed'][0]['proposed_cents'] == 30000
    assert result['recalculated']['budget_recommendations'] == 1


def test_recalculate_uses_saved_data_and_respects_excluded_banks(monkeypatch):
    from pocketbook import service, connections
    from pocketbook.plaid import save_sync
    c,_=linked({})
    save_sync(c,'i1',ACCT,[tx(str(m),f'2026-{m:02d}-10',100,'Cafe') for m in (8,9,10)],[],[],'old')
    monkeypatch.setattr(service,'today',lambda:TODAY)
    monkeypatch.setattr(service,'settings',lambda:{})
    monkeypatch.setattr(service,'plaid',lambda *args:pytest.fail('Recalculate must not contact Plaid'))
    assert service.recalculate(c)['budget_recommendations'] == 1
    connections.set_bank(c,'i1',False)
    assert service.recalculate(c)['budget_recommendations'] == 0
    assert insights.budget_status(c,TODAY)['proposed'] == []
    assert c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0] == 3


def test_partial_recalculation_failure_does_not_hide_successful_bank_refresh(monkeypatch):
    from pocketbook import service
    c,p=linked({})
    monkeypatch.setattr(p,'sync',lambda *args,**kwargs:{'items':1,'errors':[]})
    monkeypatch.setattr(service,'plaid',lambda *args:p)
    monkeypatch.setattr(service,'settings',lambda:{})
    monkeypatch.setattr(service,'_notify_ui',lambda *args:None)
    def fail(conn):raise ValueError('Synthetic calculation failure')
    monkeypatch.setattr(service,'recalculate',fail)
    result=service.sync(c,force=True)
    assert result['items'] == 1
    assert 'Synthetic calculation failure' in result['notes'][0]
