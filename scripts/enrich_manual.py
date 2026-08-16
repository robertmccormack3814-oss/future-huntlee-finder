import json
from datetime import datetime, timezone
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "data.json"
db = json.loads(p.read_text())

for c in db.get("candidates", []):
    if c.get("name") in {"Caboolture West", "Waraba", "Waraba (Caboolture West)"} or "caboolture-west" in c.get("source", ""):
        c.update({
            "name": "Waraba (Caboolture West)",
            "subtitle": "New regional city planned for Moreton Bay with major funded infrastructure rollout",
            "state": "QLD",
            "region": "Moreton Bay",
            "stage": "Planned / infrastructure funded",
            "status": "HIGH CONVICTION",
            "homes": 30000,
            "population": 70000,
            "jobs": 17000,
            "score": 91,
            "scores": {
                "scale": 10,
                "infrastructure": 10,
                "schools": 9,
                "retail": 9,
                "transport": 8,
                "employment": 9,
                "earliness": 10,
                "certainty": 8
            },
            "commercial_infrastructure": [
                "Town centre / major centre",
                "Shopping centre / retail precinct",
                "Local / neighbourhood centres",
                "Commercial / employment precinct",
                "Industrial / logistics precinct",
                "Community / civic facilities"
            ],
            "highlights": [
                "~30,000 homes and ~70,000 future residents over the long-term rollout",
                "~17,000 local jobs planned across retail, commercial, industrial and other employment areas",
                ">$2 billion proposed/funded infrastructure program including trunk roads, water, sewer, parks and community facilities",
                "Primary and secondary schools planned alongside community hubs and sporting facilities",
                "Caboolture River Road enabling works underway, with major funded upgrade construction scheduled from 2027",
                "Very early city-scale opportunity: first housing is emerging while the wider Waraba build-out remains decades from completion"
            ],
            "risk": "Very large future housing supply may limit scarcity. Major transport projects have different commitment levels, and infrastructure timing can lag development. Prefer early, well-connected precincts and scarce lots rather than undifferentiated greenfield stock.",
            "last_checked": datetime.now(timezone.utc).date().isoformat(),
            "discovery": "tracked-manual-enrichment"
        })
        break
else:
    db.setdefault("candidates", []).append({
        "name": "Waraba (Caboolture West)", "subtitle": "New regional city planned for Moreton Bay with major funded infrastructure rollout",
        "state": "QLD", "region": "Moreton Bay", "stage": "Planned / infrastructure funded", "status": "HIGH CONVICTION",
        "homes": 30000, "population": 70000, "jobs": 17000, "score": 91,
        "scores": {"scale":10,"infrastructure":10,"schools":9,"retail":9,"transport":8,"employment":9,"earliness":10,"certainty":8},
        "commercial_infrastructure": ["Town centre / major centre","Shopping centre / retail precinct","Local / neighbourhood centres","Commercial / employment precinct","Industrial / logistics precinct","Community / civic facilities"],
        "highlights": ["~30,000 homes and ~70,000 future residents","~17,000 local jobs planned",">$2 billion infrastructure program","Schools, centres, parks and community facilities planned","Caboolture River Road upgrades progressing","Early city-scale opportunity"],
        "risk": "Large future housing supply and infrastructure timing are key risks; favour scarce, well-connected early precincts.",
        "source": "https://www.planning.qld.gov.au/planning-issues-and-interests/seq-land-supply/caboolture-west",
        "last_checked": datetime.now(timezone.utc).date().isoformat(), "discovery": "tracked-manual-enrichment"
    })

db["candidates"].sort(key=lambda x: x.get("score", 0), reverse=True)
db["updated"] = datetime.now(timezone.utc).date().isoformat()
p.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n")
print("Waraba enrichment applied")
