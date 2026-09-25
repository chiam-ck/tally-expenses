"""Credit-card account routing regression tests for the text parser."""
import unittest

from app import llm


class CreditCardRoutingTests(unittest.TestCase):
    def test_generic_credit_card_uses_only_active_card(self):
        parsed = llm.regex_parse("spent 18 on credit card for lunch")
        self.assertEqual(parsed["account"], "DBS_CC")

    def test_generic_cc_abbreviation_uses_only_active_card(self):
        parsed = llm.regex_parse("spent 18 on CC for lunch")
        self.assertEqual(parsed["account"], "DBS_CC")

    def test_llm_candidate_cannot_override_generic_card_default(self):
        text = "spent 18 on credit card for lunch"
        parsed = llm._coerce(
            {
                "amount": 18,
                "category": "Food",
                "account": "MBB_CC",
                "currency": "SGD",
                "flow": "expense",
                "to_account": None,
                "note": text,
            },
            text,
        )
        self.assertEqual(parsed["account"], "DBS_CC")

    def test_generic_card_payment_transfers_from_dbs_bank(self):
        parsed = llm.regex_parse("CC payment 180")
        self.assertEqual(parsed["flow"], "transfer")
        self.assertEqual(parsed["account"], "DBS")
        self.assertEqual(parsed["to_account"], "DBS_CC")

    def test_explicit_legacy_card_mention_preserves_historical_route(self):
        parsed = llm.regex_parse("cashback refund on MBB CC")
        self.assertEqual(parsed["account"], "MBB_CC")


if __name__ == "__main__":
    unittest.main()
