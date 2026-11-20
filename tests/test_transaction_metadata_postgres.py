"""Run this only with the disposable, network-disabled PostgreSQL fixture."""
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
KEY = 'tally_sk_synthetic_metadata_fixture'
URL = 'postgresql:///tally_metadata_test?host=/var/run/postgresql&user=postgres'
NOTE = ('receipt:0123456789abcdef; SYNTHETIC SHOP; ref:FIXTURE-NO_1; '
        'at:2026-01-02T12:00:00+08:00')
DATE = '2026-01-02'
ID = 'T-SYNTHETIC-METADATA'


@unittest.skipUnless(os.environ.get('TALLY_ISOLATED_POSTGRES_TEST') == '1',
                     'requires disposable, network-disabled PostgreSQL')
class MetadataPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        with psycopg.connect('postgresql:///postgres?host=/var/run/postgresql&user=postgres',
                             autocommit=True) as conn:
            conn.execute('CREATE DATABASE tally_metadata_test')
        os.environ['DATABASE_URL'] = URL
        from app import auth, db, main, queries
        from fastapi.testclient import TestClient
        cls.previous_key, cls.previous_dsn = auth.API_KEY, db.DATABASE_URL
        cls.auth, cls.db, cls.queries = auth, db, queries
        auth.API_KEY = KEY
        db.DATABASE_URL = URL
        db.open_pool()
        cls.client = TestClient(main.app)
        cls.db.execute((Path(__file__).resolve().parent.parent/'schema.sql').read_text())
        cls.db.execute('ALTER TABLE transactions DROP COLUMN display_label, DROP COLUMN original_receipt_category')
        queries.ensure_fx_schema()
        queries.ensure_transaction_metadata_schema()
        queries.ensure_transaction_metadata_schema()  # deploy/boot retry is safe
        cls.db.execute("INSERT INTO accounts(account_id,name,type) VALUES ('test','Synthetic','asset')")
        cls.db.execute("INSERT INTO categories(name) VALUES ('Old'),('New'),('Alternate')")

    @classmethod
    def tearDownClass(cls):
        cls.db.close_pool()
        cls.db.DATABASE_URL = cls.previous_dsn
        cls.auth.API_KEY = cls.previous_key

    def setUp(self):
        self.db.execute("TRUNCATE transactions, balances")
        self.db.execute(
            """INSERT INTO transactions(txn_id,txn_date,account_id,flow,category,amount,currency,source,note)
               VALUES (%s,%s,'test','expense','Old',1.23,'SGD','manual',%s)""", (ID,DATE,NOTE))
        self.queries.upsert_balance(DATE, 'test', 20, 'SGD')
        self.headers = {"Authorization": f"Bearer {KEY}"}
        self.path = f"/api/txn/{ID}/metadata"
        self.body = {"expected": {"category": "Old", "display_label": None},
                     "replacement": {"category": "New", "display_label": "Synthetic approved label"}}

    def edit(self, body=None):
        return self.client.patch(self.path, json=self.body if body is None else body, headers=self.headers)

    def list_rows(self, **filters):
        r = self.client.get('/api/transactions',
                            params=dict(date_from=DATE,date_to=DATE,**filters),headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['transactions']

    def test_in_place_replay_overlap_and_balance(self):
        before = self.list_rows()[0]
        self.assertTrue(self.edit().json()['changed'])
        self.assertFalse(self.edit().json()['changed'])
        canonical = self.list_rows(q=NOTE)[0]
        normal = self.list_rows()[0]
        for field in ('txn_id','txn_date','account_id','flow','amount','currency','source','note'):
            self.assertEqual(normal[field], before[field], field)
        self.assertEqual((normal['category'], normal['display_label']),
                         ('New', self.body['replacement']['display_label']))
        self.assertEqual((canonical['category'],canonical['note'],canonical['reporting_category']),
                         ('Old', NOTE, 'New'))
        self.assertEqual(self.list_rows(q='FIXTURE-NO_1')[0]['category'], 'New')
        self.assertEqual(self.list_rows(category='New')[0]['txn_id'], ID)
        self.assertEqual(self.list_rows(category='Old'), [])
        self.assertEqual(self.list_rows(q='Synthetic approved label'), [])
        self.assertEqual(self.db.query_one("SELECT balance FROM balances")['balance'],20)
        # Real local receipt executor, if mounted read-only by the operator.
        # Both its exact replay and category-normalized issuer-overlap proof
        # must match the immutable authorization packet and original txn_id.
        if '/receipt-verifier' in sys.path:
            import neutral_expense
            plan={k:canonical[k] for k in ('txn_date','account_id','flow','currency','note')}
            plan.update(category='Old', amount='1.23')
            assert neutral_expense.matching([canonical],plan)
            assert neutral_expense.matching([dict(normal,category='Old')],plan)
            assert canonical['txn_id']==normal['txn_id']==ID

    def test_auth_validation_conflict_and_missing(self):
        self.assertEqual(self.client.patch(self.path,json=self.body).status_code,401)
        self.assertEqual(self.client.patch(self.path,json=self.body, headers={'X-API-Key':'wrong'}).status_code,401)
        self.assertEqual(self.edit(dict(self.body, amount=99)).status_code,422)
        extra = dict(self.body['replacement'], note='forged marker')
        self.assertEqual(self.edit(dict(self.body,replacement=extra)).status_code,422)
        self.assertEqual(self.edit(dict(self.body,replacement=dict(extra, note=None))).status_code,422)
        bad_category = dict(self.body['replacement'],category='Unknown')
        self.assertEqual(self.edit(dict(self.body,replacement=bad_category)).status_code,422)
        bad_label = dict(self.body['replacement'],display_label='new\nline')
        self.assertEqual(self.edit(dict(self.body,replacement=bad_label)).status_code,422)
        stale = dict(self.body['expected'],category='Alternate')
        self.assertEqual(self.edit(dict(self.body,expected=stale)).status_code,409)
        self.assertEqual(self.client.patch('/api/txn/not-there/metadata',json=self.body,
                                           headers=self.headers).status_code,404)
        self.assertEqual(self.list_rows()[0]['category'],'Old')

    def test_protected_row_and_history_preserve_proof(self):
        self.db.execute("UPDATE transactions SET source='recurring:fixture'")
        self.assertEqual(self.edit().status_code,409)
        self.db.execute("UPDATE transactions SET source='manual', txn_id=%s", (ID+'_A',))
        self.assertEqual(self.client.patch(self.path.replace(ID,ID+'_A'),
                         json=self.body,headers=self.headers).status_code,409)
        self.db.execute("UPDATE transactions SET txn_id=%s", (ID,))
        self.assertEqual(self.client.post(f'/history/txn/{ID}/update',data={'note':'changed'},follow_redirects=False).status_code,303)
        # Follow a redirect to /login if no session was actually present.
        self.assertIsNone(self.list_rows()[0]['display_label'])
        result=self.queries.edit_transaction_metadata(ID,category='New',
                label='UI label',write_label=True,history_note=True)
        self.assertEqual(result['display_label'],'UI label')
        self.assertEqual(self.list_rows(q=NOTE)[0]['category'],'Old')
        self.assertEqual(self.list_rows()[0]['note'],NOTE)


if __name__ == '__main__':
    unittest.main()
