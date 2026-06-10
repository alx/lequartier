"""Remove garbage enriched URLs from city and curated GeoJSON files.

Validation rules per field:
  wikipedia_url  — must be *.wikipedia.org/wiki/<slug> with a non-generic slug
  transit_url    — must be a known public-transit domain
  ticket_url     — must not be a junk/unrelated domain
  courses_url    — must not be a junk/unrelated domain
  video_url      — must be a youtube.com/embed/ URL (untouched by this script)

Usage:
  uv run scripts/scrub_enriched_urls.py           # scrub and write
  uv run scripts/scrub_enriched_urls.py --dry-run # show removals without writing
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT        = Path(__file__).parent.parent
CITIES_DIR  = ROOT / "src/web/static/data/cities"
CURATED_DIR = ROOT / "src/web/curated"

# ── Domain lists ──────────────────────────────────────────────────────────────

JUNK_DOMAINS = {
    "youtube.com", "youtu.be", "facebook.com", "twitter.com", "x.com",
    "whatsapp.com", "web.whatsapp.com", "instagram.com", "linkedin.com",
    "microsoft.com", "support.microsoft.com", "office.com",
    "google.com", "support.google.com", "accounts.google.com",
    "zhihu.com", "baidu.com", "jingyan.baidu.com", "xmind.cn",
    "ef.com.tw", "gamewith.jp", "mumu.163.com", "answers.com",
    "lowyat.net", "forum.lowyat.net", "reddit.com",
    "tenforums.com", "investopedia.com", "hibcc.org", "mcafee.com",
    "palaceskateboards.com", "trustoo.nl", "dafont.com",
    "rosacomputer.vn", "lmskincentre.com", "xylem.live",
    "hk01.com", "tw.stock.yahoo.com", "cmoney.tw",
    "trip.com", "unirank.org", "guldborgsund.dk",
    "st.com",  # ST Microelectronics, not Saint-anything
    "joto.com", "stackoverflow.com",
    "tripadvisor.com", "th.tripadvisor.com",
    "rome2rio.com",  # travel planner, not a transit schedule
}

TRANSIT_DOMAINS = {
    "moovitapp.com", "busmaps.com",
    # DE
    "bahnhof.de", "bvg.de", "mvg.de", "mvv-muenchen.de", "s-bahn-berlin.de",
    # FR
    "ratp.fr", "tisseo.fr", "sncf.com", "sncf-connect.com",
    # UK
    "tfl.gov.uk", "nationalrail.co.uk", "transportforwales.wales",
    # IT
    "treniamo.it", "trenitalia.com", "atac.roma.it",
    # GR
    "oasa.gr", "stasy.gr",
    # ES
    "metromadrid.es", "metro.cat", "tmb.cat", "metrobilbao.eus",
    # BE
    "nmbs.be", "stib.brussels", "stib-mivb.be",
    # NL
    "ns.nl", "gvb.nl", "ret.nl",
    # AT
    "wienerlinien.at", "oebb.at",
    # CH
    "sbb.ch", "zvv.ch",
    # PL
    "ztm.waw.pl",
    # CZ
    "dpp.cz", "idos.cz",
    # HU
    "bkk.hu",
    # SE
    "sl.se", "sj.se",
    # NO
    "ruter.no",
    # DK
    "dsb.dk", "rejseplanen.dk",
    # FI
    "hsl.fi",
    # PT
    "carris.pt", "metrolisboa.pt",
    # RO
    "metrorex.ro",
    # US/CA
    "mta.info", "bart.gov", "wmata.com", "mbta.com", "ttc.ca", "translink.ca",
    "seattletransitblog.com", "trimet.org",
    # AU
    "transportnsw.info", "ptv.vic.gov.au", "transperth.wa.gov.au",
    # JP
    "tokyometro.jp", "jreast.co.jp",
    # HK
    "mtr.com.hk",
    # SG
    "lta.gov.sg",
    # TH
    "bts.co.th", "mrt.co.th",
    # IN
    "delhimetrorail.com", "bmrc.co.in",
    # KR
    "seoulmetro.co.kr",
    # BR
    "metro.sp.gov.br", "metro.df.gov.br",
    # AR
    "metrovias.com.ar",
    # MX
    "metro.cdmx.gob.mx",
    # RU
    "mosmetro.ru",
    # Generic
    "publictransit.us",
}

# Wikipedia slugs that are generic/wrong — reject even if on wikipedia.org
GENERIC_WIKI_SLUGS = {
    "The", "Basilica", "Rock_(geology)", "Fidel_Castro",
    "Comt%C3%A9_cheese", "Berliner_(doughnut)",
}


# ── Validators ────────────────────────────────────────────────────────────────

def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _is_junk(url: str) -> bool:
    h = _host(url)
    return h in JUNK_DOMAINS or any(h.endswith("." + d) for d in JUNK_DOMAINS)


def valid_wikipedia(url: str) -> bool:
    if _is_junk(url):
        return False
    try:
        p = urlparse(url)
        if "wikipedia.org" not in p.netloc:
            return False
        parts = p.path.split("/")
        if len(parts) < 3 or parts[1] != "wiki":
            return False
        slug = parts[2]
        return len(slug) > 4 and slug not in GENERIC_WIKI_SLUGS
    except Exception:
        return False


def valid_transit(url: str) -> bool:
    if _is_junk(url):
        return False
    h = _host(url)
    return any(h == d or h.endswith("." + d) for d in TRANSIT_DOMAINS)


def valid_ticket(url: str) -> bool:
    return not _is_junk(url)


def valid_courses(url: str) -> bool:
    return not _is_junk(url)


VALIDATORS = {
    "wikipedia_url": valid_wikipedia,
    "transit_url":   valid_transit,
    "ticket_url":    valid_ticket,
    "courses_url":   valid_courses,
}


# ── Scrub one file ────────────────────────────────────────────────────────────

def scrub_features(features: list, label: str, dry_run: bool) -> int:
    removed = 0
    for f in features:
        props = f.get("properties") or {}
        for field, validator in VALIDATORS.items():
            url = props.get(field)
            if not url:
                continue
            if not validator(url):
                name = props.get("name", "?")
                print(f"  REMOVE [{field}] {name!r:40} {url}")
                removed += 1
                if not dry_run:
                    del props[field]
    return removed


def scrub_city(path: Path, dry_run: bool) -> int:
    data = json.loads(path.read_text())
    features = data.get("features", [])
    n = scrub_features(features, path.stem, dry_run)
    if n and not dry_run:
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return n


def scrub_curated(path: Path, dry_run: bool) -> int:
    data = json.loads(path.read_text())
    features = ((data.get("result") or {}).get("geojson") or {}).get("features") or []
    n = scrub_features(features, path.stem, dry_run)
    if n and not dry_run:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = p.parse_args()

    if args.dry_run:
        print("[dry-run] no files will be written\n")

    total = 0

    print("── Curated listings ──")
    for path in sorted(CURATED_DIR.glob("*.json")):
        n = scrub_curated(path, args.dry_run)
        if n:
            print(f"  {path.stem}: {n} removed{'(dry)' if args.dry_run else ', written'}")
        total += n

    zillow_dir = CURATED_DIR / "zillow"
    if zillow_dir.exists():
        for path in sorted(zillow_dir.glob("*.json")):
            n = scrub_curated(path, args.dry_run)
            if n:
                print(f"  zillow/{path.stem}: {n} removed")
            total += n

    print("\n── City GeoJSONs ──")
    for path in sorted(CITIES_DIR.glob("*.geojson")):
        n = scrub_city(path, args.dry_run)
        if n:
            print(f"  {path.stem}: {n} removed{'(dry)' if args.dry_run else ', written'}")
        total += n

    print(f"\nDone — {total} URL{'s' if total != 1 else ''} removed.")


if __name__ == "__main__":
    main()
