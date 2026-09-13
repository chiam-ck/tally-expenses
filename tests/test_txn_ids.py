"""Regression tests for transaction ID generation."""
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.transaction_ids import make_txn_id


def test_transaction_ids_are_unique_within_the_same_second():
    now = datetime(2026, 9, 13, 9, 32, 32)

    ids = {make_txn_id(now) for _ in range(100)}

    assert len(ids) == 100
    assert all(txn_id.startswith("T20260913093232-") for txn_id in ids)


if __name__ == "__main__":
    test_transaction_ids_are_unique_within_the_same_second()
    print("ok")
