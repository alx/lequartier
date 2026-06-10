"""Rebuild the SQLite listing index from curated JSON files.

Usage:
    uv run scripts/rebuild_listing_index.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from web.listing_index import rebuild

n = rebuild()
print(f"Listing index rebuilt: {n} row{'s' if n != 1 else ''}")
