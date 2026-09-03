"""Frozen PA/SMC shadow variant registry.

All variants consume one caller-supplied feature snapshot. They never rebuild
historical state, mutate a strategy, or import an execution/account service.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from services.research_context import stable_hash
from services.shadow_research import ShadowResearchStore


REGISTRY_VERSION = "PA_SMC_SHADOW_VARIANTS_V1"


@dataclass(frozen=True)
class VariantDefinition:
    key: str
    strategy_id: str
    strategy_version: str
    engine: str
    label: str
    required_features: tuple[str, ...]
    attribution_features: tuple[str, ...] = ()
    execution_class: str = "SHADOW"

    @property
    def definition_hash(self) -> str:
        return stable_hash(asdict(self))


VARIANTS = (
    VariantDefinition("A", "SMC_A_SWEEP", "1.0.0", "SMC", "Sweep only",
                      ("sweep", "closed_reclaim")),
    VariantDefinition("B", "SMC_B_SWEEP_SESSION", "1.0.0", "SMC",
                      "Sweep only + session attribution",
                      ("sweep", "closed_reclaim"), ("session",)),
    VariantDefinition("C", "SMC_C_SWEEP_HTF", "1.0.0", "SMC",
                      "Sweep + real HTF gate",
                      ("sweep", "closed_reclaim", "htf_aligned"), ("htf",)),
    VariantDefinition("D", "SMC_D_SWEEP_DISPLACEMENT", "1.0.0", "SMC",
                      "Sweep + displacement gate",
                      ("sweep", "closed_reclaim", "displacement")),
    VariantDefinition("E", "SMC_E_SWEEP_FRESH_LIQUIDITY", "1.0.0", "SMC",
                      "Sweep + fresh-liquidity gate",
                      ("sweep", "closed_reclaim", "fresh_liquidity"), ("liquidity",)),
    VariantDefinition("F", "SMC_F_SWEEP_HTF_SESSION", "1.0.0", "SMC",
                      "Sweep + HTF gate + session attribution",
                      ("sweep", "closed_reclaim", "htf_aligned"), ("htf", "session")),
    VariantDefinition("G", "SMC_G_EXISTING_FULL", "1.0.0", "SMC",
                      "Existing full SMC AND-stack", ("full_smc_ready",)),
    VariantDefinition("H", "PA_H_SR_REJECTION", "1.0.0", "PA",
                      "PA support/resistance rejection", ("pa_sr_rejection",)),
    VariantDefinition("I", "PA_I_FLIP_RETEST", "1.0.0", "PA",
                      "PA flip retest", ("pa_flip_retest",)),
)
BY_KEY = {row.key: row for row in VARIANTS}


def registry_payload(research_config: dict | None = None) -> dict:
    config = dict(research_config or {})
    rows = [{**asdict(row), "definition_hash": row.definition_hash,
             "config_hash": stable_hash({"definition": asdict(row), "config": config})}
            for row in VARIANTS]
    return {
        "registry_version": REGISTRY_VERSION,
        "execution_class": "SHADOW",
        "automatic_optimization": False,
        "real_paper_behavior_changed": False,
        "research_config": config,
        "variants": rows,
    }


def _blocker(variant: VariantDefinition, features: dict) -> str:
    if not features.get("market_data_fresh", True):
        return "MARKET_DATA_STALE"
    if variant.key in {"A", "B", "C", "D", "E", "F"}:
        if not features.get("sweep"):
            return "NO_SETUP"
        if not features.get("closed_reclaim"):
            return "RECLAIM_FAILED"
        if "htf_aligned" in variant.required_features and not features.get("htf_aligned"):
            return "HTF_MISALIGNED"
        if "displacement" in variant.required_features and not features.get("displacement"):
            return "GATE_REJECTED"
        if "fresh_liquidity" in variant.required_features and not features.get("fresh_liquidity"):
            return "ZONE_NOT_FRESH"
        return "SETUP_FOUND"
    if variant.key == "G":
        return "SETUP_FOUND" if features.get("full_smc_ready") else "SMC_CONDITION_MISSING"
    if variant.key == "H":
        return "SETUP_FOUND" if features.get("pa_sr_rejection") else "NO_SETUP"
    if variant.key == "I":
        return "SETUP_FOUND" if features.get("pa_flip_retest") else "NO_SETUP"
    raise KeyError(variant.key)


class ShadowVariantRunner:
    """Journal all frozen variants from one shared source snapshot."""

    def __init__(self, store: ShadowResearchStore, *, research_config: dict | None = None):
        self.store = store
        self.research_config = dict(research_config or {})
        self.registry = registry_payload(self.research_config)

    def evaluate(self, *, candle_id: str, snapshot_lineage: str,
                 decision_timestamp: datetime | str, features: dict,
                 variants: tuple[str, ...] | None = None) -> list[dict]:
        if stable_hash(features) != snapshot_lineage:
            raise ValueError("snapshot lineage does not match the shared feature projection")
        selected = variants or tuple(row.key for row in VARIANTS)
        results = []
        for variant_key in selected:
            variant = BY_KEY[variant_key]
            config_hash = stable_hash({
                "registry_version": REGISTRY_VERSION,
                "definition": asdict(variant),
                "research_config": self.research_config,
            })
            blocker = _blocker(variant, features)
            direction = features.get("direction")
            decision = self.store.record_decision(
                engine=variant.engine,
                account_id=f"shadow:{variant.engine.lower()}:{variant.key}",
                strategy_id=variant.strategy_id,
                strategy_version=variant.strategy_version,
                config_hash=config_hash, candle_id=candle_id,
                action_class="ENTRY", direction=direction, blocker=blocker,
                decision_timestamp=decision_timestamp,
                snapshot_lineage=snapshot_lineage,
                context={"features": features, "variant": asdict(variant),
                         "research_config": self.research_config},
            )
            order = None
            # A gated setup is still followed counterfactually. NO_SETUP has
            # no defensible direction or price plan and therefore no order.
            if direction and blocker not in {"NO_SETUP", "MARKET_DATA_STALE"} and all(
                    features.get(name) is not None for name in ("entry", "stop_loss", "take_profit")):
                order = self.store.record_order(
                    decision["decision_id"], symbol=str(features.get("symbol") or ""),
                    order_type=str(features.get("order_type") or "market"),
                    side="buy" if direction in {"bullish", "long", "buy"} else "sell",
                    requested_price=float(features["entry"]),
                    stop_loss=float(features["stop_loss"]),
                    take_profit=float(features["take_profit"]), quantity=1,
                    status="INTENT" if blocker == "SETUP_FOUND" else "SHADOW_REJECTED_INTENT",
                )
            results.append({"variant": variant.key, "decision": decision, "order": order})
        return results
