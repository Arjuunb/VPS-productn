"""Shared-feed PA/SMC observational research runtime."""
from __future__ import annotations

import threading
from dataclasses import asdict
from datetime import timedelta

from bot.data.indicators import atr
from bot.types import Bar
from services.forward_paper_hub import ForwardPaperMarketDataHub, candle_id
from services.native_price_action import NativePriceActionEngine, PriceActionConfig
from services.native_smc import SMCConfig, SMCMarketStructureEngine
from services.research_context import CausalHTFContext, NamedLiquidityBook, session_tag, stable_hash
from services.research_variants import ShadowVariantRunner
from services.shadow_research import ShadowResearchStore


class ResearchObservationRuntime:
    """Fan one immutable closed-candle projection into all shadow variants.

    This service has no account or broker reference. Exceptions are contained
    within this optional observer and surface as research status; they cannot
    kill the market-data stream used by the real-paper engines.
    """

    def __init__(self, market_hub: ForwardPaperMarketDataHub,
                 store: ShadowResearchStore, *, symbol: str = "BTCUSDT",
                 timeframe: str = "5m", research_config: dict | None = None):
        self.market_hub = market_hub
        self.store = store
        self.symbol = symbol.upper().replace("/", "")
        self.timeframe = timeframe
        self.research_config = dict(research_config or {})
        self.htf = CausalHTFContext()
        self.liquidity = NamedLiquidityBook(
            swing_left=int(self.research_config.get("swing_left", 3)),
            swing_right=int(self.research_config.get("swing_right", 3)),
            equal_tolerance_atr=self.research_config.get("equal_tolerance_atr"),
        )
        self.pa = NativePriceActionEngine(PriceActionConfig(
            symbol=self.symbol, timeframe=self.timeframe, execution_allowed=False))
        self.smc = SMCMarketStructureEngine(SMCConfig(
            symbol=self.symbol, timeframe=self.timeframe, execution_allowed=False))
        self.variants = ShadowVariantRunner(store, research_config={
            **self.research_config, "liquidity_config_hash": self.liquidity.config_hash,
        })
        self._lock = threading.RLock()
        self._last_observation: dict = {}
        self._last_error = ""
        self._market_data_fresh = True
        self._subscriptions = [
            market_hub.subscription(
                f"research:{self.symbol}:{self.timeframe}:decision",
                bar_sink=self._on_closed_bar, quote_sink=self._on_quote,
                event_sink=self._on_event),
            market_hub.subscription(
                f"research:{self.symbol}:{self.timeframe}:1h",
                bar_sink=lambda bar: self._on_htf("1h", bar)),
            market_hub.subscription(
                f"research:{self.symbol}:{self.timeframe}:4h",
                bar_sink=lambda bar: self._on_htf("4h", bar)),
        ]

    def start(self) -> bool:
        started = self._subscriptions[0].start(self.symbol, self.timeframe)
        htf_started = self._subscriptions[1].start(self.symbol, "1h")
        htf4_started = self._subscriptions[2].start(self.symbol, "4h")
        return bool(started and htf_started and htf4_started)

    def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.stop()

    def _on_event(self, event: dict) -> None:
        state = str(event.get("state") or "")
        if event.get("kind") in {"market_data_health", "connection"}:
            self._market_data_fresh = state in {"SYNCHRONIZED", "CONNECTED"}

    def _on_htf(self, timeframe: str, bar: Bar) -> None:
        try:
            self.htf.ingest(self.symbol, timeframe, bar,
                            candle_id(self.symbol, timeframe, bar))
        except Exception as exc:  # research must not interrupt the shared feed
            with self._lock:
                self._last_error = f"HTF observation failed closed: {exc}"

    def _feature_projection(self, bar: Bar, pa_snapshot, smc_snapshot,
                            liquidity: list[dict]) -> dict:
        decision_time = bar.timestamp + timedelta(
            milliseconds={"1m": 60_000, "3m": 180_000, "5m": 300_000,
                          "15m": 900_000, "30m": 1_800_000,
                          "1h": 3_600_000, "4h": 14_400_000,
                          "1d": 86_400_000}[self.timeframe])
        sweep = self.smc.events.get(smc_snapshot.latest_sweep_id or "")
        direction = getattr(sweep, "direction", None)
        smc_proposal = next((self.smc.proposals[ident] for ident in smc_snapshot.proposal_ids
                             if ident in self.smc.proposals), None)
        pa_proposal = next((self.pa.proposals[ident] for ident in pa_snapshot.proposal_ids
                            if ident in self.pa.proposals), None)
        proposal = smc_proposal or pa_proposal
        if direction is None and proposal is not None:
            direction = proposal.direction
        htf = self.htf.at(self.symbol, decision_time)
        preferred_htf = htf.get("4h") or htf.get("1h")
        htf_aligned = bool(direction and preferred_htf and (
            (direction == "bullish" and preferred_htf["bias"] == "BULLISH") or
            (direction == "bearish" and preferred_htf["bias"] == "BEARISH")
        ))
        relevant_liquidity = [row for row in liquidity if (
            row["side"] == ("LOW" if direction == "bullish" else "HIGH")
            and row["freshness"] == "FRESH"
        )] if direction else []
        traces = {(row.strategy_id, row.direction): row for row in pa_snapshot.strategy_traces}
        sr = next((row for (strategy, _), row in traces.items()
                   if strategy == "PA1_SR_REJECTION" and row.state == "ORDER_PENDING"), None)
        flip = next((row for (strategy, _), row in traces.items()
                     if strategy == "PA3_FLIP_RETEST" and row.state == "ORDER_PENDING"), None)
        fvg_on_bar = any(gap.created_at == bar.timestamp for gap in self.smc.fvgs.values())
        entry = stop = target = None
        if proposal is not None:
            entry, stop, target = proposal.entry, proposal.stop, proposal.target
        elif sweep is not None:
            current_atr = atr(self.smc.bars, self.smc.config.atr_length)
            if current_atr > 0:
                entry = bar.close
                stop = (bar.low - current_atr * self.smc.config.atr_multiplier
                        if direction == "bullish" else
                        bar.high + current_atr * self.smc.config.atr_multiplier)
                risk = abs(entry - stop)
                target = (entry + risk * self.smc.config.rr_ratio
                          if direction == "bullish" else
                          entry - risk * self.smc.config.rr_ratio)
        return {
            "symbol": self.symbol, "timeframe": self.timeframe,
            "candle_open": bar.timestamp.isoformat(),
            "candle_close": decision_time.isoformat(),
            "market_data_source": "Binance USD-M public WebSocket",
            "market_data_fresh": self._market_data_fresh,
            "direction": direction, "sweep": sweep is not None,
            # Native SMC sweep creation already requires a closed reclaim.
            "closed_reclaim": sweep is not None,
            "sweep_id": getattr(sweep, "id", None),
            "sweep_level": getattr(sweep, "level", None),
            "displacement": fvg_on_bar,
            "fresh_liquidity": bool(relevant_liquidity),
            "liquidity": liquidity,
            "session": session_tag(bar.timestamp),
            "htf": htf, "htf_aligned": htf_aligned,
            "full_smc_ready": bool(smc_snapshot.proposal_ids),
            "smc_missing_condition": smc_snapshot.next_required_event,
            "pa_sr_rejection": sr is not None,
            "pa_flip_retest": flip is not None,
            "pa_snapshot_id": pa_snapshot.id,
            "smc_snapshot_id": smc_snapshot.id,
            "entry": entry, "stop_loss": stop, "take_profit": target,
            "order_type": "market",
        }

    def _on_closed_bar(self, bar: Bar) -> None:
        try:
            cid = candle_id(self.symbol, self.timeframe, bar)
            liquidity = self.liquidity.ingest(self.symbol, self.timeframe, bar, cid)
            pa_snapshot = self.pa.process_closed_bar(
                bar, market_data_health=("SYNCHRONIZED" if self._market_data_fresh else "STALE_CANDLES"))
            smc_snapshot = self.smc.process_closed_bar(bar)
            features = self._feature_projection(bar, pa_snapshot, smc_snapshot, liquidity)
            lineage = stable_hash(features)
            decisions = self.variants.evaluate(
                candle_id=cid, snapshot_lineage=lineage,
                decision_timestamp=features["candle_close"], features=features)
            with self._lock:
                self._last_observation = {
                    "candle_id": cid, "snapshot_lineage": lineage,
                    "features": features, "decisions": decisions,
                }
                self._last_error = ""
        except Exception as exc:  # optional observer is fail-closed and isolated
            with self._lock:
                self._last_error = f"research observation failed closed: {exc}"

    def _on_quote(self, quote: dict) -> None:
        if not self._market_data_fresh:
            return
        try:
            for order in self.store.open_orders(self.symbol):
                if order["status"] in {"INTENT", "SHADOW_REJECTED_INTENT"}:
                    self.store.record_fill(
                        order["order_id"], quote,
                        slippage_bps=float(self.research_config.get("slippage_bps", 3.0)),
                        commission_bps=float(self.research_config.get("commission_bps", 4.0)),
                    )
                    continue
                excursion = self.store.observe_mae_mfe(order["order_id"], quote)
                side = str(order["side"]).lower()
                long = side in {"buy", "long"}
                stop_hit = (float(quote["bid"]) <= float(order["stop_loss"]) if long else
                            float(quote["ask"]) >= float(order["stop_loss"]))
                target_hit = (float(quote["bid"]) >= float(order["take_profit"]) if long else
                              float(quote["ask"]) <= float(order["take_profit"]))
                if stop_hit or target_hit:
                    self.store.record_outcome(
                        order["order_id"], quote,
                        exit_reason="STOP" if stop_hit else "TARGET",
                        slippage_bps=float(self.research_config.get("slippage_bps", 3.0)),
                        commission_bps=float(self.research_config.get("commission_bps", 4.0)),
                    )
                _ = excursion
        except Exception as exc:
            with self._lock:
                self._last_error = f"research quote observation failed closed: {exc}"

    def status(self) -> dict:
        with self._lock:
            observation = dict(self._last_observation)
            error = self._last_error
        return {
            "state": "ERROR" if error else "OBSERVING",
            "error": error or None,
            "execution_class": "SHADOW", "real_execution_allowed": False,
            "symbol": self.symbol, "timeframe": self.timeframe,
            "last_observation": observation,
            "table_counts": self.store.table_counts(),
            "registry": self.variants.registry,
        }

