"""Backfill `properties.score` on existing curated listing GeoJSONs.

Reads each curated file, computes poi_score() from existing feature data
(coordinates + properties already present), and writes the updated file.
Does not re-fetch any external data.

Usage:
    uv run scripts/backfill_scores.py
    uv run scripts/backfill_scores.py --dry-run
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from airbnb_nearby import poi_score, load_config

CURATED_DIR = Path(__file__).parent.parent / "src" / "web" / "curated"


def backfill(curated_dir: Path, dry_run: bool) -> None:
    load_config(Path(__file__).parent.parent / "src" / "airbnb_nearby.toml")
    paths = list(curated_dir.glob("*.json")) + list((curated_dir / "zillow").glob("*.json"))
    updated = 0
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  skip {path.name}: {e}")
            continue

        rental_lat = data.get("lat") or 0.0
        rental_lon = data.get("lon") or 0.0
        geojson = (data.get("result") or {}).get("geojson") or {}
        features = geojson.get("features") or []
        changed = 0
        for f in features:
            props = f.get("properties") or {}
            if props.get("score") is not None:
                continue
            coords = (f.get("geometry") or {}).get("coordinates") or [0, 0]
            p = {
                "lat": coords[1], "lon": coords[0],
                "category":           props.get("category", ""),
                "rating":             props.get("rating"),
                "user_rating_count":  props.get("user_rating_count"),
                "coord_accuracy":     props.get("coord_accuracy", ""),
                "source":             props.get("source", ""),
            }
            props["score"] = poi_score(p, rental_lat, rental_lon)
            changed += 1

        if changed:
            if not dry_run:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  {'(dry) ' if dry_run else ''}{'updated' if not dry_run else 'would update'} {path.name}: {changed} score{'s' if changed != 1 else ''} added")
            updated += 1

    if not updated:
        print("All features already have scores.")
    else:
        print(f"\n{updated} file{'s' if updated != 1 else ''} {'updated' if not dry_run else 'would be updated'}.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    backfill(CURATED_DIR, args.dry_run)
