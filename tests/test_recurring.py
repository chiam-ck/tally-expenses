"""Recurring-template behavior that does not require a live Postgres database."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app import jobs


def test_recurring_poster_skips_external_pipeline_rows():
    row = {
        "recur_id": "R99",
        "name": "YouTube Premium",
        "account_id": "WISE",
        "flow": "expense",
        "category": "Subscriptions",
        "amount": 27.98,
        "currency": "SGD",
        "day_of_month": 26,
        "frequency": "monthly",
        "month_of_year": None,
        "start_date": date(2026, 1, 1),
        "end_date": None,
        "external_pipeline": True,
    }

    def fail_if_inserted(**kwargs):
        raise AssertionError("external pipeline recurring row was auto-posted")

    with patch.object(jobs, "_today", return_value=date(2026, 8, 26)), \
         patch.object(jobs.queries, "active_recurring", return_value=[row]), \
         patch.object(jobs.queries, "insert_transaction", side_effect=fail_if_inserted):
        assert jobs.recurring_poster() == {
            "date": "2026-08-26",
            "posted": 0,
            "skipped": 0,
        }


def test_external_pipeline_rows_stay_in_projection():
    row = {
        "recur_id": "R99",
        "account_id": "WISE",
        "flow": "expense",
        "category": "Subscriptions",
        "amount": Decimal("27.98"),
        "currency": "SGD",
        "day_of_month": 26,
        "frequency": "monthly",
        "month_of_year": None,
        "start_date": date(2026, 1, 1),
        "end_date": None,
        "external_pipeline": True,
    }

    with patch.object(jobs.queries.db, "query", return_value=[row]):
        projected = jobs.queries.upcoming_recurring_sgd(
            date(2026, 8, 20), date(2026, 8, 31), {"SGD": Decimal("1")}
        )

    assert projected == Decimal("27.98")


def test_display_next_due_starts_after_today():
    monthly = {
        "frequency": "monthly",
        "day_of_month": 20,
        "month_of_year": None,
        "start_date": date(2026, 1, 1),
        "end_date": None,
    }
    every30 = {
        "frequency": "every30",
        "day_of_month": 1,
        "month_of_year": None,
        "start_date": date(2026, 8, 21),
        "end_date": None,
    }

    assert jobs.next_due(monthly, date(2026, 9, 20)) == date(2026, 9, 20)
    assert jobs.next_due(every30, date(2026, 9, 20)) == date(2026, 9, 20)
    assert jobs.next_due(
        monthly, date(2026, 9, 20), strictly_after=True
    ) == date(2026, 10, 20)
    assert jobs.next_due(
        every30, date(2026, 9, 20), strictly_after=True
    ) == date(2026, 10, 20)


def test_recurring_display_sort_prioritizes_next_due_date():
    items = [
        {"name": "Later", "active": True, "next_due": date(2026, 10, 16)},
        {"name": "Paused soon", "active": False, "next_due": date(2026, 9, 19)},
        {"name": "Soon", "active": True, "next_due": date(2026, 9, 20)},
        {"name": "No next date", "active": True, "next_due": None},
    ]

    ordered = jobs.sort_recurring_for_display(items)

    assert [item["name"] for item in ordered] == [
        "Soon", "Later", "No next date", "Paused soon"
    ]


if __name__ == "__main__":
    test_recurring_poster_skips_external_pipeline_rows()
    test_external_pipeline_rows_stay_in_projection()
    print("ok")