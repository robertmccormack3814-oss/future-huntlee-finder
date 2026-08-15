from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data.json"
SOURCES_PATH = ROOT / "scanner_sources.json"
ALERT_PATH = ROOT / "latest_alert.json"

UA = "FutureHuntleeFinder/2.0 (+GitHub Actions; public property-planning research)"
TIMEOUT = 18
MAX_DISCOVERY_PAGES = 120
MAX_SALES_DISCOVERY_FETCHES = 6
MIN_HOMES = 5000
MIN_INFRA_CATEGORIES = 3
ALERT_SCORE = 75

URL_HINTS = (
    "growth", "precinct", "greenfield", "release", "masterplan", "master-plan",
    "structure-plan", "structureplan", "new-community", "urban-development"
)
REJECT_PATH_PARTS = (
    "/the-planning-system/housing/", "low-and-mid-rise",
    "transport-oriented-development-program", "housing-policy"
)
GENERIC_NAME_TERMS = (
    "government", "investing", "investment", "reform", "responsibility",
    "housing crisis", "planning rules", "fast track", "program", "policy",
    "announcement", "deliver a pipeline", "new planning", "shared responsibility"
)
GOV_HOST_HINTS = (".gov.au", "planning.nsw.gov.au", "planning.qld.gov.au", "planning.vic.gov.au")
IGNORE_SALES_HOSTS = (
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "x.com",
    "realestate.com.au", "domain.com.au", "google.com", "bing.com", "duckduckgo.com"
)

CATEGORY_TERMS = {
    "schools": ("school", "education", "primary school", "high school"),
    "retail": ("town centre", "shopping centre", "retail", "local centre", "neighbourhood centre"),
    "transport": ("rail", "metro", "station", "bus", "transport", "motorway", "highway", "road upgrade"),
    "employment": ("jobs", "employment", "business park", "industrial", "commercial centre"),
    "parks": ("park", "open space", "sporting", "recreation", "community facility"),
    "utilities": ("water", "wastewater", "sewer", "electricity", "infrastructure contribution"),
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
    "Community / civic facilities": ("community centre", "community facility", "civic centre", "library"),
}
STATUS_WORDS = {
    "under construction": 10, "construction": 9, "contract awarded": 9,
    "funded": 9, "approved": 8, "rezoned": 8, "adopted": 8,
    "structure plan": 7, "master plan": 7, "planning proposal": 6,
    "draft": 5, "investigation": 4, "future": 3,
}

LAND_RELEASE_STAGES = [
    "Not released", "Expressions of interest", "Coming soon", "Pre-release / VIP",
    "Off-the-plan sales", "Available now", "Registered"
]
LAND_RELEASE_PATTERNS = [
    ("Registered", (r"\bregistered land\b", r"\blots? registered\b", r"\bregistered and ready\b", r"\btitle(?:s)? issued\b")),
    ("Available now", (r"\bland for sale\b", r"\blots? available now\b", r"\bnow selling\b", r"\bavailable lots?\b", r"\bbuy land\b", r"\bselect your lot\b")),
    ("Off-the-plan sales", (r"\boff[- ]the[- ]plan\b", r"\bsecure (?:a |your )?lot\b", r"\bcontracts? now available\b", r"\bdeposit to secure\b", r"\bsecure your homesite\b")),
    ("Pre-release / VIP", (r"\bvip release\b", r"\bpre[- ]release\b", r"\bpriority release\b", r"\bpriority access\b", r"\bexclusive release\b")),
    ("Coming soon", (r"\bcoming soon\b", r"\bland release coming\b", r"\bnew release soon\b", r"\bfuture release\b")),
    ("Expressions of interest", (r"\bexpressions? of interest\b", r"\bregister your interest\b", r"\bregister interest\b", r"\bjoin (?:our |the )?(?:waitlist|database)\b", r"\bstay informed\b")),
]
SALES_WORDS = (
    "land for sale", "now selling", "available land", "available lots", "land release",
    "new release", "stage release", "buy land", "homesites", "home sites", "price list",
    "lot plan", "sales plan", "secure your lot", "register your interest"
)
PRIORITY_WORDS = (
    "register your interest", "register interest", "join the database", "join our database",
    "priority list", "priority access", "vip", "pre-release", "waitlist", "stay informed"
)


def fetch(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA}, allow_redirects=True)
    r.raise_for_status()
    return r.text


def clean_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = re.sub(r"\s+", " ", " ".join(soup.stripped_strings))
    return title[:180], text


def extract_urls_from_sitemap(xml: str) -> list[str]:
    return [x.get_text(strip=True) for x in BeautifulSoup(xml, "xml").find_all("loc")]


def sitemap_candidates(url: str) -> list[str]:
    try:
        urls = extract_urls_from_sitemap(fetch(url))
    except Exception:
        return []
    nested = [u for u in urls if u.endswith(".xml") or "sitemap" in u.lower()]
    pages: list[str] = []
    if nested:
        for sm in nested[:30]:
            try:
                pages += extract_urls_from_sitemap(fetch(sm))
            except Exception:
                pass
    else:
        pages = urls
    return [
        u for u in pages
        if any(h in u.lower() for h in URL_HINTS)
        and not any(b in u.lower() for b in REJECT_PATH_PARTS)
    ]


def linked_candidates(seed: str) -> list[str]:
    try:
        soup = BeautifulSoup(fetch(seed), "lxml")
    except Exception:
        return []
    host = urlparse(seed).netloc
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        u = urljoin(seed, a["href"]).split("#")[0]
        blob = (u + " " + a.get_text(" ", strip=True)).lower()
        if (
            urlparse(u).netloc == host
            and any(h.replace("-", " ") in blob.replace("-", " ") for h in URL_HINTS)
            and not any(b in u.lower() for b in REJECT_PATH_PARTS)
        ):
            out.append(u)
    return out


def extract_largest_home_count(text: str) -> int | None:
    vals: list[int] = []
    patterns = (
        r"(?:up to|around|approximately|about|more than|over)?\s*([\d,]{4,})\s+(?:new\s+)?(?:homes|dwellings|lots|residences)",
        r"(?:homes|dwellings|lots|residences)\s*(?:of|for|:)?\s*(?:up to|around|approximately|about|more than|over)?\s*([\d,]{4,})",
    )
    for p in patterns:
        for m in re.finditer(p, text, re.I):
            try:
                vals.append(int(m.group(1).replace(",", "")))
            except ValueError:
                pass
    return max(vals) if vals else None


def extract_largest_number_before(text: str, nouns: str) -> int | None:
    vals: list[int] = []
    for m in re.finditer(rf"([\d,]{{3,}})\s+(?:new\s+)?(?:{nouns})", text, re.I):
        try:
            vals.append(int(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return max(vals) if vals else None


def category_hits(text: str) -> dict[str, bool]:
    t = text.lower()
    return {k: any(x in t for x in v) for k, v in CATEGORY_TERMS.items()}


def extract_commercial_infrastructure(text: str) -> list[str]:
    t = text.lower()
    return [k for k, v in COMMERCIAL_INFRA_TERMS.items() if any(x in t for x in v)][:8]


def infer_stage(text: str) -> tuple[str, int]:
    t = text.lower()
    label, best = "Planning pipeline", 4
    for k, v in STATUS_WORDS.items():
        if k in t and v > best:
            label, best = k.title(), v
    return label, best


def infer_land_release(text: str) -> tuple[str, int]:
    t = text.lower()
    for label, patterns in LAND_RELEASE_PATTERNS:
        if any(re.search(p, t, re.I) for p in patterns):
            return label, LAND_RELEASE_STAGES.index(label)
    return "Not released", 0


def extract_release_details(text: str) -> dict:
    out: dict = {}
    m = re.search(
        r"(?:registration|registered|titles?)\s*(?:expected|anticipated|due|by|in|around)?\s*[:\-]?\s*((?:Q[1-4]\s*)?20\d{2}|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+20\d{2})",
        text, re.I,
    )
    if m:
        out["expected_registration"] = m.group(1)
    m = re.search(r"(?:deposit|holding deposit)\s*(?:of|from|is|:)?\s*\$([\d,]+)", text, re.I)
    if m:
        out["deposit"] = int(m.group(1).replace(",", ""))
    sizes = [
        int(x) for x in re.findall(r"\b(\d{3,4})\s*m(?:²|2|sqm)\b", text, re.I)
        if 150 <= int(x) <= 2000
    ]
    if sizes:
        out["lot_size_min_sqm"] = min(sizes)
        out["lot_size_max_sqm"] = max(sizes)
    prices = [int(x.replace(",", "")) for x in re.findall(r"\$([2-9]\d{2},\d{3})", text)]
    if prices:
        out["starting_land_price"] = min(prices)
        out["avg_block_price"] = round(sum(prices) / len(prices))
    return out


def score_candidate(homes: int, hits: dict[str, bool], stage_score: int) -> tuple[dict, int]:
    scale = 10 if homes >= 30000 else 9 if homes >= 15000 else 8 if homes >= 10000 else 7
    infra = min(10, 4 + sum(hits.values()))
    scores = {
        "scale": scale,
        "infrastructure": infra,
        "schools": 9 if hits["schools"] else 4,
        "retail": 8 if hits["retail"] else 4,
        "transport": 9 if hits["transport"] else 4,
        "employment": 9 if hits["employment"] else 4,
        "earliness": 10 if stage_score <= 5 else 8 if stage_score <= 7 else 6 if stage_score <= 8 else 4,
        "certainty": stage_score,
    }
    weights = {"scale": 18, "infrastructure": 18, "schools": 10, "retail": 10, "transport": 12, "employment": 12, "earliness": 10, "certainty": 10}
    return scores, round(sum(scores[k] / 10 * weights[k] for k in weights))


def source_headline(title: str) -> str | None:
    t = re.sub(
        r"\s*[-|–]\s*(NSW Planning|Planning NSW|Queensland Government|Victorian Planning).*$",
        "", title, flags=re.I,
    ).strip()
    return t[:150] if t else None


def project_name(title: str, url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1].replace("-", " ").strip()
    candidate = slug.title() if slug else re.sub(r"\s*[-|–].*$", "", title).strip()
    if any(x in candidate.lower() for x in GENERIC_NAME_TERMS) or len(candidate) < 3:
        return None
    if candidate.lower() in ("housing", "growth", "precinct", "greenfield", "transport oriented development", "low and mid"):
        return None
    return candidate[:100]


def derive_region(text: str, state: str) -> str:
    for m in ("Greater Macarthur", "Western Sydney", "Moreton Bay", "South East Queensland", "Ballarat", "Geelong", "Melbourne"):
        if m.lower() in text.lower():
            return m
    return state


def summarise(homes: int, jobs: int | None, pop: int | None, hits: dict[str, bool], stage: str) -> list[str]:
    h = [f"At least {homes:,} planned homes/dwellings detected in official planning text", f"Planning stage signal: {stage}"]
    if pop:
        h.append(f"Population signal: approximately {pop:,} people")
    if jobs:
        h.append(f"Employment signal: approximately {jobs:,} jobs")
    labels = {
        "schools": "schools/education", "retail": "town or shopping centres",
        "transport": "major transport/road investment", "employment": "employment land/jobs",
        "parks": "parks/community infrastructure", "utilities": "major utilities/infrastructure servicing",
    }
    present = [labels[k] for k, v in hits.items() if v]
    if present:
        h.append("Infrastructure signals: " + ", ".join(present))
    return h[:5]


def meaningful_project_tokens(name: str) -> list[str]:
    stop = {"the", "and", "part", "area", "growth", "precinct", "estate", "city", "centre", "west", "north", "south", "east"}
    return [x.lower() for x in re.findall(r"[A-Za-z]{4,}", name) if x.lower() not in stop]


def is_possible_sales_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return bool(host) and not any(x in host for x in GOV_HOST_HINTS + IGNORE_SALES_HOSTS)


def outbound_sales_candidates(html: str, base_url: str, project: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    tokens = meaningful_project_tokens(project)
    scored: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        u = urljoin(base_url, a["href"]).split("#")[0]
        if not is_possible_sales_url(u):
            continue
        blob = (a.get_text(" ", strip=True) + " " + u).lower()
        score = sum(2 for t in tokens if t in blob)
        score += sum(3 for w in SALES_WORDS if w in blob)
        score += sum(2 for w in PRIORITY_WORDS if w in blob)
        if score:
            scored.append((score, u))
    scored.sort(reverse=True)
    seen, out = set(), []
    for _, u in scored:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:8]


def unwrap_ddg(url: str) -> str:
    if "duckduckgo.com/l/" in url:
        q = parse_qs(urlparse(url).query)
        if q.get("uddg"):
            return unquote(q["uddg"][0])
    return url


def ddg_search(query: str) -> list[str]:
    try:
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query}, timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 FutureHuntleeFinder/2.0"},
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        out: list[str] = []
        for a in soup.select("a.result__a[href]"):
            u = unwrap_ddg(urljoin("https://duckduckgo.com", a["href"]))
            if is_possible_sales_url(u) and u not in out:
                out.append(u)
        return out[:8]
    except Exception:
        return []


def validate_sales_page(url: str, project: str) -> tuple[int, str | None, str | None]:
    try:
        html = fetch(url)
        title, text = clean_text(html)
    except Exception:
        return 0, None, None
    low = (title + " " + text[:30000]).lower()
    tokens = meaningful_project_tokens(project)
    token_hits = sum(1 for t in tokens if t in low)
    sales_hits = sum(1 for w in SALES_WORDS if w in low)
    priority_hits = sum(1 for w in PRIORITY_WORDS if w in low)
    score = token_hits * 4 + min(sales_hits, 4) * 3 + min(priority_hits, 3) * 2
    if tokens and token_hits == 0:
        score = 0
    return score, html, text


def find_links_on_sales_site(html: str, base_url: str) -> tuple[str | None, str | None]:
    soup = BeautifulSoup(html, "lxml")
    base_host = urlparse(base_url).netloc.lower()
    best_sales: tuple[int, str] | None = None
    best_priority: tuple[int, str] | None = None
    for a in soup.find_all("a", href=True):
        u = urljoin(base_url, a["href"]).split("#")[0]
        if urlparse(u).netloc.lower() != base_host:
            continue
        blob = (a.get_text(" ", strip=True) + " " + u).lower()
        sales_score = sum(2 for w in SALES_WORDS if w in blob)
        priority_score = sum(3 for w in PRIORITY_WORDS if w in blob)
        if sales_score and (best_sales is None or sales_score > best_sales[0]):
            best_sales = (sales_score, u)
        if priority_score and (best_priority is None or priority_score > best_priority[0]):
            best_priority = (priority_score, u)
    return (best_sales[1] if best_sales else None, best_priority[1] if best_priority else None)


def developer_label(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    root = host.split(".")[0]
    return re.sub(r"[-_]", " ", root).title()


def discover_developer_sales(project: dict, planning_html: str | None = None) -> dict:
    name = project.get("name", "")
    region = project.get("region", "")
    state = project.get("state", "")
    source = project.get("source", "")
    candidates: list[str] = []

    if planning_html and source:
        candidates += outbound_sales_candidates(planning_html, source, name)

    query = f'"{name}" {region} {state} land release now selling register interest'
    candidates += ddg_search(query)

    seen: set[str] = set()
    ranked: list[tuple[int, str, str, str]] = []
    for u in candidates:
        if u in seen or not is_possible_sales_url(u):
            continue
        seen.add(u)
        score, html, text = validate_sales_page(u, name)
        if score >= 7 and html and text:
            ranked.append((score, u, html, text))
        if len(seen) >= MAX_SALES_DISCOVERY_FETCHES:
            break
    if not ranked:
        return {}

    ranked.sort(reverse=True, key=lambda x: x[0])
    _, landing, html, text = ranked[0]
    sales_link, priority_link = find_links_on_sales_site(html, landing)
    sales_url = sales_link or landing

    sales_text = text
    sales_html = html
    if sales_link and sales_link != landing:
        try:
            sales_html = fetch(sales_link)
            _, sales_text = clean_text(sales_html)
        except Exception:
            pass
    status, idx = infer_land_release(sales_text)
    details = extract_release_details(sales_text)
    return {
        "developer_name": developer_label(landing),
        "developer_url": f"{urlparse(landing).scheme}://{urlparse(landing).netloc}/",
        "sales_link": sales_url,
        "priority_list_url": priority_link,
        "land_release_status": status,
        "land_release_index": idx,
        "sales_last_checked": datetime.now(timezone.utc).date().isoformat(),
        "sales_source_status": "developer-site-confirmed",
        **details,
    }


def monitor_existing_sales(project: dict) -> dict:
    sales_url = project.get("sales_link") or project.get("developer_url")
    if not sales_url:
        return {}
    try:
        html = fetch(sales_url)
        _, text = clean_text(html)
    except Exception:
        return {"sales_last_checked": datetime.now(timezone.utc).date().isoformat(), "sales_source_status": "temporarily-unreachable"}
    sales_link, priority_link = find_links_on_sales_site(html, sales_url)
    chosen = sales_link or sales_url
    if sales_link and sales_link != sales_url:
        try:
            html2 = fetch(sales_link)
            _, text = clean_text(html2)
        except Exception:
            pass
    status, idx = infer_land_release(text)
    return {
        "sales_link": chosen,
        "priority_list_url": priority_link or project.get("priority_list_url"),
        "land_release_status": status,
        "land_release_index": idx,
        "sales_last_checked": datetime.now(timezone.utc).date().isoformat(),
        "sales_source_status": "developer-site-confirmed",
        **extract_release_details(text),
    }


def send_email(alerts: list[dict]) -> None:
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    to_addr = os.getenv("ALERT_EMAIL")
    if not all([host, user, password, to_addr]) or not alerts:
        return
    msg = EmailMessage()
    msg["Subject"] = f"Future Huntlee Finder: {len(alerts)} important update(s)"
    msg["From"] = user
    msg["To"] = to_addr
    lines = ["Future Huntlee Finder found important updates:\n"]
    for c in alerts:
        lines += [
            f"{c['name']} ({c['state']}) — {c.get('score', '—')}/100",
            c.get("subtitle", ""),
            f"Land release: {c.get('land_release_status', 'Unknown')}",
            f"Developer: {c.get('developer_name', 'Unknown')}",
            f"Priority list: {c.get('priority_list_url') or 'Not found'}",
            f"Sales page: {c.get('sales_link') or 'Not found'}",
            f"Homes: {c.get('homes', '—')}",
            "",
        ]
    msg.set_content("\n".join(lines))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, int(os.getenv("SMTP_PORT", "465")), context=ctx) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


def main() -> None:
    db = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    existing_urls = {c.get("source"): c for c in db["candidates"] if c.get("source")}
    planning_html_cache: dict[str, str] = {}

    discovered: list[tuple[str, str]] = []
    for sm in sources["sitemaps"]:
        for u in sitemap_candidates(sm["url"]):
            discovered.append((sm["state"], u))
    for seed in sources["seed_pages"]:
        for u in linked_candidates(seed["url"]):
            discovered.append((seed["state"], u))

    dedup: list[tuple[str, str]] = []
    seen: set[str] = set()
    for state, u in discovered:
        if u not in seen:
            seen.add(u)
            dedup.append((state, u))

    alerts: list[dict] = []
    alert_keys: set[str] = set()
    log: list[dict] = []

    # PASS 1: discover/update projects from official planning sources.
    for state, url in dedup[:MAX_DISCOVERY_PAGES]:
        try:
            html = fetch(url)
            planning_html_cache[url] = html
            title, text = clean_text(html)
            name = project_name(title, url)
            if not name:
                continue
            homes = extract_largest_home_count(text)
            if not homes or homes < MIN_HOMES:
                continue
            hits = category_hits(text)
            if sum(hits.values()) < MIN_INFRA_CATEGORIES:
                continue
            stage, stage_score = infer_stage(text)
            scores, total = score_candidate(homes, hits, stage_score)
            jobs = extract_largest_number_before(text, r"jobs|employees")
            pop = extract_largest_number_before(text, r"people|residents|population")
            commercial = extract_commercial_infrastructure(text)
            candidate = {
                "name": name,
                "subtitle": source_headline(title),
                "state": state,
                "region": derive_region(text, state),
                "stage": stage,
                "status": "HIGH CONVICTION" if total >= 85 else "WATCH CLOSELY" if total >= 75 else "EARLY WATCH",
                "homes": homes,
                "population": pop,
                "jobs": jobs,
                "commercial_infrastructure": commercial,
                "scores": scores,
                "highlights": summarise(homes, jobs, pop, hits, stage),
                "risk": "Automatically discovered from official planning material. Verify project boundaries, delivery timing, local supply, infrastructure funding and purchase price before investment decisions.",
                "source": url,
                "score": total,
                "last_checked": datetime.now(timezone.utc).date().isoformat(),
                "discovery": "automatic",
            }
            prev = existing_urls.get(url)
            changed = False
            if prev:
                changed = (
                    abs(total - prev.get("score", 0)) >= 5
                    or homes != prev.get("homes")
                    or stage != prev.get("stage")
                    or commercial != prev.get("commercial_infrastructure", [])
                )
                # Preserve sales fields; only planning fields are overwritten here.
                prev.update(candidate)
                candidate = prev
            else:
                candidate.setdefault("land_release_status", "Not released")
                candidate.setdefault("land_release_index", 0)
                db["candidates"].append(candidate)
                existing_urls[url] = candidate
                changed = True
            if changed and total >= ALERT_SCORE:
                key = f"planning:{url}:{total}"
                if key not in alert_keys:
                    alerts.append(candidate.copy())
                    alert_keys.add(key)
            log.append({"name": name, "url": url, "score": total, "homes": homes})
        except Exception as e:
            log.append({"url": url, "error": str(e)[:180]})

    # PASS 2: discover and then monitor each project's developer/estate sales channel.
    for project in db["candidates"]:
        old_idx = int(project.get("land_release_index", 0) or 0)
        old_priority = project.get("priority_list_url")
        old_sales = project.get("sales_link")
        source = project.get("source")

        sales_update: dict = {}
        if project.get("sales_link") or project.get("developer_url"):
            sales_update = monitor_existing_sales(project)
        else:
            planning_html = planning_html_cache.get(source)
            if planning_html is None and source:
                try:
                    planning_html = fetch(source)
                    planning_html_cache[source] = planning_html
                except Exception:
                    planning_html = None
            sales_update = discover_developer_sales(project, planning_html)

        if not sales_update:
            project["sales_last_checked"] = datetime.now(timezone.utc).date().isoformat()
            project.setdefault("sales_source_status", "developer-site-not-found-yet")
            continue

        project.update(sales_update)
        new_idx = int(project.get("land_release_index", 0) or 0)
        new_priority = project.get("priority_list_url")
        new_sales = project.get("sales_link")

        priority_found = bool(new_priority and not old_priority)
        sales_channel_found = bool(new_sales and not old_sales)
        release_advanced = new_idx > old_idx
        buyable_breakthrough = new_idx >= 4 and old_idx < 4

        if priority_found:
            project["release_alert_reason"] = "Priority/VIP registration channel discovered"
        if release_advanced:
            project["release_alert_reason"] = f"Land release advanced to {project.get('land_release_status')}"
        if buyable_breakthrough:
            project["release_alert_reason"] = "BLOCKS MAY NOW BE SECURABLE"

        # Alert as soon as a useful priority list appears, and on every forward release milestone.
        if priority_found or release_advanced or buyable_breakthrough:
            key = f"sales:{project.get('name')}:{new_idx}:{new_priority or ''}"
            if key not in alert_keys:
                alerts.append(project.copy())
                alert_keys.add(key)
        elif sales_channel_found and project.get("score", 0) >= ALERT_SCORE:
            key = f"sales-source:{project.get('name')}:{new_sales}"
            if key not in alert_keys:
                alerts.append(project.copy())
                alert_keys.add(key)

    db["updated"] = datetime.now(timezone.utc).date().isoformat()
    db["scanner"] = {
        "last_run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages_considered": len(dedup[:MAX_DISCOVERY_PAGES]),
        "qualified_pages": len([x for x in log if "score" in x]),
        "developer_sites_confirmed": len([c for c in db["candidates"] if c.get("sales_source_status") == "developer-site-confirmed"]),
        "priority_lists_found": len([c for c in db["candidates"] if c.get("priority_list_url")]),
        "buyable_projects": len([c for c in db["candidates"] if int(c.get("land_release_index", 0) or 0) >= 4]),
        "alert_count": len(alerts),
        "mode": "planning-discovery-plus-developer-sales-monitoring",
    }
    db["candidates"].sort(key=lambda c: c.get("score", 0), reverse=True)
    DATA_PATH.write_text(json.dumps(db, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ALERT_PATH.write_text(
        json.dumps({"generated": db["scanner"]["last_run_utc"], "alerts": alerts}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    send_email(alerts)
    print(
        f"Planning pages: {len(dedup[:MAX_DISCOVERY_PAGES])}; "
        f"developer sites: {db['scanner']['developer_sites_confirmed']}; "
        f"priority lists: {db['scanner']['priority_lists_found']}; "
        f"buyable: {db['scanner']['buyable_projects']}; alerts: {len(alerts)}"
    )


if __name__ == "__main__":
    main()
