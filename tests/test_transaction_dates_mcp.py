"""MCP contract tests with every HTTP request mocked."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from tally_mcp import server
class MCPDates(unittest.TestCase):
    def test_omitted_is_backward_compatible(self):
        with patch.object(server,'_request',return_value={'ok':True}) as request:
            server.log_transaction('a','Food',1)
            request.assert_called_once()
            self.assertNotIn('txn_date',request.call_args.kwargs['json'])
    def test_legacy_or_missing_capability_never_posts(self):
        for response in [{'capabilities':None},{'capabilities':{'transaction_date':None}},{},{'error':'unauthorized'},{'capabilities':{'transaction_date':{'version':0}}},{'capabilities':{'transaction_date':{'version':1,'atomic_transfer':False}}}]:
            with self.subTest(response=response),patch.object(server,'_request',return_value=response) as request:
                result=server.log_transaction('a','Food',1,txn_date='2026-11-07')
                self.assertIn('error',result)
                request.assert_called_once_with('GET','/api/reference')
    def test_explicit_date_forwarded_unchanged(self):
        cap={'capabilities':{'transaction_date':{'version':1,'atomic_transfer':True,'endpoint':'/api/txn/dated'}}}
        with patch.object(server,'_request',side_effect=[cap,{'ok':True,'txn_date':'2026-11-07'}]) as request:
            result=server.log_transaction('a','Food',1,txn_date='2026-11-07')
            self.assertEqual(request.call_args.kwargs['json']['txn_date'],'2026-11-07')
            self.assertEqual(result['txn_date'],'2026-11-07')
            self.assertEqual(request.call_args.args,('POST','/api/txn/dated'))
    def test_rollback_between_capability_and_post_cannot_use_old_write_route(self):
        cap={'capabilities':{'transaction_date':{'version':1,'atomic_transfer':True,'endpoint':'/api/txn/dated'}}}
        with patch.object(server,'_request',side_effect=[cap,{'error':'HTTP 404: Not Found'}]) as request:
            result=server.log_transaction('a','Food',1,txn_date='2026-11-07')
            self.assertIn('404',result['error'])
            self.assertEqual(request.call_count,2)
            self.assertEqual(request.call_args.args,('POST','/api/txn/dated'))
if __name__=='__main__':unittest.main()
