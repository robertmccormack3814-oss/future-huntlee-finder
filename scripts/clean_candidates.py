from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data.json"
ALERT_PATH = ROOT / "latest_alert.json"

HEADLINE_TERMS = (
    "first group", "will help", "community urged", "give feedback", "government investing",
    "new planning rules", "housing crisis", "shared responsibility", "fast track",
    "announcement", "planning reforms", "deliver homes", "help build homes"
)


def is_bad_candidate(c: dict) -> bool:
    source = (c.get("source") or "").lower()
    name = (c.get("name") or "").strip().lower()
    # News articles often aggregate multiple precincts and produce misleading project names/counts.
    if "/news/" in source:
        return True
    # Backstop for headline-like records that may come from other paths.
    if len(name) > 70 and any(term in name for term in HEADLINE_TERMS):
        return True
    if any(term in name for term in HEADLINE_TERMS):
        return True
    return False


def main() -> None:
    db = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    before = len(db.get("candidates", []))
    kept = [c for c in db.get("candidates", []) if not is_bad_candidate(c)]
    db["candidates"] = kept
    DATA_PATH.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if ALERT_PATH.exists():
        alert = json.loads(ALERT_PATH.read_text(encoding="utf-8"))
        if isinstance(alert, dict) and isinstance(alert.get("alerts"), list):
            alert["alerts"] = [c for c in alert["alerts"] if not is_bad_candidate(c)]
            ALERT_PATH.write_text(json.dumps(alert, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Removed {before - len(kept)} headline/news candidates; {len(kept)} remain")


if __name__ == "__main__":
    main()
