"""Regression tests for account choices in transaction and recurring forms."""
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import main
from app.main import templates


ACCOUNTS = [
    {"account_id": "DBS_CC", "name": "DBS CC", "currency": "SGD", "type": "liability", "active": True},
    {"account_id": "MBB_CC", "name": "MBB CC", "currency": "SGD", "type": "liability", "active": False},
]
INACTIVE_RECURRING_FORM = {
    "account_id": "MBB_CC",
    "category": "Subscriptions",
    "currency": "SGD",
    "flow": "expense",
    "frequency": "monthly",
    "name": "Existing monthly charge",
    "amount": "9.99",
    "day_of_month": "1",
    "start_date": "",
    "end_date": "",
    "note": "",
    "external_pipeline": "",
}
EXISTING_INACTIVE_RECURRING = {
    "recur_id": "R1",
    **INACTIVE_RECURRING_FORM,
    "amount": 9.99,
    "month_of_year": None,
    "active": True,
}


def make_request(path="/settings/recurring"):
    return Request({
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    })


def active_base_context():
    with patch.object(main.queries, "accounts", return_value=ACCOUNTS), \
         patch.object(main.queries, "categories", return_value=[]), \
         patch.object(main.queries, "latest_balances", return_value=[]):
        return main.base_ctx(None)


def recurring_page_html(editing=None):
    with patch.object(main.queries, "get_recurring", return_value=editing), \
         patch.object(main.queries, "list_recurring", return_value=[]), \
         patch.object(main.queries, "accounts", return_value=ACCOUNTS), \
         patch.object(main.queries, "categories", return_value=[]), \
         patch.object(main.queries, "latest_balances", return_value=[]):
        response = main.page_recurring(
            make_request(), edit=editing["recur_id"] if editing else ""
        )
    return response.body.decode()


def accounts_page_html():
    refs = {"txns": 3, "bals": 1, "recs": 0}
    with patch.object(main.queries, "accounts", return_value=ACCOUNTS), \
         patch.object(main.queries, "account_reference_counts", return_value=refs), \
         patch.object(main.queries, "categories", return_value=[]), \
         patch.object(main.queries, "latest_balances", return_value=[]), \
         patch.object(main.queries, "next_account_sort", return_value=3):
        response = main.page_accounts(make_request("/settings/accounts"))
    return response.body.decode()


class OptionValues(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = []

    def handle_starttag(self, tag, attrs):
        if tag == "option":
            value = dict(attrs).get("value")
            if value is not None:
                self.values.append(value)


def option_values(html):
    parser = OptionValues()
    parser.feed(html)
    return parser.values


class AccountDropdownTests(unittest.TestCase):
    def test_dashboard_latest_balances_hide_inactive_accounts(self):
        metrics = {
            "latest_balances": [
                {"account_id": "DBS_CC", "active": True},
                {"account_id": "MBB_CC", "active": False},
            ]
        }
        displayed = main._dashboard_display_metrics(metrics)
        self.assertEqual(
            [row["account_id"] for row in displayed["latest_balances"]], ["DBS_CC"]
        )
        self.assertEqual(len(metrics["latest_balances"]), 2)

    def test_inactive_accounts_are_hidden_in_collapsed_archive(self):
        html = accounts_page_html()
        archive_start = html.index('<details id="inactive-accounts"')
        self.assertNotIn("MBB_CC", html[:archive_start])
        self.assertIn("Inactive accounts (1)", html[archive_start:])
        self.assertIn("MBB_CC", html[archive_start:])

    def test_transaction_form_does_not_offer_inactive_account(self):
        context = active_base_context()
        html = templates.env.get_template("_log_modal.html").render(**context)
        values = option_values(html)
        self.assertIn("DBS_CC", values)
        self.assertNotIn("MBB_CC", values)

    def test_unavailable_parsed_account_requires_explicit_active_choice(self):
        context = active_base_context()
        html = templates.env.get_template("_log_modal.html").render(**context)
        self.assertIn('<select id="account" required>', html)
        self.assertIn('value="" disabled selected', html)
        self.assertIn("if (!accSel.value)", html)

    def test_new_recurring_form_does_not_offer_inactive_account(self):
        values = option_values(recurring_page_html())
        self.assertIn("DBS_CC", values)
        self.assertNotIn("MBB_CC", values)

    def test_editing_recurring_preserves_its_inactive_account(self):
        html = recurring_page_html(EXISTING_INACTIVE_RECURRING)
        self.assertIn("MBB_CC", option_values(html))
        self.assertIn("inactive, already assigned", html)

    def test_new_recurring_rejects_inactive_account_on_post(self):
        with patch.object(main.queries, "account_map", return_value={"MBB_CC": ACCOUNTS[1]}), \
             patch.object(main.queries, "category_names", return_value={"Subscriptions"}):
            with self.assertRaises(HTTPException):
                main._parse_recurring_form(INACTIVE_RECURRING_FORM)

    def test_existing_recurring_can_keep_its_current_inactive_account(self):
        with patch.object(main.queries, "account_map", return_value={"MBB_CC": ACCOUNTS[1]}), \
             patch.object(main.queries, "category_names", return_value={"Subscriptions"}):
            parsed = main._parse_recurring_form(
                INACTIVE_RECURRING_FORM, allow_existing_inactive="MBB_CC"
            )
        self.assertEqual(parsed["account_id"], "MBB_CC")


if __name__ == "__main__":
    unittest.main()
