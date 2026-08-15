from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data.json"
SOURCES_PATH = ROOT / "scanner_sources.json"
ALERT_PATH = ROOT / "latest_alert.json"

UA = "FutureHuntleeFinder/1.0 (+GitHub Actions; public planning research)"
TIMEOUT = 20
MAX_DISCOVERY_PAGES = 120
MIN_HOMES = 5000
MIN_INFRA_CATEGORIES = 3
ALERT_SCORE = 75

URL_HINTS = (
    "growth", "precinct", "greenfield", "release", "masterplan", "master-plan",
    "structure-plan", "structureplan", "housing", "new-community", "urban-development"
)

CATEGORY_TERMS = {
    "schools": ("school", "education", "primary school", "high school"),
    "retail": ("town centre", "shopping centre", "retail", "local centre", "neighbourhood centre"),
    "transport": ("rail", "metro", "station", "bus", "transport", "motorway", "highway", "road upgrade"),
    "employment": ("jobs", "employment", "business park", "industrial", "commercial centre"),
    "parks": ("park", "open space", "sporting", "recreation", "community facility"),
    "utilities": ("water", "wastewater", "sewer", "electricity", "infrastructure contribution")
}

COMMERCIAL_INFRA_TERMS = {
    "Town centre / major centre": ("town centre", "city centre", "major centre", "metropolitan centre"),
    "Shopping centre / retail precinct": ("shopping centre", "retail centre", "retail precinct", "retail hub"),
    "Local / neighbourhood centres": ("local centre", "neighbourhood centre", "neighborhood centre", "village centre"),
    "Supermarket / grocery retail": ("supermarket", "grocery", "food retail"),
    "Commercial / employment precinct": ("commercial precinct", "employment precinct", "employment land", "commercial centre", "commercial core"),
    "Business park / office precinct": ("business park", "office precinct", "office space", "business precinct"),
    "Industrial / logistics precinct": ("industrial precinct", "industrial land", "logistics precinct", "warehouse", "freight precinct"),
    "Health / medical facilities": ("health centre", "medical centre", "health precinct", "medical precinct", "health services"),
    "Hospital": ("hospital",),
    "Hospitality / accommodation": ("hotel", "hospitality", "accommodation precinct"),
    "Childcare / early learning": ("childcare", "child care", "early learning centre"),
    "Community / civic facilities": ("community centre", "community facility", "civic centre", "library")
}

STATUS_WORDS = {
    "under construction": 10,
    "construction": 9,
    "contract awarded": 9,
    "funded": 9,
    "approved": 8,
    "rezoned": 8,
    "adopted": 8,
    "structure plan": 7,
    "master plan": 7,
    "planning proposal": 6,
    "draft": 5,
    "investigation": 4,
    "future": 3,
}


def fetch(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.text


def clean_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = " ".join(soup.stripped_strings)
    return title[:160], re.sub(r"\s+", " ", text)


def extract_urls_from_sitemap(xml: str) -> list[str]:
    soup = BeautifulSoup(xml, "xml")
    return [loc.get_text(strip=True) for loc in soup.find_all("loc")]


def sitemap_candidates(url: str) -> list[str]:
    try:
        first = fetch(url)
    except Exception:
        return []
    urls = extract_urls_from_sitemap(first)
    nested = [u for u in urls if u.endswith(".xml") or "sitemap" in u.lower()]
    pages = []
    if nested:
        for sm in nested[:30]:
            try:
                pages.extend(extract_urls_from_sitemap(fetch(sm)))
            except Exception:
                continue
    else:
        pages = urls
    out = []
    for u in pages:
        lu = u.lower()
        if any(h in lu for h in URL_HINTS):
            out.append(u)
    return out


def linked_candidates(seed_url: str) -> list[str]:
    try:
        html = fetch(seed_url)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(seed_url).netloc
    out = []
    for a in soup.find_all("a", href=True):
        u = urljoin(seed_url, a["href"]).split("#")[0]
        if urlparse(u).netloc != host:
            continue
        blob = (u + " " + a.get_text(" ", strip=True)).lower()
        if any(h.replace("-", " ") in blob.replace("-", " ") for h in URL_HINTS):
            out.append(u)
    return out


def extract_largest_home_count(text: str) -> int | None:
    patterns = [
        r"(?:up to|around|approximately|about|more than|over)?\s*([\d,]{4,})\s+(?:new\s+)?(?:homes|dwellings|lots|residences)",
        r"(?:homes|dwellings|lots|residences)\s*(?:of|for|:)?\s*(?:up to|around|approximately|about|more than|over)?\s*([\d,]{4,})",
    ]
    vals = []
    for p in patterns:
        for m in re.finditer(p, text, flags=re.I):
            try:
                vals.append(int(m.group(1).replace(",", "")))
            except ValueError:
                pass
    return max(vals) if vals else None


def extract_largest_number_before(text: str, noun_pattern: str) -> int | None:
    vals = []
    for m in re.finditer(rf"([\d,]{{3,}})\s+(?:new\s+)?(?:{noun_pattern})", text, flags=re.I):
        try:
            vals.append(int(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return max(vals) if vals else None


def category_hits(text: str) -> dict[str, bool]:
    lt = text.lower()
    return {k: any(term in lt for term in terms) for k, terms in CATEGORY_TERMS.items()}


def extract_commercial_infrastructure(text: str) -> list[str]:
    lt = text.lower()
    found = []
    for label, terms in COMMERCIAL_INFRA_TERMS.items():
        if any(term in lt for term in terms):
            found.append(label)
    return found[:8]


def infer_stage(text: str) -> tuple[str, int]:
    lt = text.lower()
    best_label, best = "Planning pipeline", 4
    for label, score in STATUS_WORDS.items():
        if label in lt and score > best:
            best_label, best = label.title(), score
    return best_label, best


def score_candidate(homes: int, hits: dict[str, bool], stage_score: int, text: str) -> tuple[dict, int]:
    scale = 10 if homes >= 30000 else 9 if homes >= 15000 else 8 if homes >= 10000 else 7 if homes >= 5000 else 5
    infrastructure = min(10, 4 + sum(hits.values()))
    schools = 9 if hits["schools"] else 4
    retail = 8 if hits["retail"] else 4
    transport = 9 if hits["transport"] else 4
    employment = 9 if hits["employment"] else 4
    certainty = stage_score
    earliness = 10 if stage_score <= 5 else 8 if stage_score <= 7 else 6 if stage_score <= 8 else 4
    scores = {
        "scale": scale,
        "infrastructure": infrastructure,
        "schools": schools,
        "retail": retail,
        "transport": transport,
        "employment": employment,
        "earliness": earliness,
        "certainty": certainty,
    }
    weights = {"scale":18,"infrastructure":18,"schools":10,"retail":10,"transport":12,"employment":12,"earliness":10,"certainty":10}
    total = round(sum(scores[k] / 10 * w for k, w in weights.items()))
    return scores, total


def short_name(title: str, url: str) -> str:
    t = re.sub(r"\s*[-|–].*$", "", title).strip()
    if len(t) >= 5:
        return t[:100]
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").title()[:100]


def derive_region(text: str, state: str) -> str:
    for marker in ("Greater Macarthur", "Western Sydney", "Moreton Bay", "South East Queensland", "Ballarat", "Geelong", "Melbourne"):
        if marker.lower() in text.lower():
            return marker
    return state


def summarise_highlights(homes: int, jobs: int | None, pop: int | None, hits: dict[str, bool], stage: str) -> list[str]:
    h = [f"At least {homes:,} planned homes/dwellings detected in official planning text", f"Planning stage signal: {stage}"]
    if pop: h.append(f"Population signal: approximately {pop:,} people")
    if jobs: h.append(f"Employment signal: approximately {jobs:,} jobs")
    labels = {"schools":"schools/education", "retail":"town or shopping centres", "transport":"major transport/road investment", "employment":"employment land/jobs", "parks":"parks/community infrastructure", "utilities":"major utilities/infrastructure servicing"}
    present = [labels[k] for k,v in hits.items() if v]
    if present: h.append("Infrastructure signals: " + ", ".join(present))
    return h[:5]


def normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def send_email(alerts: list[dict]) -> None:
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    to_addr = os.getenv("ALERT_EMAIL")
    if not all([host, user, password, to_addr]) or not alerts:
        return
    port = int(os.getenv("SMTP_PORT", "465"))
    msg = EmailMessage()
    msg["Subject"] = f"Future Huntlee Finder: {len(alerts)} promising update(s)"
    msg["From"] = user
    msg["To"] = to_addr
    lines = ["Future Huntlee Finder found promising planning updates:\n"]
    for c in alerts:
        lines += [f"{c['name']} ({c['state']}) — {c['score']}/100", f"Homes: {c.get('homes','—')}", f"Stage: {c.get('stage','—')}", c.get("source", ""), ""]
    msg.set_content("\n".join(lines))
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=context) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


def main() -> None:
    db = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    existing = {normalise(c["name"]): c for c in db["candidates"]}
    existing_urls = {c.get("source") for c in db["candidates"]}

    discovered_urls: list[tuple[str,str]] = []
    for sm in sources["sitemaps"]:
        for u in sitemap_candidates(sm["url"]):
            discovered_urls.append((sm["state"], u))
    for seed in sources["seed_pages"]:
        for u in linked_candidates(seed["url"]):
            discovered_urls.append((seed["state"], u))

    dedup = []
    seen = set()
    for state, u in discovered_urls:
        if u not in seen:
            seen.add(u)
            dedup.append((state, u))
    dedup = dedup[:MAX_DISCOVERY_PAGES]

    alerts = []
    scan_log = []
    for state, url in dedup:
        try:
            html = fetch(url)
            title, text = clean_text(html)
            homes = extract_largest_home_count(text)
            if not homes or homes < MIN_HOMES:
                continue
            hits = category_hits(text)
            if sum(hits.values()) < MIN_INFRA_CATEGORIES:
                continue
            stage, stage_score = infer_stage(text)
            scores, total = score_candidate(homes, hits, stage_score, text)
            jobs = extract_largest_number_before(text, r"jobs|employees")
            pop = extract_largest_number_before(text, r"people|residents|population")
            commercial = extract_commercial_infrastructure(text)
            name = short_name(title, url)
            key = normalise(name)
            candidate = {
                "name": name,
                "state": state,
                "region": derive_region(text, state),
                "stage": stage,
                "status": "HIGH CONVICTION" if total >= 85 else "WATCH CLOSELY" if total >= 75 else "EARLY WATCH",
                "homes": homes,
                "population": pop,
                "jobs": jobs,
                "commercial_infrastructure": commercial,
                "scores": scores,
                "highlights": summarise_highlights(homes, jobs, pop, hits, stage),
                "risk": "Automatically discovered from official planning material. Verify project boundaries, delivery timing, local supply, infrastructure funding and purchase price before investment decisions.",
                "source": url,
                "score": total,
                "last_checked": datetime.now(timezone.utc).date().isoformat(),
                "discovery": "automatic"
            }

            previous = existing.get(key)
            materially_changed = False
            if previous:
                materially_changed = abs(total - previous.get("score", 0)) >= 5 or homes != previous.get("homes") or stage != previous.get("stage") or commercial != previous.get("commercial_infrastructure", [])
                previous.update(candidate)
            elif url not in existing_urls:
                db["candidates"].append(candidate)
                existing[key] = candidate
                existing_urls.add(url)
                materially_changed = True

            if materially_changed and total >= ALERT_SCORE:
                alerts.append(candidate)
            scan_log.append({"name":name,"url":url,"score":total,"homes":homes})
        except Exception as e:
            scan_log.append({"url":url,"error":str(e)[:180]})

    db["updated"] = datetime.now(timezone.utc).date().isoformat()
    db["scanner"] = {
        "last_run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages_considered": len(dedup),
        "qualified_pages": len([x for x in scan_log if "score" in x]),
        "alert_count": len(alerts),
        "mode": "official-planning-autodiscovery"
    }
    db["candidates"].sort(key=lambda c: c.get("score", 0), reverse=True)
    DATA_PATH.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ALERT_PATH.write_text(json.dumps({"generated":db["scanner"]["last_run_utc"],"alerts":alerts}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    send_email(alerts)
    print(f"Scanned {len(dedup)} planning URLs; {len(alerts)} alert-worthy change(s).")


if __name__ == "__main__":
    main()
