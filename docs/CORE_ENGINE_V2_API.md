# Core Trading Engine V2 API — Shadow Mode

`/api/v2` is an additive, authenticated API for Core Trading Engine V2. It is
currently **shadow mode only**: it can validate supplied research snapshots,
produce evidence, and persist an auditable decision record. It cannot submit,
approve, size, open, close or modify a paper or live trade.

All routes require the existing signed-in session or configured control header;
they are not public endpoints. Legacy `/api/v1` and root API routes are
unchanged.

## Evaluate a snapshot

`POST /api/v2/decisions/evaluate`

The request must provide an ISO-8601 timezone-aware `as_of` timestamp and
strictly ascending closed OHLCV bars. A bar after `as_of`, invalid high/low, or
unbounded request is rejected with HTTP 422.

```json
{
  "symbol": "BTCUSDT",
  "as_of": "2026-08-01T10:05:00Z",
  "bars_by_timeframe": {
    "5m": [
      {
        "timestamp": "2026-08-01T10:00:00Z",
        "open": 100000,
        "high": 100200,
        "low": 99900,
        "close": 100100,
        "volume": 125.4
      }
    ]
  },
  "events": [],
  "event_calendar_connected": false,
  "metadata": {
    "bid": 100095,
    "ask": 100105,
    "estimated_slippage_bps": 1.5
  }
}
```

The response includes `action: "WAIT"` and `execution_eligible: false` by
design, plus immutable evidence records. The same no-execution invariant is
enforced in the SQLite schema.

## Read models

| Route | Purpose |
|---|---|
| `GET /api/v2/decisions/latest?symbol=BTCUSDT&limit=50` | Recent persisted shadow decisions. |
| `GET /api/v2/decisions/{id}` | Full snapshot-linked evidence record. |
| `GET /api/v2/health/engines` | Stored observation count and evidence-status counts by engine. |
| `GET /api/v2/metrics/decision` | Compact dashboard metrics view of the same diagnostics. |

Records are stored in `core_v2_shadow_decisions` inside the existing durable
`HUB_DECISIONS_DB` SQLite database. They are deliberately isolated from the
legacy `decisions` table so existing paper-trading dashboards cannot treat a
shadow observation as an executable decision.
