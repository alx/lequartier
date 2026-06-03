from __future__ import annotations

import json
import logging
from pathlib import Path

from . import cache as cache_mod

logger = logging.getLogger(__name__)

_EXAMPLES_DIR = Path(__file__).parent / "examples"

EXAMPLE_COORDS: dict[str, tuple[float, float]] = {
    "686559818391956388": (37.9767, 23.7184),
    "10349749": (43.60498, 1.45783),
}


def seed_cache() -> None:
    """Populate cache from source-controlled seed files if no fresh entry exists."""
    cfg = None
    try:
        from . import poi_engine
        cfg = poi_engine.get_cfg()
    except Exception:
        pass

    ttl = cfg.cache_ttl_days if cfg else 7

    for seed_file in sorted(_EXAMPLES_DIR.glob("*.json")):
        try:
            seed = json.loads(seed_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[examples] could not read %s: %s", seed_file.name, exc)
            continue

        listing_id = seed.get("listing_id") or seed_file.stem
        lat = seed.get("lat")
        lon = seed.get("lon")
        categories = seed.get("categories", [])
        result = seed.get("result", {})

        if not result or lat is None or lon is None:
            logger.warning("[examples] incomplete seed data in %s", seed_file.name)
            continue

        existing = cache_mod.get(listing_id, lat, lon, categories, ttl_days=ttl)
        if existing:
            logger.debug("[examples] cache already fresh for %s", listing_id)
            continue

        cache_mod.put(listing_id, lat, lon, categories, {**result, "from_cache": False})
        logger.info("[examples] seeded cache for %s", listing_id)
