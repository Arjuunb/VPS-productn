"""Duplicate-alert protection (Phase 1).

TradingView can fire the same alert multiple times (retries, repaints, multiple
webhooks). We reject a repeat ``alert_id`` seen within a rolling time window,
using the ledger's webhook_events as the store of record.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from data.ledger import Ledger


class DuplicateGuard:
    def __init__(self, ledger: Ledger, window_seconds: int = 300):
        self.ledger = ledger
        self.window_seconds = window_seconds

    def is_duplicate(self, alert_id: str) -> bool:
        if not alert_id:
            return False
        # Autonomous IDs encode instance + candle + action and are immutable.
        # Their protection must outlive the normal webhook retry window: a
        # cursor checkpoint can fail, then the same candle can be recovered
        # hours later after a restart.
        since = ("1970-01-01T00:00:00+00:00" if alert_id.startswith("auto:")
                 else (datetime.now(timezone.utc)
                       - timedelta(seconds=self.window_seconds)).isoformat())
        return self.ledger.webhook_seen(alert_id, since)
