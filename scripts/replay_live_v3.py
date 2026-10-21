"""Replay selected V3 policies against one incident's recorded public capture."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rca_live_v3.assets import Assets
from rca_live_v3.controller import investigate
from rca_investigation.backends.replay import ReplayBackend
from rca_investigation.contracts_v2 import decode_record
from rca_investigation.dataset import atomic_json
from rca_investigation.schedule_v3 import SCENARIOS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--incident", required=True)
    parser.add_argument("--policies", required=True, help="comma-separated policy IDs")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.incident)
    receipt = json.loads((root / "receipt.json").read_text())
    public = decode_record(json.loads((root / "public.json").read_text()), "public")
    packets = tuple(decode_record(row, "packet") for row in json.loads((root / "responses.json").read_text()))
    assets = Assets(args.bundle)
    truth = sorted(cause for cause, _ in SCENARIOS[receipt["case"]])
    rows = {}
    for name in dict.fromkeys(args.policies.split(",")):
        try:
            result = investigate(assets, name, public, ReplayBackend(packets, assets.specs))
            decision = result["decision"]
            result["exact_set_correct"] = decision["decision_type"] == "DIAGNOSE" and sorted(decision["cause_ids"]) == truth
            rows[name] = result
        except Exception as exc:
            rows[name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    atomic_json(args.output, {"capture_id": receipt["id"], "case": receipt["case"], "ground_truth": truth, "policies": rows})


if __name__ == "__main__":
    main()
