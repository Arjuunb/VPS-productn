from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from bot.types import Bar
from services.native_smc import SMCConfig, SMCMarketStructureEngine, VisualReview
from services.native_smc_visual_verification import (
    DatasetProvenanceError,
    component_agreement,
    deterministic_review_sample,
    frozen_evidence_hash,
    load_verified_march_archive,
)

UTC = timezone.utc


def _verified_archive(tmp_path: Path) -> tuple[Path, Path]:
    """A full March-shaped fixture validates the adapter, never research claims."""
    start = datetime(2025, 3, 1, tzinfo=UTC)
    rows = []
    for index in range(8_928):
        stamp = start + timedelta(minutes=5 * index)
        # Binance Vision currently publishes microsecond timestamps.
        epoch_us = int(stamp.timestamp() * 1_000_000)
        rows.append(f"{epoch_us},100,102,99,101,4,0,0,0,0,0,0")
    payload = ("\n".join(rows) + "\n").encode()
    payload_name = "BTCUSDT-5m-2025-03.csv"
    archive = tmp_path / "BTCUSDT-5m-2025-03.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as zipped:
        zipped.writestr(payload_name, payload)
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = {
        "manifest_status": "VERIFIED",
        "dataset": {
            "archive_filename": archive.name,
            "archive_sha256": archive_hash,
            "official_checksum_sha256": archive_hash,
            "payload_filename": payload_name,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "period_start": "2025-03-01T00:00:00+00:00",
            "period_end": "2025-03-31T23:55:00+00:00",
            "expected_rows": 8_928,
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return archive, manifest_path


def test_verified_official_layout_accepts_microsecond_rows_and_full_integrity(tmp_path):
    archive, manifest = _verified_archive(tmp_path)
    rows, integrity = load_verified_march_archive(archive, manifest)
    assert len(rows) == 8_928
    assert rows[0].timestamp == datetime(2025, 3, 1, tzinfo=UTC)
    assert integrity.duplicate_count == integrity.gap_count == integrity.malformed_count == 0


def test_payload_or_archive_hash_mismatch_is_a_provenance_failure(tmp_path):
    archive, manifest_path = _verified_archive(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["dataset"]["payload_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(DatasetProvenanceError, match="payload SHA-256"):
        load_verified_march_archive(archive, manifest_path)


def test_review_sampling_is_deterministic_and_does_not_change_native_state():
    engine = SMCMarketStructureEngine(SMCConfig("BTCUSDT"))
    for index in range(60):
        stamp = datetime(2025, 3, 1, tzinfo=UTC) + timedelta(minutes=5 * index)
        engine.process_closed_bar(Bar(stamp, 100, 102, 99, 101, 1))
    before = engine.checkpoint()
    first = deterministic_review_sample(engine)
    second = deterministic_review_sample(engine)
    assert first == second
    assert engine.checkpoint() == before


def test_agreement_excludes_ambiguous_labels_and_evidence_hash_is_stable():
    created = datetime(2025, 3, 1, tzinfo=UTC)
    rows = [
        VisualReview("1", "SMC_NATIVE_V1_RESEARCH", "BTCUSDT", "5m", "x", "pivot", "CORRECT", None, None, None, None, created),
        VisualReview("2", "SMC_NATIVE_V1_RESEARCH", "BTCUSDT", "5m", "y", "pivot", "AMBIGUOUS", None, None, None, None, created),
        VisualReview("3", "SMC_NATIVE_V1_RESEARCH", "BTCUSDT", "5m", "z", "pivot", "INCORRECT", None, None, None, None, created),
    ]
    assert component_agreement(rows)["pivot"] == {"reviewed": 3, "correct": 1, "incorrect": 1, "ambiguous": 1, "agreement_pct": 50.0}
    payload = {"record": "frozen", "evidence_content_sha256": "old"}
    assert frozen_evidence_hash(payload) == frozen_evidence_hash({"record": "frozen"})
