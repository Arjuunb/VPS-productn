#!/usr/bin/env python3
"""Run the frozen Native SMC visual-review ingest outside the repository.

Example (VPS or research workstation):
  python scripts/run_native_smc_visual_verification.py \\
    --archive /secure-research/BTCUSDT-5m-2025-03.zip \\
    --manifest data/native_smc_visual_verification_manifest_v2.json \\
    --out-dir /var/lib/tradexa/native_smc_visual_verification
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.native_smc_visual_verification import (
    deterministic_review_sample, ingest_verified_visual_archive, review_run_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verified Native SMC visual-review archive ingest")
    parser.add_argument("--archive", required=True, help="Official archive outside the repository")
    parser.add_argument("--manifest", required=True, help="Versioned verified manifest")
    parser.add_argument("--out-dir", required=True, help="Checkpoint/evidence directory outside the repository")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    repo_root = Path(__file__).resolve().parents[2]
    if repo_root == out_dir or repo_root in out_dir.parents:
        raise SystemExit("--out-dir must be outside the repository")
    out_dir.mkdir(parents=True, exist_ok=True)

    engine, integrity = ingest_verified_visual_archive(args.archive, args.manifest)
    sample = deterministic_review_sample(engine)
    evidence = review_run_payload(engine, integrity, sample)
    checkpoint_path = out_dir / "smc_native_v1_btcusdt_5m_2025-03.checkpoint.json"
    sample_path = out_dir / "smc_native_v1_btcusdt_5m_2025-03.review-sample.json"
    evidence_path = out_dir / "smc_native_v1_btcusdt_5m_2025-03.evidence.json"
    checkpoint_path.write_text(json.dumps(engine.checkpoint(), default=lambda value: value.isoformat(), sort_keys=True))
    sample_path.write_text(json.dumps(evidence["review_sample"], default=lambda value: value.isoformat(), sort_keys=True, indent=2) + "\n")
    evidence_path.write_text(json.dumps(evidence, default=lambda value: value.isoformat(), sort_keys=True, indent=2) + "\n")
    print(json.dumps({
        "status": "INGESTED_FOR_VISUAL_REVIEW", "execution_allowed": False,
        "rows": integrity.row_count, "sample_size": len(sample),
        "checkpoint": str(checkpoint_path), "evidence": str(evidence_path),
        "evidence_content_sha256": evidence["evidence_content_sha256"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
