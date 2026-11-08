"""Isolated date/atomicity contract tests; no database or app lifespan is started."""
import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, Mock
os.environ['DATABASE_URL'] = 'postgresql://unused:unused@invalid/unused'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import main, db, queries
from fastapi import HTTPException

class Request:
    def __init__(self, body): self.body = body
    async def json(self): return self.body

class DateTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 8)
    def test_omitted_current_past(self):
        for body, expected in [({}, self.today), ({'txn_date':'2026-11-08'},self.today), ({'txn_date':'2026-11-07'},date(2026,11,7)), ({'txn_date':'2024-02-29'},date(2024,2,29))]:
            self.assertEqual(main._transaction_date(body,self.today),expected)
    def test_invalid_future(self):
        for value in [None, '', '2026-2-01', '2026-02-30', '2026-11-09', '2026-11-08T00:00:00Z', ' 2026-11-08', 20261108, True, '0000-01-01']:
            with self.subTest(value=value), self.assertRaises(HTTPException): main._transaction_date({'txn_date':value},self.today)
    def invoke(self, extra, fail_second=False):
        records=[]; committed=[]
        @contextmanager
        def atomic():
            try: yield
            except Exception:
                records.clear(); raise
            else: committed.extend(records)
        def insert(**kw):
            records.append(kw)
            if fail_second and len(records)==2: raise RuntimeError('second leg failure')
        body=dict(account_id='a', category='Food', amount='10', currency='SGD', note='issuer reference')
        body.update(extra)
        with patch.object(main,'datetime') as clock, patch.object(db,'transaction',atomic), patch.object(queries,'account_map',return_value={'a':{'currency':'SGD'},'b':{'currency':'SGD'}}), patch.object(queries,'category_names',return_value=['Food']), patch.object(queries,'insert_transaction',side_effect=insert):
            clock.now.return_value=datetime(2026,11,8,0,1,tzinfo=main.SGT)
            result=asyncio.run(main.api_txn(Request(body)))
            clock.now.assert_called_once_with(main.SGT)
        return result,committed
    def test_api_dates_and_balance_contract(self):
        for extra, expected in [({},'2026-11-08'),({'txn_date':'2026-11-08'},'2026-11-08'),({'txn_date':'2026-11-07'},'2026-11-07')]:
            result,rows=self.invoke(extra)
            self.assertEqual(result['txn_date'],expected)
            self.assertEqual(rows[0]['txn_date'].isoformat(),expected)
            self.assertEqual(rows[0]['source'],'manual')
            self.assertEqual(rows[0]['note'],'issuer reference')
    def test_transfer_both_dates(self):
        result,rows=self.invoke(dict(txn_date='2026-11-07',flow='transfer',to_account_id='b'))
        self.assertEqual(len(rows),2)
        self.assertEqual([r['flow'] for r in rows],['expense','income'])
        self.assertTrue(all(r['txn_date']==date(2026,11,7) for r in rows))
        self.assertEqual(len(result['txn_ids']),2)
    def test_failed_validation_never_opens_transaction(self):
        with patch.object(db,'transaction') as atomic, patch.object(queries,'insert_transaction') as insert:
            with self.assertRaises(HTTPException): asyncio.run(main.api_txn(Request({'txn_date':'9999-01-01'})))
            atomic.assert_not_called(); insert.assert_not_called()
    def test_transfer_failure_propagates(self):
        with self.assertRaises(RuntimeError): self.invoke(dict(flow='transfer',to_account_id='b'), True)
    def test_sgt_midnight(self):
        for utc,expected in [(datetime(2026,11,7,15,59,tzinfo=timezone.utc),date(2026,11,7)),(datetime(2026,11,7,16,0,tzinfo=timezone.utc),date(2026,11,8))]:
            self.assertEqual(main._transaction_date({},utc.astimezone(main.SGT).date()),expected)
    def test_balance_reflection_still_today(self):
        with patch.object(queries,'_get_account_type',return_value='asset'), patch.object(db,'query_one',side_effect=[{'balance':100},{'currency':'SGD'}]), patch.object(queries,'_today',return_value=self.today), patch.object(queries,'upsert_balance') as upsert:
            queries._reflect_balance('a','expense',Decimal('10'),'SGD',1)
            self.assertEqual(upsert.call_args.args[:3],(self.today,'a',Decimal('90.00')))
    def test_insert_reflects_only_once(self):
        with patch.object(db,'execute',return_value=1), patch.object(queries,'_reflect_balance') as reflect:
            queries.insert_transaction('t',date(2026,11,7),'a','expense','Food',10,'SGD','manual','ref')
            reflect.assert_called_once_with('a','expense',10,'SGD',sign=1)
    def test_db_pinning_rollback_no_retry(self):
        events=[]
        class Conn:
            @contextmanager
            def transaction(self):
                events.append('begin')
                try: yield
                except RuntimeError: events.append('rollback'); raise
                else: events.append('commit')
        conn=Conn()
        class Pool:
            @contextmanager
            def connection(self):
                events.append('checkout'); yield conn
        with patch.object(db,'pool',Pool()):
            with self.assertRaises(RuntimeError):
                with db.transaction():
                    with db.get_conn() as a, db.get_conn() as b:
                        self.assertIs(a,conn); self.assertIs(a,b)
                    raise RuntimeError('failure after first leg')
            self.assertIsNone(db._transaction_conn.get())
        self.assertEqual(events,['checkout','begin','rollback'])
    def test_capability(self):
        import json
        with patch.object(queries,'accounts',return_value=[]),patch.object(queries,'categories',return_value=[]):
            c=json.loads(main.api_reference().body)['capabilities']['transaction_date']
        self.assertEqual(c['version'],1); self.assertIs(c['atomic_transfer'],True)

class ReadbackTests(unittest.TestCase):
    def test_readback_retains_requested_date(self):
        import json
        row=dict(txn_id='t', txn_date=date(2026,11,7), account_id='a', account_name='A',flow='expense', category='Food', amount=Decimal('10'),currency='SGD',source='manual',note='issuer reference')
        with patch.object(queries,'count_filtered_transactions',return_value=1), patch.object(queries,'filtered_transactions',return_value=[row]) as filtered,patch.object(queries,'get_fx_rates',return_value={'SGD':Decimal('1')}),patch.object(queries,'filtered_expense_total_sgd',return_value=Decimal('10')):
            result=json.loads(main.api_transactions(date_from='2026-11-07',date_to='2026-11-07',q='t').body)
            self.assertEqual(result['transactions'][0]['txn_date'],'2026-11-07')
            self.assertEqual(filtered.call_args.args[0]['date_from'],'2026-11-07')
    def test_capability_remains_authenticated(self):
        from fastapi.testclient import TestClient
        with patch.object(main.auth,'API_KEY','test-only'),patch.object(queries,'accounts',return_value=[]),patch.object(queries,'categories',return_value=[]):
            client=TestClient(main.app)
            self.assertEqual(client.get('/api/reference').status_code,401)
            response=client.get('/api/reference',headers={'Authorization':'Bearer test-only'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['capabilities']['transaction_date']['version'],1)

if __name__=='__main__': unittest.main()
