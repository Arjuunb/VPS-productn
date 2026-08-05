# Paper-Trading Evidence Policy

TradeLogX does not promise profitability and never promotes a strategy to live
execution automatically. Backtests and replay are research tools; they do not
qualify as forward paper evidence.

## Required before a human live-trading review

The application remains hard-locked to paper execution. The review panel now
requires all of the following before it can show `ready-for-review (evidence)`:

- At least 100 closed paper trades. Sixty trades only opens an early review.
- Forward paper trading on a connected live market-data feed, not replay or
  synthetic candles.
- A non-ideal fill model that includes transaction costs.
- Profit factor of at least 1.15 and positive expectancy.
- Maximum realised paper drawdown no greater than 10%.
- Chronological stability: the latest window and at least two of three
  contiguous trade windows must be profitable with a profit factor of at least
  1.0.
- A measured `Degrading` or `Unhealthy` paper record reduces only future
  entry risk for that symbol to 75% or 50%, respectively. It never alters an
  open trade, suppresses an exit, or increases risk.
- The quality gate applies its existing losing-streak penalty after three
  consecutive closed paper losses for the same symbol and blocks a new trend
  entry after five. This is scoped per symbol and never closes an open trade.
- Configured daily-loss and drawdown limits, recorded decision logs, and a
  verified emergency-stop drill.

Meeting these conditions is evidence for a human review only. It does not enable
a broker, send a real order, or guarantee future returns. The policy is designed
to reduce the risk of selecting a strategy that only looked good because of
backtest selection or a single favourable market regime.

## VPS paper-validation configuration

Set the paper settings in `/opt/VPS-productn/.env`, then recreate only the app:

```dotenv
HUB_USE_LIVE_DATA=1
HUB_EXCHANGE=kraken
HUB_FILL_MODEL=realistic
HUB_MAX_DAILY_LOSS=0.01
HUB_MAX_DRAWDOWN=0.10
HUB_EXPOSURE_LIMIT=0.05
HUB_STRATEGY_HEALTH_GUARD=1
```

```sh
cd /opt/VPS-productn
docker compose up -d --build --no-deps --force-recreate app
```

Check `GET /validation/paper` or the dashboard **Strategy Proof** page. If the
market feed reports replay, fallback, or failed, its performance remains useful
for research but cannot qualify for a future live review.
