"""Verified-data utilities for Native SMC visual review, never performance research.

Raw exchange archives and checkpoints belong outside source control.  This
module verifies a frozen manifest before handing any closed candle to the
native engine's normal authoritative-input boundary.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile

from bot.types import Bar
from services.native_smc import (
    FairValueGap, LiquiditySweep, OrderBlock, SMCConfig, SMCMarketStructureEngine,
    StructureEvent, VisualReview,
)


class DatasetProvenanceError(ValueError):
    """A frozen manifest or archive cannot be trusted for research review."""


@dataclass(frozen=True)
class ArchiveIntegrity:
    archive_filename: str
    archive_sha256: str
    payload_filename: str
    payload_sha256: str
    byte_size: int
    row_count: int
    first_timestamp: datetime
    last_timestamp: datetime
    duplicate_count: int
    gap_count: int
    malformed_count: int
    incomplete_count: int


@dataclass(frozen=True)
class ReviewSampleItem:
    object_id: str
    category: str
    timestamp: datetime
    setup_id: str | None = None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_hash(payload: dict) -> str:
    return _sha256(json.dumps(payload, default=lambda value: value.isoformat(), sort_keys=True, separators=(",", ":")).encode())


def load_verified_march_archive(archive_path: str | Path, manifest_path: str | Path) -> tuple[list[Bar], ArchiveIntegrity]:
    """Verify the exact v2 archive, then return only valid March 2025 closed bars."""
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get("manifest_status") != "VERIFIED":
        raise DatasetProvenanceError("dataset manifest is not VERIFIED")
    expected = manifest["dataset"]
    archive = Path(archive_path)
    archive_bytes = archive.read_bytes()
    archive_hash = _sha256(archive_bytes)
    if archive.name != expected["archive_filename"]:
        raise DatasetProvenanceError("archive filename does not match frozen manifest")
    if archive_hash != expected["archive_sha256"]:
        raise DatasetProvenanceError("archive SHA-256 does not match frozen manifest")
    if expected["archive_sha256"] != expected["official_checksum_sha256"]:
        raise DatasetProvenanceError("frozen manifest does not preserve official checksum agreement")

    with ZipFile(io.BytesIO(archive_bytes)) as zipped:
        names = zipped.namelist()
        if names != [expected["payload_filename"]]:
            raise DatasetProvenanceError("archive payload filename does not match frozen manifest")
        payload = zipped.read(names[0])
    payload_hash = _sha256(payload)
    expected_payload_hash = expected.get("payload_sha256")
    if expected_payload_hash and payload_hash != expected_payload_hash:
        raise DatasetProvenanceError("archive payload SHA-256 does not match frozen manifest")

    start = datetime.fromisoformat(expected["period_start"])
    end = datetime.fromisoformat(expected["period_end"])
    rows: list[Bar] = []
    malformed = 0
    seen: set[datetime] = set()
    duplicates = 0
    for raw in csv.reader(io.StringIO(payload.decode("utf-8-sig"))):
        if len(raw) < 6:
            malformed += 1
            continue
        try:
            # Binance Vision moved its monthly kline exports to microsecond
            # timestamps.  Accept only the two documented epoch units so a
            # malformed timestamp can never silently become a different bar.
            epoch = int(raw[0])
            divisor = 1_000_000 if epoch >= 100_000_000_000_000 else 1_000
            stamp = datetime.fromtimestamp(epoch / divisor, tz=timezone.utc)
            values = [float(raw[index]) for index in (1, 2, 3, 4, 5)]
            item = Bar(stamp, values[0], values[1], values[2], values[3], values[4])
        except (TypeError, ValueError, OverflowError):
            malformed += 1
            continue
        if stamp in seen:
            duplicates += 1
            continue
        seen.add(stamp)
        if not (start <= stamp <= end):
            malformed += 1
            continue
        if (
            not all(math.isfinite(value) for value in values)
            or min(item.open, item.high, item.low, item.close) <= 0
            or item.high < max(item.open, item.close, item.low)
            or item.low > min(item.open, item.close, item.high)
            or item.volume < 0
        ):
            malformed += 1
            continue
        rows.append(item)

    rows.sort(key=lambda row: row.timestamp)
    gaps = sum(1 for left, right in zip(rows, rows[1:]) if right.timestamp - left.timestamp != timedelta(minutes=5))
    expected_rows = int(expected["expected_rows"])
    if malformed or duplicates or gaps or len(rows) != expected_rows:
        raise DatasetProvenanceError(f"archive integrity failed: rows={len(rows)} malformed={malformed} duplicates={duplicates} gaps={gaps}")
    if not rows or rows[0].timestamp != start or rows[-1].timestamp != end:
        raise DatasetProvenanceError("archive timestamps do not match frozen March boundary")

    integrity = ArchiveIntegrity(
        archive_filename=archive.name, archive_sha256=archive_hash,
        payload_filename=expected["payload_filename"], payload_sha256=payload_hash,
        byte_size=len(archive_bytes), row_count=len(rows), first_timestamp=rows[0].timestamp,
        last_timestamp=rows[-1].timestamp, duplicate_count=duplicates, gap_count=gaps,
        malformed_count=malformed, incomplete_count=0,
    )
    return rows, integrity


def ingest_verified_visual_archive(archive_path: str | Path, manifest_path: str | Path) -> tuple[SMCMarketStructureEngine, ArchiveIntegrity]:
    """Use the engine's authoritative closed-bar input, never direct injection."""
    bars, integrity = load_verified_march_archive(archive_path, manifest_path)
    engine = SMCMarketStructureEngine(SMCConfig("BTCUSDT", timeframe="5m"))
    snapshots = engine.ingest_authoritative_closed_bars(
        bars, timeframe_seconds=300, now=integrity.last_timestamp + timedelta(minutes=5)
    )
    if len(snapshots) != integrity.row_count:
        raise DatasetProvenanceError("authoritative closed-bar boundary excluded verified historical candles")
    return engine, integrity


def deterministic_review_sample(engine: SMCMarketStructureEngine, *, seed: str = "SMC_NATIVE_V1_RESEARCH:BTCUSDT:2025-03", target: int = 80) -> list[ReviewSampleItem]:
    """Stratified hash-order sample; no chart appearance can affect selection."""
    candidates: list[ReviewSampleItem] = []
    candidates += [ReviewSampleItem(row.id, f"{row.scope}_pivot", row.confirmed_at) for row in engine.pivots.values()]
    for row in engine.events.values():
        if isinstance(row, StructureEvent):
            candidates.append(ReviewSampleItem(row.id, f"{row.scope}_{row.event_type.lower()}", row.confirmed_at))
        elif isinstance(row, LiquiditySweep):
            candidates.append(ReviewSampleItem(row.id, "liquidity_sweep", row.timestamp))
    candidates += [ReviewSampleItem(row.id, "fvg_mitigated" if row.mitigated else "fvg_active", row.created_at) for row in engine.fvgs.values()]
    candidates += [ReviewSampleItem(row.id, "ob_mitigated" if row.mitigated else "ob_active", row.created_at) for row in engine.obs.values()]
    candidates += [ReviewSampleItem(row.id, f"setup_{row.phase.value.lower()}", row.created_at, row.id) for row in engine.setups.values()]
    setup_times = {
        row.id: (row.transitions[-1].timestamp if row.transitions else row.created_at)
        for row in engine.setups.values()
    }
    candidates += [
        ReviewSampleItem(
            row.id,
            "proposed_trade",
            setup_times.get(row.setup_id, engine.bars[-1].timestamp),
            row.setup_id,
        )
        for row in engine.proposals.values()
    ]

    def ranking(item: ReviewSampleItem) -> str:
        return _sha256(f"{seed}|{item.category}|{item.object_id}".encode())

    grouped: dict[str, list[ReviewSampleItem]] = {}
    for item in candidates:
        grouped.setdefault(item.category, []).append(item)
    selected: list[ReviewSampleItem] = []
    ids: set[str] = set()
    for category in sorted(grouped):
        for item in sorted(grouped[category], key=ranking)[:6]:
            if item.object_id not in ids:
                selected.append(item); ids.add(item.object_id)
    for item in sorted(candidates, key=ranking):
        if len(selected) >= target:
            break
        if item.object_id not in ids:
            selected.append(item); ids.add(item.object_id)
    return sorted(selected, key=lambda item: (item.timestamp, item.object_id))


def component_agreement(reviews: list[VisualReview]) -> dict[str, dict[str, int | float | None]]:
    """Report ambiguity separately; agreement uses only decisive human labels."""
    groups: dict[str, list[VisualReview]] = {}
    for review in reviews:
        groups.setdefault(review.component, []).append(review)
    result: dict[str, dict[str, int | float | None]] = {}
    for component, rows in sorted(groups.items()):
        correct = sum(row.classification == "CORRECT" for row in rows)
        incorrect = sum(row.classification == "INCORRECT" for row in rows)
        ambiguous = sum(row.classification == "AMBIGUOUS" for row in rows)
        decisive = correct + incorrect
        result[component] = {"reviewed": len(rows), "correct": correct, "incorrect": incorrect, "ambiguous": ambiguous, "agreement_pct": round(100 * correct / decisive, 2) if decisive else None}
    return result


def frozen_evidence_hash(payload: dict) -> str:
    """Stable hash for a finished evidence payload, excluding its own hash field."""
    copy = dict(payload)
    copy.pop("evidence_content_sha256", None)
    return _canonical_hash(copy)


def review_run_payload(engine: SMCMarketStructureEngine, integrity: ArchiveIntegrity, sample: list[ReviewSampleItem]) -> dict:
    """Evidence summary without profitability statistics or trade execution."""
    payload = {
        "research_id": "SMC_NATIVE_V1_RESEARCH", "execution_allowed": False,
        "integrity": asdict(integrity), "native_object_counts": {
            "pivots": len(engine.pivots), "events": len(engine.events), "fvgs": len(engine.fvgs),
            "order_blocks": len(engine.obs), "setups": len(engine.setups), "proposals": len(engine.proposals),
        },
        "review_sample": [asdict(item) for item in sample],
        "review_sample_definition": {"algorithm": "stratified deterministic SHA-256 ordering", "seed": "SMC_NATIVE_V1_RESEARCH:BTCUSDT:2025-03", "target": 80},
        "human_reviews": [], "component_agreement": {},
    }
    payload["evidence_content_sha256"] = frozen_evidence_hash(payload)
    return payload
