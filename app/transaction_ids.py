"""Collision-safe transaction identifiers."""
from datetime import datetime
from uuid import uuid4


def make_txn_id(now: datetime) -> str:
    """Return a readable timestamp-prefixed ID with a collision-safe suffix."""
    return f"T{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex}"
