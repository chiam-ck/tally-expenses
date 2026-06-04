"""Scheduled jobs (APScheduler, Asia/Singapore). All idempotent and read-safe.

Stocks/flows rule: the recurring poster writes ONLY to ``transactions``; the
rollover writes ONLY to ``balances`` (a stock carry-forward, not a flow).
"""
from __future__ import annotations

import calendar
import logging
import os
from datetime import datetime

import httpx

from . import fx, queries
from .db import SGT

log = logging.getLogger("expenses.jobs")


def _today():
    return datetime.now(SGT).date()


# ── recurring poster (daily 00:10) ──────────────────────────────────────────

def recurring_poster() -> dict:
    """Post any recurring template due today. Idempotent via partial unique index."""
    today = _today()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    posted = 0
    skipped = 0

    for r in queries.active_recurring(today):
        # Yearly templates fire only in their month; monthly fire every month.
        if r.get("frequency") == "yearly" and today.month != r.get("month_of_year"):
            continue
        effective_day = min(r["day_of_month"], days_in_month)
        if today.day != effective_day:
            continue
        txn_id = f"T{today.strftime('%Y%m%d')}-{r['recur_id']}"
        rows = queries.insert_transaction(
            txn_id=txn_id,
            txn_date=today,
            account_id=r["account_id"],
            flow=r["flow"],
            category=r["category"],
            amount=r["amount"],
            currency=r["currency"],
            source=f"recurring:{r['recur_id']}",
            note="auto",
            on_conflict_nothing=True,
        )
        if rows:
            posted += 1
        else:
            skipped += 1

    log.info("recurring_poster %s: posted=%d skipped=%d", today, posted, skipped)
    return {"date": today.isoformat(), "posted": posted, "skipped": skipped}


# ── monthly rollover (1st 00:05) ────────────────────────────────────────────

def monthly_rollover() -> dict:
    """Carry each account's latest balance forward to a 1st-of-month snapshot."""
    today = _today()
    first = today.replace(day=1)
    inserted = 0

    for row in queries.latest_balance_rows():
        rows = queries.rollover_balance(
            snap_date=first,
            account_id=row["account_id"],
            balance=row["balance"],
            currency=row["currency"],
        )
        inserted += rows

    log.info("monthly_rollover %s: inserted=%d", first, inserted)
    return {"date": first.isoformat(), "inserted": inserted}


# ── FX rate refresh (daily 00:20 + on boot) ─────────────────────────────────

def fx_update() -> dict:
    """Refresh stored FX rates from the web source. Keeps last rates on failure."""
    try:
        rates = fx.fetch_rates()
    except Exception as exc:  # noqa: BLE001 — never propagate; keep last/fallback
        log.warning("fx_update: fetch failed (%s); keeping stored rates", exc)
        return {"updated": False, "reason": str(exc)}
    n = queries.upsert_fx_rates(rates, source="open.er-api.com")
    log.info("fx_update: refreshed %d rates", n)
    return {"updated": True, "count": n}


# ── weekly digest (Mon 07:00) ───────────────────────────────────────────────

def _digest_html(m: dict) -> str:
    nc = m["net_cash"]
    rows = "".join(
        f"<tr><td>{c['category']}</td>"
        f"<td style='text-align:right'>{queries.d2(c['sgd'])}</td></tr>"
        for c in m["spend_by_category"]
    )
    return f"""
    <div style="font-family:system-ui,sans-serif">
      <h2>Expenses — week of {m['today']}</h2>
      <p><b>Net cash</b>: SGD {nc['SGD']} · MYR {nc['MYR']} · Combined SGD {nc['combined_sgd']}</p>
      <p><b>Total expenses MTD (SGD)</b>: {m['total_expenses_sgd']}<br>
         <b>Days elapsed</b>: {m['days_elapsed']} / {m['days_in_month']}<br>
         <b>Avg daily burn</b>: {m['avg_daily_burn']}<br>
         <b>Projected month</b>: {m['projected_month']}</p>
      <table cellpadding="4" style="border-collapse:collapse">
        <tr><th align="left">Category</th><th align="right">SGD MTD</th></tr>
        {rows}
      </table>
    </div>
    """


def weekly_digest() -> dict:
    """Email the weekly summary via Resend. No-op if RESEND_API_KEY is unset."""
    today = _today()
    metrics = queries.dashboard(today)

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        log.info("weekly_digest %s: RESEND_API_KEY unset, no-op", today)
        return {"sent": False, "reason": "RESEND_API_KEY unset"}

    digest_from = os.environ.get("DIGEST_FROM", "")
    digest_to = os.environ.get("DIGEST_TO", "")
    if not digest_from or not digest_to:
        log.info("weekly_digest %s: DIGEST_FROM/TO unset, no-op", today)
        return {"sent": False, "reason": "DIGEST_FROM/TO unset"}

    body = {
        "from": digest_from,
        "to": [digest_to],
        "subject": f"Expenses weekly digest — {today.isoformat()}",
        "html": _digest_html(metrics),
    }
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        log.info("weekly_digest %s: sent to %s", today, digest_to)
        return {"sent": True, "to": digest_to}
    except Exception as exc:  # noqa: BLE001
        log.warning("weekly_digest %s: send failed (%s)", today, exc)
        return {"sent": False, "reason": str(exc)}


# ── scheduler wiring ────────────────────────────────────────────────────────

def register_jobs(scheduler) -> None:
    from apscheduler.triggers.cron import CronTrigger

    tz = SGT
    scheduler.add_job(
        recurring_poster, CronTrigger(hour=0, minute=10, timezone=tz),
        id="recurring_poster", replace_existing=True,
    )
    scheduler.add_job(
        monthly_rollover, CronTrigger(day=1, hour=0, minute=5, timezone=tz),
        id="monthly_rollover", replace_existing=True,
    )
    scheduler.add_job(
        weekly_digest, CronTrigger(day_of_week="mon", hour=7, minute=0, timezone=tz),
        id="weekly_digest", replace_existing=True,
    )
    scheduler.add_job(
        fx_update, CronTrigger(hour=0, minute=20, timezone=tz),
        id="fx_update", replace_existing=True,
    )
