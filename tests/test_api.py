"""Finance HTTP contract using an isolated database and a fake Plaid client."""
import importlib.util
from pathlib import Path
import pytest
pytest.importorskip('fastapi')
pytest.importorskip('httpx')
from fastapi import FastAPI
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location('pocketbook_test_api', Path(__file__).parents[1] / 'dashboard/plugin_api.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

@pytest.fixture
def client(monkeypatch):
    c = api.store.memory()
    class Fake:
        env = 'production'
        configured = True
        def link_status(self, conn):
            return {'environment': self.env, 'url': 'https://plaid.com/synthetic'}
        def finish_link(self, conn):
            raise api.PlaidError('LINK_PENDING', 'Finish signing in to your bank.')
        def cancel_link(self, conn):
            return {'status': 'cancelled', 'message': 'Sign-in cancelled. Existing banks are unchanged.'}
    monkeypatch.setattr(api.service, 'connect', lambda: c)
    monkeypatch.setattr(api.service, 'plaid', lambda *args: Fake())
    monkeypatch.setattr(api.service, 'settings', lambda: {})
    app = FastAPI()
    app.include_router(api.router, prefix='/finance')
    with TestClient(app) as http:
        yield http, c
    c.close()


def test_pending_signin_is_a_successful_waiting_response(client):
    http, c = client
    response = http.post('/finance/connect/finish')
    assert response.status_code == 200
    assert response.json() == {'status':'pending', 'code':'LINK_PENDING', 'message':'Finish signing in to your bank.'}


def test_bank_and_account_preferences_roundtrip_over_http(client):
    http, c = client
    c.execute("INSERT INTO items(item_id,institution,environment) VALUES('i','Synthetic bank','production')")
    c.execute("INSERT INTO accounts(account_id,item_id,name,type,current_cents) VALUES('a','i','Checking','depository',12345)")
    c.commit()
    response = http.post('/finance/connections', json={'item_id':'i','enabled':False})
    assert response.status_code == 200
    summary = http.get('/finance/summary').json()
    assert summary['overview']['accounts'] == 0
    assert summary['networth']['net_cents'] == 0
    assert summary['connections'][0]['enabled'] is False
    assert http.post('/finance/connections', json={'item_id':'i','enabled':True}).status_code == 200
    assert http.post('/finance/accounts', json={'account_id':'a','name':'Household','include_networth':False}).status_code == 200
    summary = http.get('/finance/summary').json()
    assert summary['accounts'][0]['name'] == 'Household'
    assert summary['networth']['net_cents'] == 0
    assert http.post('/finance/accounts', json={'account_id':'a','include_networth':True}).status_code == 200
    assert http.get('/finance/summary').json()['networth']['net_cents'] == 12345


def test_missing_bank_is_an_actionable_400(client):
    http, _ = client
    response = http.post('/finance/connections', json={'item_id':'missing','enabled':False})
    assert response.status_code == 400
    assert response.json()['detail'] == 'Bank connection not found. Refresh the account list.'


def test_budget_decimal_roundtrip_and_manual_edit_clears_proposal(client):
    http,c=client
    response=http.post('/finance/budgets/set',json={'category':'GROCERIES','amount':450.25})
    assert response.status_code == 200
    assert http.get('/finance/summary').json()['budgets']['budgets'][0]['budget_cents'] == 45025
    c.execute("UPDATE budgets SET proposed_cents=99999")
    c.commit()
    assert http.post('/finance/budgets/set',json={'category':'GROCERIES','amount':451.29}).status_code == 200
    assert c.execute('SELECT amount_cents,proposed_cents FROM budgets').fetchone()[:] == (45129,None)
    assert http.post('/finance/budgets/set',json={'category':'GROCERIES','amount':None}).status_code == 200
    assert http.get('/finance/summary').json()['budgets']['budgets'] == []


def test_connected_response_keeps_partial_connection_and_sync_failures_visible(client,monkeypatch):
    http,_=client
    class Partial:
        link_warnings=['Another bank did not finish connecting.']
        def finish_link(self,conn):return [{'institution':'Synthetic bank','item_id':'i'}]
    monkeypatch.setattr(api.service,'plaid',lambda:Partial())
    monkeypatch.setattr(api.service,'sync',lambda *args,**kwargs:{'items':0,'errors':['Balance refresh unavailable']})
    response=http.post('/finance/connect/finish')
    assert response.status_code == 200
    assert response.json()['status'] == 'connected'
    assert response.json()['warnings'] == ['Another bank did not finish connecting.']
    assert response.json()['sync']['errors'] == ['Balance refresh unavailable']
