"""One-off script: replace 686559818391956388 curated POIs with Lamai locations."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).parent.parent
LAMAI_SRC = REPO.parent / "travel-guide" / "static" / "lamai" / "locations.geojson"
CURATED_OUT = REPO / "src" / "web" / "curated" / "686559818391956388.json"
LISTING_ID = "686559818391956388"
LISTING_URL = f"https://www.airbnb.com/rooms/{LISTING_ID}"
LAMAI_CENTER = {"lat": 9.4700, "lon": 100.0490}
LAMAI_LOCATION = {"neighbourhood": "Lamai", "city": "Ko Samui", "country": "Thailand", "timezone": None}

CATEGORY_MAP: dict[str, str] = {
    "Restaurant": "Restaurant",
    "Muay Thai": "Activity",
    "Spa": "Wellness",
    "Market": "Market",
    "Bakery": "Bakery & Food",
    "Bar": "Bar",
    "Fitness": "Activity",
    "Landmark": "Culture",
    "Temple": "Culture",
    "Café": "Bakery & Food",
    "Shopping": "Shopping",
    "Beach": "Beach",
}

CATEGORY_META: dict[str, dict] = {
    "Supermarket":   {"icon": "🛒", "color": "#16a34a"},
    "Bakery & Food": {"icon": "🥖", "color": "#2563eb"},
    "Market":        {"icon": "🏪", "color": "#f97316"},
    "Night Shop":    {"icon": "🌙", "color": "#9333ea"},
    "Park":          {"icon": "🌳", "color": "#dc2626"},
    "Playground":    {"icon": "🛝", "color": "#0891b2"},
    "Dog Park":      {"icon": "🐕", "color": "#ca8a04"},
    "Transit":       {"icon": "🚌", "color": "#be185d"},
    "Activity":      {"icon": "🎠", "color": "#15803d"},
    "Culture":       {"icon": "🏛️",  "color": "#1d4ed8"},
    "Wellness":      {"icon": "🧘", "color": "#ea580c"},
    "Restaurant":    {"icon": "🍽️",  "color": "#7c3aed"},
    "Health":        {"icon": "💊", "color": "#b91c1c"},
    "Bike Share":    {"icon": "🚲", "color": "#0e7490"},
    "Bar":           {"icon": "🍺", "color": "#b45309"},
    "Shopping":      {"icon": "🛍️",  "color": "#db2777"},
    "Beach":         {"icon": "🏖️",  "color": "#0ea5e9"},
}


def convert_feature(src: dict, idx: int) -> dict:
    props = src["properties"]
    src_cat = props.get("category", "")
    cat = CATEGORY_MAP.get(src_cat, src_cat)
    meta = CATEGORY_META.get(cat, {})

    out_props: dict = {
        "name": props["name"],
        "category": cat,
        "icon": meta.get("icon", "📍"),
        "coord_source": props.get("coord_source", "manual"),
        "coord_accuracy": props.get("coord_accuracy", "medium"),
        "source": "curated",
        "listing_url": LISTING_URL,
        "generated_name": False,
        "status": "primary",
    }

    hours = props.get("hours")
    if hours:
        out_props["opening_hours"] = {"raw": hours, "open_now": None, "source": "curated"}

    for field in ("phone", "notes"):
        if props.get(field):
            out_props[field] = props[field]

    url = props.get("url") or props.get("website")
    if url:
        out_props["website"] = url

    if props.get("price"):
        out_props["price_level"] = props["price"]

    return {
        "type": "Feature",
        "id": f"airbnb/{LISTING_ID}-{idx:03d}",
        "geometry": src["geometry"],
        "properties": out_props,
    }


def main() -> None:
    lamai = json.loads(LAMAI_SRC.read_text(encoding="utf-8"))
    base = json.loads(CURATED_OUT.read_text(encoding="utf-8"))

    features = [convert_feature(f, i + 1) for i, f in enumerate(lamai["features"])]
    n = len(features)

    geojson = base["result"]["geojson"]
    geojson["features"] = features
    geojson["_meta"]["category_meta"] = CATEGORY_META
    geojson["_meta"]["center"] = LAMAI_CENTER
    geojson["_meta"]["location"] = LAMAI_LOCATION
    geojson["_meta"]["source"] = f"Curated — {LISTING_URL}"
    geojson["_meta"]["listing_url"] = LISTING_URL

    base["lat"] = LAMAI_CENTER["lat"]
    base["lon"] = LAMAI_CENTER["lon"]
    base["result"]["lat"] = LAMAI_CENTER["lat"]
    base["result"]["lon"] = LAMAI_CENTER["lon"]
    base["result"]["location"] = LAMAI_LOCATION
    base["result"]["n_pois"] = n

    CURATED_OUT.write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Written {n} POIs to {CURATED_OUT}")


if __name__ == "__main__":
    main()
