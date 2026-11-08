"""Opt-in real PostgreSQL rollback tests, restricted to a disposable Unix socket.
Run only in the documented network-disabled test containers, never production.
"""
import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
import unittest
from unittest.mock import patch

@unittest.skipUnless(os.environ.get('TALLY_ISOLATED_POSTGRES_TEST') == '1', 'requires disposable test PostgreSQL')
class PostgresAtomicity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        # Fixed disposable database/socket, never consume production DATABASE_URL.
        cls.url='postgresql:///tally_date_test?host=/var/run/postgresql&user=postgres'
        with psycopg.connect('postgresql:///postgres?host=/var/run/postgresql&user=postgres',autocommit=True) as conn:
            conn.execute('CREATE DATABASE tally_date_test')
        os.environ['DATABASE_URL']=cls.url
        from app import db, main, queries
        cls.db,cls.main,cls.queries=db,main,queries
        db.DATABASE_URL=cls.url
        db.open_pool()
        db.execute((Path(__file__).resolve().parent.parent/'schema.sql').read_text())
        db.execute("INSERT INTO accounts(account_id,name,type) VALUES ('a','Test A','asset'),('b','Test B','asset')")
        db.execute("INSERT INTO categories(name) VALUES ('Food')")
    @classmethod
    def tearDownClass(cls): cls.db.close_pool()
    def setUp(self):
        self.db.execute('TRUNCATE transactions, balances')
        self.today=datetime.now(self.main.SGT).date()
        self.yesterday=self.today-timedelta(days=1)
        for account,amount in [('a',100),('b',50)]:
            self.queries.upsert_balance(self.yesterday,account,amount,'SGD')
    def invoke(self):
        body=dict(account_id='a',to_account_id='b',flow='transfer',category='Food',amount='10',currency='SGD',txn_date=self.yesterday.isoformat(),note='isolated synthetic test')
        class Request:
            async def json(self): return body
        return asyncio.run(self.main.api_txn_dated(Request()))
    def assert_unchanged(self):
        self.assertEqual(self.db.query_one('SELECT count(*) AS n FROM transactions')['n'],0)
        rows=self.db.query('SELECT account_id,balance,snap_date FROM balances ORDER BY account_id')
        self.assertEqual([(r['account_id'],int(r['balance']),r['snap_date']) for r in rows],[('a',100,self.yesterday),('b',50,self.yesterday)])
        self.assertIsNone(self.db._transaction_conn.get())
    def test_second_leg_failure_rolls_everything_back(self):
        original=self.queries.insert_transaction; calls=[]
        def insert(**kwargs):
            calls.append(kwargs)
            if len(calls)==2: raise RuntimeError('synthetic second-leg failure')
            return original(**kwargs)
        with patch.object(self.queries,'insert_transaction',side_effect=insert),self.assertRaises(RuntimeError):self.invoke()
        self.assertEqual(len(calls),2);self.assert_unchanged()
    def test_balance_failure_no_replay(self):
        from psycopg import OperationalError
        with patch.object(self.queries,'_reflect_balance',side_effect=OperationalError('synthetic failure')) as reflect,self.assertRaises(OperationalError):self.invoke()
        reflect.assert_called_once();self.assert_unchanged()
    def test_success_both_legs_balances_and_dates(self):
        response=self.invoke()
        rows=self.db.query('SELECT txn_id,txn_date,account_id,flow FROM transactions ORDER BY txn_id')
        self.assertEqual([r['txn_id'] for r in rows],response['txn_ids'])
        self.assertTrue(all(r['txn_date']==self.yesterday for r in rows))
        current=self.db.query('SELECT account_id,balance,snap_date FROM v_latest_balance ORDER BY account_id')
        self.assertEqual([(r['account_id'],int(r['balance']),r['snap_date']) for r in current],[('a',90,self.today),('b',60,self.today)])
        self.assertEqual(self.db.query_one('SELECT count(*) AS n FROM balances WHERE snap_date=%s',(self.yesterday,))['n'],2)
if __name__=='__main__':unittest.main()
