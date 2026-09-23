#!/usr/bin/env python3
"""Bake deadlines.json into index.html's seed block.

index.html prefers a live deadlines.json fetched next to it, which is what
happens on GitHub Pages. But the same file also gets published as a standalone
page where no sibling JSON exists, so it carries an embedded copy as a fallback.
This keeps that copy in step with the scraper instead of hand-syncing two files.

    python3 scrape.py && python3 build.py
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "index.html")
DATA = os.path.join(HERE, "deadlines.json")

BLOCK = re.compile(
    r'(<script id="seed-data" type="application/json">)(.*?)(</script>)',
    re.S,
)


def main() -> int:
    if not os.path.exists(DATA):
        sys.exit(f"{DATA} not found — run scrape.py first.")

    with open(DATA, encoding="utf-8") as handle:
        data = json.load(handle)

    # ensure_ascii keeps the payload pure ASCII, so an em dash survives even if
    # the page is ever served without a charset. </script> inside the payload
    # would end the block early, so < is escaped too.
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")

    with open(PAGE, encoding="utf-8") as handle:
        html = handle.read()

    if not BLOCK.search(html):
        sys.exit('No <script id="seed-data"> block in index.html — cannot inject.')

    html = BLOCK.sub(lambda m: m.group(1) + payload + m.group(3), html, count=1)

    with open(PAGE, "w", encoding="utf-8") as handle:
        handle.write(html)

    dated = sum(1 for v in data["venues"] if v.get("date"))
    print(f"Baked {len(data['venues'])} venues ({dated} dated) into index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
