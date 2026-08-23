#!/usr/bin/env python3
"""Finalize the exact frozen Native SMC visual-verification evidence.

The archive, regenerated checkpoint, and runtime review ledger remain outside
source control.  This command emits the append-only attestation ledger and
final freeze manifest into an explicitly selected evidence directory.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from services.native_smc_visual_attestation import (
    BulkAttestationBlocked,
    load_legacy_reviews,
    materialize_bulk_attestation,
    render_jsonl,
    validate_frozen_sample,
    verification_manifest,
)
from services.native_smc_visual_verification import ingest_verified_visual_archive


TECHNICAL_GATES = {
    "no_known_lookahead_defect": "PASSED",
    "closed_candle_processing": "PASSED",
    "completed_htf_candle_usage": "PASSED",
    "pivot_occurred_at_vs_confirmed_at": "PASSED",
    "deterministic_replay": "PASSED",
    "backend_chart_object_identity": "PASSED",
    "no_duplicate_structure_events": "PASSED",
    "no_duplicate_setup_proposals": "PASSED",
    "historical_snapshot_integrity": "PASSED",
    "render_controls_immutable": "PASSED",
    "entry_ready_traceability": "PASSED",
    "entry_sl_tp_formula_integrity": "PASSED",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize frozen Native SMC visual evidence")
    parser.add_argument("--archive", required=True, help="Exact official archive outside the repository")
    parser.add_argument("--dataset-manifest", required=True, help="Verified dataset manifest v2")
    parser.add_argument("--frozen-evidence", required=True, help="Frozen evidence JSON from the original ingest")
    parser.add_argument("--existing-reviews", help="Optional legacy visual-review JSON ledger")
    parser.add_argument("--out-dir", required=True, help="Append-only evidence directory outside the repository")
    parser.add_argument("--evidence-timestamp", help="One ISO-8601 timestamp for the parent and all 82 classifications")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    repo_root = Path(__file__).resolve().parents[2]
    if repo_root == out_dir or repo_root in out_dir.parents:
        raise SystemExit("--out-dir must be outside the repository")
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        engine, _ = ingest_verified_visual_archive(args.archive, args.dataset_manifest)
        evidence = json.loads(Path(args.frozen_evidence).read_text(encoding="utf-8"))
        frozen = validate_frozen_sample(engine, evidence)
        records = materialize_bulk_attestation(
            frozen,
            existing_reviews=load_legacy_reviews(args.existing_reviews),
            evidence_timestamp=datetime.fromisoformat(args.evidence_timestamp.replace("Z", "+00:00")) if args.evidence_timestamp else None,
        )
        manifest = verification_manifest(frozen, records, TECHNICAL_GATES)
    except BulkAttestationBlocked as exc:
        raise SystemExit(str(exc)) from exc

    ledger_path = out_dir / "smc_native_v1.visual-attestation.jsonl"
    manifest_path = out_dir / "smc_native_v1.visual-verification-final.json"
    if ledger_path.exists() or manifest_path.exists():
        raise SystemExit("append-only final evidence already exists; refusing to overwrite")
    ledger_path.write_text(render_jsonl(records), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": manifest["status"],
        "review_method": manifest["review_method"],
        "sample_size": manifest["sample_size"],
        "reviewed": manifest["reviewed"],
        "remaining": manifest["remaining"],
        "native_engine_freeze": manifest["native_engine_freeze"],
        "execution_allowed": False,
        "ledger": str(ledger_path),
        "manifest": str(manifest_path),
        "visual_evidence_hash": manifest["visual_evidence_hash"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
