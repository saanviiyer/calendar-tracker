#!/usr/bin/env python3
"""Collect conference and workshop submission deadlines into deadlines.json.

Four sources, roughly in decreasing order of trust:

  openreview  authoritative for workshops. Every NeurIPS/ICLR/ICML workshop
              registers a submission invitation carrying an exact `duedate`,
              so this finds workshop deadlines the moment organizers set them —
              usually well before the workshop's own website is filled in.
  feeds       ccfddl and huggingface/ai-deadlines publish structured YAML.
              Solid for main-track ML/CV/NLP/HCI venues, silent on workshops.
  venues.yml  hand-checked entries. A `pin_date` here beats everything.
  probe       last resort: fetch a CFP page and read dates out of the prose
              near deadline keywords. Always flagged for human confirmation.

When no future deadline exists anywhere, the venue's own past editions are
rolled forward a year to give a planning estimate, labelled `projected`.

    python3 scrape.py              feeds + openreview
    python3 scrape.py --probe      also scrape CFP pages (slower, noisier)
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import io
import json
import os
import re
import ssl
import sys
import tarfile
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required:  pip install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))
VENUES_FILE = os.path.join(HERE, "venues.yml")
OUT_FILE = os.path.join(HERE, "deadlines.json")
DROPPED_FILE = os.path.join(HERE, "dropped.yml")
STATE_FILE = os.path.join(HERE, "state.yml")
LABS_FILE = os.path.join(HERE, "labs_cache.yml")
TASKS_FILE = os.path.join(HERE, "tasks.yml")
MY_VENUES_FILE = os.path.join(HERE, "my_venues.yml")

HF_TARBALL = "https://codeload.github.com/huggingface/ai-deadlines/tar.gz/refs/heads/main"
CCF_ALLCONF = "https://ccfddl.com/conference/allconf.yml"
OR_API = "https://api2.openreview.net"

UA = "conference-tracker/1.0 (personal deadline board)"
TIMEOUT = 40
TODAY = dt.date.today()
LOCAL = ZoneInfo("America/Los_Angeles")     # overridable via venues.yml `timezone`


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def fetch(url: str, binary: bool = False, retries: int = 4):
    """GET with backoff. OpenReview throttles hard, so 429/503 must be retried."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT,
                                        context=ssl.create_default_context()) as resp:
                raw = resp.read()
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503) and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    if binary:
        return raw
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def try_fetch(url: str, binary: bool = False, label: str = ""):
    try:
        return fetch(url, binary=binary)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
            ValueError, http.client.HTTPException) as exc:
        # ValueError covers http.client.InvalidURL, raised for a malformed host
        # before any connection is attempted. Third-party metadata is the source
        # of most of these URLs, so one bad record must not end the run.
        print(f"  ! {label or url}: {exc}", file=sys.stderr)
        return None


def try_json(url: str, label: str = ""):
    text = try_fetch(url, label=label)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"  ! {label or url}: bad JSON ({exc})", file=sys.stderr)
        return None


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------

MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted(MONTH_NAMES, key=len, reverse=True))

DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(" + _MONTH_RE + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I), "mdy"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_RE + r")\.?,?\s+(\d{4})\b", re.I), "dmy"),
]
DEADLINE_KEYWORDS = re.compile(
    r"deadline|submission|submit|due|closes|closing|abstract|cfp|call for", re.I)
EXCLUDE_KEYWORDS = re.compile(
    r"notification|accept|decision|camera[- ]ready|conference date|workshop date|held on|"
    r"takes place|registration|early bird|rebuttal|review release|reviews due", re.I)


def parse_iso(value) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", str(value).strip())
    if not match:
        return None
    try:
        return dt.date(*(int(g) for g in match.groups()))
    except ValueError:
        return None


def dates_in(text: str) -> list[dt.date]:
    found = []
    for pattern, order in DATE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                if order == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif order == "mdy":
                    mo = MONTH_NAMES[m.group(1).lower().rstrip(".")]
                    d, y = int(m.group(2)), int(m.group(3))
                else:
                    d = int(m.group(1))
                    mo = MONTH_NAMES[m.group(2).lower().rstrip(".")]
                    y = int(m.group(3))
                found.append(dt.date(y, mo, d))
            except (ValueError, KeyError):
                continue
    return found


def slug(name: str) -> str:
    norm = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", norm.lower())).strip("-")


def add_year(day: dt.date) -> dt.date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:                       # Feb 29
        return day.replace(year=day.year + 1, day=28)


def _append(note: str, extra: str) -> str:
    note = (note or "").strip()
    return f"{note} {extra}".strip() if note else extra


# --------------------------------------------------------------------------
# source: OpenReview — workshop discovery
# --------------------------------------------------------------------------

def openreview_workshops(domain: str) -> list[dict]:
    """Every workshop registered under e.g. NeurIPS.cc/2026, with metadata.

    One request returns all of them including title, website and the id of the
    submission invitation that carries the real deadline.
    """
    try:
        data = json.loads(fetch(f"{OR_API}/groups?parent={domain}/Workshop&limit=1000"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:                  # conference hasn't opened yet
            print(f"  {domain}: not accepting workshops yet")
        else:
            print(f"  ! {domain}: {exc}", file=sys.stderr)
        return []
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"  ! {domain}: {exc}", file=sys.stderr)
        return []
    out = []
    for group in data.get("groups") or []:
        content = group.get("content") or {}

        def val(key):
            item = content.get(key)
            if isinstance(item, dict):
                item = item.get("value")
            return item if isinstance(item, str) else None

        out.append({
            "id": group.get("id", ""),
            "short": (group.get("id") or "").rsplit("/", 1)[-1],
            "title": val("title") or "",
            "subtitle": val("subtitle") or "",
            "website": val("website") or "",
            "submission_id": val("submission_id") or "",
        })
    return out


def openreview_duedate(submission_id: str):
    """(local_date, local_datetime_string) for a submission invitation."""
    data = try_json(f"{OR_API}/invitations?id={submission_id}",
                    label=f"openreview invitation {submission_id}")
    if not data:
        return None, None
    for inv in data.get("invitations") or []:
        stamp = inv.get("duedate") or inv.get("expdate")
        if stamp:
            moment = dt.datetime.fromtimestamp(stamp / 1000, tz=dt.timezone.utc).astimezone(LOCAL)
            return moment.date(), moment.strftime("%b %d %Y, %-I:%M %p %Z")
    return None, None


def infer_fit(text: str, projects: dict) -> list[dict]:
    """Guess which projects suit a venue from its name. Explicit `fit:` beats this."""
    hay = (text or "").lower()
    hits = []
    for key, meta in (projects or {}).items():
        for word in meta.get("keywords") or []:
            if word.lower() in hay:
                hits.append({"project": key, "strength": "moderate",
                             "why": f"Name mentions “{word}”; matched by keyword, not by hand."})
                break
    return hits[:3]


def normalise_fit(raw, projects: dict) -> list[dict]:
    """Accept `fit: [prism]` or `fit: [{project: prism, strength: strong, why: ...}]`."""
    out = []
    for item in raw or []:
        if isinstance(item, str):
            out.append({"project": item, "strength": "moderate", "why": ""})
        elif isinstance(item, dict) and item.get("project"):
            out.append({"project": item["project"],
                        "strength": item.get("strength", "moderate"),
                        "why": (item.get("why") or "").strip()})
    order = {"strong": 0, "moderate": 1, "weak": 2}
    out.sort(key=lambda f: order.get(f["strength"], 1))
    return [f for f in out if f["project"] in (projects or {})]


# Odds ladder, worst to best. Venue selectivity sets the ceiling; the matched
# project's readiness and fit can only pull it down from there.
LADDER = ["longshot", "plausible", "likely"]
STRENGTH_DROP = {"strong": 0, "moderate": 1, "weak": 2}
# Unfinished work hurts less where the deliverable is an abstract: a curated
# venue wants 150-300 words about results you already have, not a written paper.
READINESS_DROP = {
    "curated": {"ready": 0, "partial": 0, "early": 1},
    "student_prize": {"ready": 0, "partial": 0, "early": 1},
    "default": {"ready": 0, "partial": 1, "early": 2},
}


def estimate_odds(kind: str, fits: list[dict], projects: dict, kinds: dict,
                  is_award: bool = False):
    """Rough odds of getting in, as a band plus the reasoning behind it.

    Deliberately a band and not a percentage: real acceptance rates are published
    for a handful of main tracks and essentially nowhere for workshops, so a
    number here would be false precision. What this does capture is the thing a
    published rate cannot — whether the work is actually written yet.
    """
    meta = (kinds or {}).get(kind) or {}
    base = meta.get("base", "plausible")
    if base not in LADDER:
        base = "plausible"
    if not fits:
        if is_award:
            # Fellowships judge the person, not one paper — the absence of a
            # project mapping says nothing about the odds.
            return base, (f"{meta.get('label', kind)}; assessed on your record as a whole "
                          "rather than any single project, so readiness of an individual "
                          "paper doesn't move this.")
        return "longshot", ("Nothing on your list fits this venue's scope, so a "
                            "submission would mean starting new work.")

    best = fits[0]
    project = (projects or {}).get(best["project"]) or {}
    readiness = project.get("readiness", "partial")
    scale = READINESS_DROP.get(kind, READINESS_DROP["default"])
    drop = STRENGTH_DROP.get(best["strength"], 1) + scale.get(readiness, 1)
    level = LADDER[max(0, LADDER.index(base) - drop)]

    bits = [f"{meta.get('label', kind)} venue"]
    bits.append(f"{best['strength']} fit for {project.get('name', best['project'])}")
    bits.append({"ready": "the draft exists",
                 "partial": ("results exist and an abstract is all this needs"
                             if kind in ("curated", "student_prize") else
                             "results exist but the writing is unfinished"),
                 "early": "no results yet"}.get(readiness, "readiness unclear"))
    return level, "; ".join(bits) + "."


def normalise_url(url: str) -> str:
    if not url:
        return ""
    # Some workshops register several URLs in the one OpenReview website field,
    # separated by a semicolon or whitespace — IAB lists its competition site and
    # its own site together. Take the first; the rest are not fetchable as a URL
    # and urlopen rejects the whole string.
    url = re.split(r"[;,\s]+", url.strip())[0]
    if not url:
        return ""
    return url if url.startswith(("http://", "https://")) else "https://" + url


MULTITRACK_WARNING = (
    "OpenReview lists one submission invitation per venue, but workshops often run "
    "a second, later track (non-archival extended abstracts). Check the workshop's "
    "own important-dates page before assuming this is your deadline."
)

# Pages that tend to carry the full track list, appended to a workshop's root URL.
TRACK_PAGES = ("", "important-dates", "dates", "call-for-papers", "cfp", "submission")


def find_other_tracks(site: str, known: dt.date, horizon_days: int = 200):
    """Look on a workshop's own site for submission dates the invitation misses.

    OpenReview exposes one invitation per workshop, so an archival paper track can
    hide a later non-archival extended-abstract track entirely. Missing that is
    worse than a wrong date: archival publication can foreclose submitting the
    full work to a main conference later.
    """
    if not site or "openreview.net" in site:
        return []
    base = site.rstrip("/")
    horizon = TODAY + dt.timedelta(days=horizon_days)
    seen, extra = set(), []
    for page in TRACK_PAGES:
        url = base if not page else base + "/" + page
        html = try_fetch(url, label="")
        if not html:
            continue
        for line in (ln.strip() for ln in strip_html(html).split("\n")):
            if not line or len(line) > 300:
                continue
            if not DEADLINE_KEYWORDS.search(line) or EXCLUDE_KEYWORDS.search(line):
                continue
            for when in dates_in(line):
                # Only dates meaningfully later than the one we already have:
                # an earlier or same-week date is almost always the same track.
                if when > known + dt.timedelta(days=6) and when <= horizon:
                    key = when.isoformat()
                    if key not in seen:
                        seen.add(key)
                        extra.append((when, re.sub(r"\s+", " ", line)[:160]))
        if extra:
            break
    extra.sort()
    return extra[:2]


# --------------------------------------------------------------------------
# source: who is actually running the workshop
# --------------------------------------------------------------------------
# Topic keywords find workshops about your subject. They cannot tell you which
# ones put you in a room with the people who hire. That is a separate signal and
# it lives here.
#
# Case matters for the acronyms: a case-insensitive \bFAIR\b matches the word
# "fair" in a fairness statement, which most of these pages have. Full lab names
# are matched case-insensitively, bare acronyms are not. "Google" alone is never
# matched — sites.google.com and Google Forms are on half the workshop pages in
# the field, and neither means Google is involved.
INDUSTRY_LABS = {
    "Google DeepMind": r"(?i)\bgoogle deepmind\b|\bdeepmind\b",
    "Google Research": r"(?i)\bgoogle research\b|\bgoogle brain\b",
    "OpenAI": r"(?i)\bopenai\b",
    "Anthropic": r"(?i)\banthropic\b",
    # NOT a bare \bFAIR\b: in biology and health venues "FAIR" almost always means
    # the FAIR data principles, which put a phantom Meta on PSB, RSNA, CHIL and SfN
    # the first time this ran.
    "Meta FAIR": r"(?i)\bmeta ai\b|\bmeta fair\b|\bfacebook ai\b|\bfair, meta\b",
    "Microsoft Research": r"(?i)\bmicrosoft research\b|\bmicrosoft\b",
    "NVIDIA": r"(?i)\bnvidia\b",
    # Qualified, because every site on earth ships an apple-touch-icon.
    "Apple": r"(?i)\bapple (?:inc|research|ml research|machine learning)\b|@apple\.com",
    "Amazon": r"(?i)\bamazon (?:science|research|ai|web services)\b|\bAWS\b|@amazon\.com",
    "IBM Research": r"(?i)\bibm\b",
    "xAI": r"\bxAI\b",
    "Genentech": r"(?i)\bgenentech\b",
    "Roche": r"(?i)\broche\b",
    "Novo Nordisk": r"(?i)\bnovo nordisk\b",
    "Isomorphic Labs": r"(?i)\bisomorphic labs\b",
    "EvolutionaryScale": r"(?i)\bevolutionaryscale\b",
    "Hugging Face": r"(?i)\bhugging ?face\b",
    "Goodfire": r"(?i)\bgoodfire\b",
    "Cohere": r"(?i)\bcohere\b",
    "Toyota Research": r"(?i)\btoyota (?:research|motor)\b",
}
LAB_PATTERNS = {name: re.compile(pat) for name, pat in INDUSTRY_LABS.items()}

# Where rosters live. Many sites are one page with anchors, so "" is the workhorse.
LAB_PAGES = ("", "organizers", "organizers.html", "speakers", "speakers.html",
             "committee", "people", "about")

# A sponsor is usually a logo, and a logo carries its name in attributes rather
# than in body text — `<a href="https://deepmind.google" title="Google DeepMind">
# <img alt="Google DeepMind">`. Stripping tags throws all of that away, which loses
# precisely the strongest signal on the page: a lab that paid is more involved than
# a lab that sent a speaker.
ATTR_TEXT = re.compile(r"""(?i)\b(?:alt|title|aria-label|href)\s*=\s*["']([^"']{2,120})["']""")
# ...but an asset path is not an affiliation. `apple-touch-icon.png` is on nearly
# every site and put Apple on two workshops that have nothing to do with Apple.
ASSET_URL = re.compile(r"(?i)\.(?:png|jpe?g|svg|gif|ico|css|js|woff2?|webp)\b|[?&]v=|icon")


# One site per run. Several rows can share a link — a workshop with two tracks is
# two rows pointing at one page — and a full board is slow enough already.
_LAB_CACHE: dict[str, list[str]] = {}


def scan_labs(site: str, budget: int = 4) -> list[str]:
    """Industry labs named on a workshop's own pages.

    A signal to go look, NOT a verified roster. It cannot tell an organizer from a
    sponsor logo from a PhD student's summer internship, and a workshop that merely
    *mentions* a lab in a topic list will register. Anything acted on should be
    confirmed by eye — which is why the board badges this "auto-read" and keeps
    hand-written `labs:` entries separate.
    """
    if not site or "openreview.net" in site:
        return []
    root = site.rstrip("/")
    if root in _LAB_CACHE:
        return _LAB_CACHE[root]
    found, tried = set(), 0
    for page in LAB_PAGES:
        # Budget attempts, not successes. Most of these sites are a single page
        # with anchors, so all seven subpaths 404 — counting only successes means
        # every such site costs eight requests instead of four.
        if tried >= budget:
            break
        url = root if not page else f"{root}/{page}"
        tried += 1
        html = try_fetch(url, label=f"labs {url}")
        if not html:
            continue
        # Drop comments before harvesting attributes too, or a previous edition's
        # commented-out sponsor block counts as this year's.
        html = re.sub(r"(?s)<!--.*?-->", " ", html)
        attrs = [a for a in ATTR_TEXT.findall(html) if not ASSET_URL.search(a)]
        text = strip_html(html) + " " + " ".join(attrs)
        for name, pattern in LAB_PATTERNS.items():
            if pattern.search(text):
                found.add(name)
    _LAB_CACHE[root] = sorted(found)
    return _LAB_CACHE[root]


def labs_for(key: str, link: str, do_labs: bool, cache: dict) -> list[str]:
    """Cached roster for a venue: rescan when asked, otherwise reuse."""
    if do_labs and link:
        found = scan_labs(link)
        if found:
            cache[key] = found
        else:
            cache.pop(key, None)
        return found
    return list(cache.get(key) or [])


def discover_workshops(config, claimed: set[str], deep: bool = False,
                       do_labs: bool = False, labs_cache: dict | None = None) -> list[dict]:
    """Workshops matching the configured interests that no venues.yml row claims.

    With `deep`, each match's own site is also checked for a second track.
    """
    disc = config.get("workshop_discovery") or {}
    domains = disc.get("domains") or []
    if not domains:
        return []
    include = [k.lower() for k in disc.get("keywords") or []]
    exclude = [k.lower() for k in disc.get("exclude_keywords") or []]
    if not include:
        return []

    print(f"· openreview workshop discovery across {len(domains)} conference(s)")
    found = []
    for domain in domains:
        workshops = openreview_workshops(domain)
        if not workshops:
            continue
        label = domain.replace(".cc", "").replace("/", " ")
        matched = []
        for ws in workshops:
            if ws["id"] in claimed:
                continue
            haystack = " ".join((ws["short"], ws["title"], ws["subtitle"])).lower()
            if not any(k in haystack for k in include):
                continue
            if any(k in haystack for k in exclude):
                continue
            matched.append(ws)
        print(f"  {domain}: {len(workshops)} workshops, {len(matched)} match your interests")

        # Serial, with a pause between calls — OpenReview 429s on concurrent bursts.
        due = []
        for ws in matched:
            due.append(openreview_duedate(ws["submission_id"]) if ws["submission_id"]
                       else (None, None))
            time.sleep(0.4)

        for ws, (when, precise) in zip(matched, due):
            name = ws["subtitle"] or ws["short"]
            if ws["title"] and ws["title"].lower() != name.lower():
                name = f"{ws['short']} — {ws['title']}"
            record = {
                "key": slug(f"{domain}-{ws['short']}"),
                "name": name[:150],
                "link": normalise_url(ws["website"]) or f"https://openreview.net/group?id={ws['id']}",
                "date": when.isoformat() if when else "",
                "approx": False,
                "track": f"{label} workshop",
                "status": "Not started",
                "source": "openreview",
                "confidence": "confirmed" if when else "unannounced",
                "note": "",
                "openreview": ws["id"],
            }
            record["fit"] = infer_fit(name + " " + ws["title"], config.get("projects"))
            record["kind"] = "workshop"
            labs = labs_for(record["key"], normalise_url(ws["website"]),
                            do_labs, labs_cache if labs_cache is not None else {})
            if labs:
                record["labs"] = labs
                record["labs_auto"] = True
            if precise:
                record["note"] = (
                    f"Submission closes {precise}. "
                    f"Submit via https://openreview.net/group?id={ws['id']}")
                if deep and when:
                    others = find_other_tracks(normalise_url(ws["website"]), when)
                    if others:
                        record["tracks"] = [o[0].isoformat() for o in others]
                        record["note"] += (
                            " SECOND TRACK LIKELY: the workshop site also lists " +
                            "; ".join(f"{o[0].isoformat()} (\"{o[1]}\")" for o in others) +
                            ". Often a later non-archival extended-abstract track — "
                            "which one you want is a strategic choice, since archival "
                            "publication can block a later main-conference submission.")
                    else:
                        record["note"] += " " + MULTITRACK_WARNING
                else:
                    record["note"] += " " + MULTITRACK_WARNING
            else:
                record["note"] = (
                    "Accepted workshop, but organizers have not set a submission deadline on "
                    f"OpenReview yet. Watch https://openreview.net/group?id={ws['id']}")
            found.append(record)
    return found


# --------------------------------------------------------------------------
# source: aggregator feeds
# --------------------------------------------------------------------------

def load_hf() -> dict[str, list[dict]]:
    print("· huggingface/ai-deadlines")
    blob = try_fetch(HF_TARBALL, binary=True, label="ai-deadlines tarball")
    if not blob:
        return {}
    out: dict[str, list[dict]] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if "/src/data/conferences/" not in member.name or not member.name.endswith(".yml"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            try:
                entries = yaml.safe_load(handle.read().decode("utf-8")) or []
            except yaml.YAMLError:
                continue
            for entry in entries:
                if isinstance(entry, dict) and entry.get("title"):
                    out.setdefault(str(entry["title"]).upper(), []).append(entry)
    print(f"  {len(out)} venues")
    return out


def load_ccf() -> dict[str, list[dict]]:
    print("· ccfddl/ccf-deadlines")
    text = try_fetch(CCF_ALLCONF, label="ccfddl allconf.yml")
    if not text:
        return {}
    try:
        data = yaml.safe_load(text) or []
    except yaml.YAMLError as exc:
        print(f"  ! unparseable: {exc}", file=sys.stderr)
        return {}
    out: dict[str, list[dict]] = {}
    for group in data:
        if isinstance(group, dict) and group.get("title"):
            for conf in group.get("confs") or []:
                if isinstance(conf, dict):
                    out.setdefault(str(group["title"]).upper(), []).append(conf)
    print(f"  {len(out)} venues")
    return out


def feed_deadlines(title_keys, hf, ccf) -> list[dict]:
    """Every abstract/paper deadline both feeds know about, oldest first."""
    out = []
    for key in title_keys:
        key = key.upper()
        for entry in hf.get(key, []):
            for deadline in entry.get("deadlines") or []:
                if not isinstance(deadline, dict):
                    continue
                if deadline.get("type") not in ("abstract", "paper", None):
                    continue
                when = parse_iso(deadline.get("date"))
                if when:
                    out.append({"date": when, "source": "huggingface/ai-deadlines",
                                "link": entry.get("link") or "",
                                "label": deadline.get("label") or deadline.get("type") or "deadline"})
        for conf in ccf.get(key, []):
            for line in conf.get("timeline") or []:
                if not isinstance(line, dict):
                    continue
                for field, label in (("abstract_deadline", "abstract"), ("deadline", "paper")):
                    when = parse_iso(line.get(field))
                    if when:
                        out.append({"date": when, "source": "ccfddl",
                                    "link": conf.get("link") or "", "label": label})
    out.sort(key=lambda d: d["date"])
    return out


def project(history: list[dict]):
    """Roll the most recent past deadline forward a year as a planning estimate.

    Conference deadlines are strongly annual, so this lands close — but it is
    arithmetic, not a published date, so callers label it `projected` and show
    the editions it came from.
    """
    past = [d for d in history if d["date"] < TODAY]
    if not past:
        return None
    guess = add_year(past[-1]["date"])
    while guess < TODAY:
        guess = add_year(guess)
    return {"date": guess, "link": past[-1]["link"], "source": past[-1]["source"],
            "basis": ", ".join(d["date"].isoformat() for d in past[-3:])}


# --------------------------------------------------------------------------
# source: probing a CFP page
# --------------------------------------------------------------------------

def strip_html(html: str) -> str:
    # Comments first, and before the generic tag strip — `<[^>]+>` cannot remove a
    # multi-line comment containing a ">", so its contents would otherwise survive
    # into the text. Workshop sites routinely leave a previous edition's speaker
    # list or schedule commented out, and reading a date or a name out of one is
    # worse than reading nothing: it looks like this year's information.
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<(br|/tr|/p|/li|/div|/h[1-6])[^>]*>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&#8217;", "'"),
                         ("&rsquo;", "'"), ("&ndash;", "-"), ("&mdash;", "—")):
        html = html.replace(entity, char)
    return re.sub(r"[ \t]+", " ", html)


def probe(url: str, horizon_days: int = 540):
    """Read candidate submission deadlines off an unstructured CFP page."""
    html = try_fetch(url, label=f"probe {url}")
    if not html:
        return None, []
    horizon = TODAY + dt.timedelta(days=horizon_days)
    candidates = []
    for line in (ln.strip() for ln in strip_html(html).split("\n")):
        if not line or len(line) > 400:
            continue
        if not DEADLINE_KEYWORDS.search(line) or EXCLUDE_KEYWORDS.search(line):
            continue
        for when in dates_in(line):
            if TODAY <= when <= horizon:
                candidates.append((when, re.sub(r"\s+", " ", line)[:200]))
    if not candidates:
        return None, []
    candidates.sort(key=lambda pair: pair[0])
    seen, evidence = set(), []
    for when, line in candidates:
        if line not in seen:
            seen.add(line)
            evidence.append(f"{when.isoformat()} — {line}")
    return candidates[0][0], evidence[:3]


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def load_my_venues() -> list:
    """Conferences added from the terminal (board.py --venue). Fed through the
    same pipeline as venues.yml, so they get feed matching, odds and fit too."""
    if not os.path.exists(MY_VENUES_FILE):
        return []
    try:
        with open(MY_VENUES_FILE, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"  ! my_venues.yml unreadable, ignoring it: {exc}", file=sys.stderr)
        return []
    return [v for v in (data.get("venues") or []) if isinstance(v, dict) and v.get("name")]


def load_tasks() -> list:
    """Tasks added from the terminal (board.py --add). Folded in on every run so
    the web board shows them alongside scraped deadlines."""
    if not os.path.exists(TASKS_FILE):
        return []
    try:
        with open(TASKS_FILE, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"  ! tasks.yml unreadable, ignoring it: {exc}", file=sys.stderr)
        return []
    out = []
    for t in (data.get("tasks") or []):
        if not isinstance(t, dict) or not t.get("text"):
            continue
        due = parse_iso(t.get("due"))
        out.append({
            "key": "task-" + str(t.get("id", slug(str(t["text"])))),
            "name": str(t["text"]),
            "link": "", "date": due.isoformat() if due else "", "approx": False,
            "track": "Task", "note": "", "source": "tasks.yml",
            "confidence": "manual", "kind": "task", "fit": [],
            "status": "Submitted" if t.get("done") else "Not started",
            "is_task": True,
        })
    return out


def load_state() -> dict:
    """Statuses set from the terminal (board.py --mark), applied on every run so
    they survive a re-scrape and reach the web board."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"  ! state.yml unreadable, ignoring it: {exc}", file=sys.stderr)
        return {}
    return {k: str(v) for k, v in (data.get("status") or {}).items()}


def load_labs_cache() -> dict:
    """Auto-read rosters from the last `--labs` run, keyed by venue.

    Scanning costs four page fetches per venue and roughly twenty minutes across
    the board, so it is opt-in. Without a cache the rosters would then vanish on
    the very next plain `scrape.py`, which is every routine refresh — the feature
    would appear to work once and then quietly delete itself.
    """
    if not os.path.exists(LABS_FILE):
        return {}
    try:
        with open(LABS_FILE, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"  ! labs_cache.yml unreadable, ignoring it: {exc}", file=sys.stderr)
        return {}
    return {k: list(v) for k, v in (data.get("labs") or {}).items() if v}


def save_labs_cache(cache: dict) -> None:
    body = {"labs": {k: cache[k] for k in sorted(cache)}}
    header = (
        "# Industry labs read off each venue's own pages by `scrape.py --labs`.\n"
        "# Regenerated whenever --labs runs; reused on every other run so a plain\n"
        "# refresh does not blank the board. Delete a line to force a re-read.\n"
        "#\n"
        "# NOT verified. The scanner cannot tell an organizer from a sponsor logo\n"
        "# from an intern, so treat a name here as a reason to open the page.\n"
        "# Hand-checked rosters belong in venues.yml under `labs:`, which wins.\n\n"
    )
    try:
        with open(LABS_FILE, "w", encoding="utf-8") as fh:
            fh.write(header)
            yaml.safe_dump(body, fh, sort_keys=True, allow_unicode=True)
    except OSError as exc:
        print(f"  ! could not write labs_cache.yml: {exc}", file=sys.stderr)


def load_dropped() -> set:
    """Venue keys the user dropped from the terminal board."""
    if not os.path.exists(DROPPED_FILE):
        return set()
    try:
        with open(DROPPED_FILE, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"  ! dropped.yml unreadable, ignoring it: {exc}", file=sys.stderr)
        return set()
    return {d["key"] for d in (data.get("dropped") or []) if isinstance(d, dict) and d.get("key")}


def build(do_probe: bool, do_labs: bool = False) -> dict:
    global LOCAL
    labs_cache = load_labs_cache()
    with open(VENUES_FILE, encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if config.get("timezone"):
        LOCAL = ZoneInfo(config["timezone"])

    added = load_my_venues()
    if added:
        config.setdefault("venues", []).extend(added)
        print(f"· {len(added)} venue(s) you added from the terminal")

    hf, ccf = load_hf(), load_ccf()

    venues, probe_jobs, claimed = [], [], set()

    for item in config.get("venues") or []:
        name = item["name"]
        record = {
            "key": item.get("key") or slug(name),
            "name": name,
            "link": item.get("link") or "",
            "date": item.get("date") or "",
            "approx": bool(item.get("approx")),
            "track": item.get("track") or "",
            "note": (item.get("note") or "").strip(),
            "status": item.get("status", "Not started"),
            "source": "venues.yml",
            "confidence": item.get("confidence", "manual"),
            "fit": (normalise_fit(item.get("fit"), config.get("projects"))
                    or infer_fit(name, config.get("projects"))),
            "goal": bool(item.get("goal")),
            "kind": item.get("kind", "workshop" if "workshop" in (item.get("track") or "").lower()
                             else "selective"),
        }
        if isinstance(item.get("date"), (dt.date, dt.datetime)):
            record["date"] = parse_iso(item["date"]).isoformat()

        # A hand-written roster is a verified one and always wins; `--labs` only
        # fills the gap where nobody has looked yet.
        if item.get("labs"):
            record["labs"] = list(item["labs"])
            if item.get("labs_note"):
                record["labs_note"] = str(item["labs_note"]).strip()
        elif record["kind"] == "workshop":
            labs = labs_for(record["key"], record["link"], do_labs, labs_cache)
            if labs:
                record["labs"] = labs
                record["labs_auto"] = True

        # An OpenReview id makes the workshop's own invitation authoritative.
        if item.get("openreview"):
            claimed.add(item["openreview"])
            record["openreview"] = item["openreview"]
            sub = f"{item['openreview']}/-/Submission"
            when, precise = openreview_duedate(sub)
            if when:
                if record["date"] and when.isoformat() != record["date"]:
                    record["note"] = _append(
                        record["note"],
                        f"Deadline moved from {record['date']} to {when.isoformat()} "
                        f"per OpenReview.")
                record["date"] = when.isoformat()
                record["approx"] = False
                record["confidence"] = "confirmed"
                record["source"] = "openreview"
                record["note"] = _append(record["note"], f"Submission closes {precise}.")

        titles = item.get("match") or ([] if item.get("no_feed") else [name])
        history = feed_deadlines(titles, hf, ccf) if titles else []
        future = [d for d in history if d["date"] >= TODAY]

        if record["confidence"] == "confirmed" and record["source"] == "openreview":
            pass                                  # OpenReview already won
        elif future:
            hit = future[0]
            found = hit["date"].isoformat()
            if item.get("pin_date"):
                if found != record["date"]:
                    record["note"] = _append(
                        record["note"],
                        f"{hit['source']} reports {found} for the {hit['label']} deadline; "
                        f"this row pins {record['date']}.")
            else:
                if record["date"] and found != record["date"]:
                    record["note"] = _append(
                        record["note"],
                        f"Corrected from {record['date']} to {found} "
                        f"({hit['label']} deadline, via {hit['source']}).")
                record["date"] = found
                record["approx"] = False
                record["confidence"] = "confirmed"
                record["source"] = hit["source"]
            if hit["link"] and not record["link"]:
                record["link"] = hit["link"]
        elif titles and not record["date"]:
            guess = project(history)
            if guess:
                record["date"] = guess["date"].isoformat()
                record["approx"] = True
                record["confidence"] = "projected"
                record["source"] = f"projected from {guess['source']}"
                record["note"] = _append(
                    record["note"],
                    f"No call posted yet. Estimated by rolling the last edition forward "
                    f"a year; recent deadlines were {guess['basis']}.")
                if guess["link"] and not record["link"]:
                    record["link"] = guess["link"]
            else:
                record["confidence"] = "unannounced"
                record["note"] = _append(
                    record["note"], "No deadline in any feed and no history to project from.")

        if do_probe and item.get("probe") and record["link"]:
            probe_jobs.append(record)
        venues.append(record)

    venues.extend(discover_workshops(config, claimed, deep=do_probe,
                                     do_labs=do_labs, labs_cache=labs_cache))

    # Awards and fellowships share the timeline — the whole point is seeing them
    # against paper deadlines — but they carry gates a conference doesn't have.
    # A nomination deadline is the real deadline when one exists.
    for item in config.get("awards") or []:
        record = {
            "key": item.get("key") or slug("award-" + item["name"]),
            "name": item["name"],
            "link": item.get("link") or "",
            "date": item.get("date") or "",
            "approx": bool(item.get("approx")),
            "track": item.get("track") or "Award",
            "note": (item.get("note") or "").strip(),
            "status": item.get("status", "Not started"),
            "source": item.get("source", "venues.yml"),
            "confidence": item.get("confidence", "manual"),
            "kind": item.get("kind", "fellowship"),
            "fit": normalise_fit(item.get("fit"), config.get("projects")),
            "is_award": True,
            "goal": bool(item.get("goal")),
            "eligibility": (item.get("eligibility") or "").strip(),
            "award": (item.get("award") or "").strip(),
            "nomination": bool(item.get("nomination")),
            "campus_deadline": item.get("campus_deadline") or "",
        }
        if isinstance(item.get("date"), (dt.date, dt.datetime)):
            record["date"] = parse_iso(item["date"]).isoformat()
        if isinstance(item.get("campus_deadline"), (dt.date, dt.datetime)):
            record["campus_deadline"] = parse_iso(item["campus_deadline"]).isoformat()
        # Sort on whichever gate comes first, so a campus deadline can't slip past.
        if record["campus_deadline"] and (
                not record["date"] or record["campus_deadline"] < record["date"]):
            record["note"] = _append(
                record["note"],
                f"Sorted by the internal nomination deadline ({record['campus_deadline']}); "
                f"the sponsor's own deadline is {record['date'] or 'unannounced'}.")
            record["date"] = record["campus_deadline"]
        venues.append(record)

    if probe_jobs:
        print(f"· probing {len(probe_jobs)} CFP pages")
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda r: probe(r["link"]), probe_jobs))
        for record, (found, evidence) in zip(probe_jobs, results):
            if not found:
                continue
            iso = found.isoformat()
            if not record["date"]:
                record.update(date=iso, approx=False, confidence="scraped",
                              source="scraped from CFP page")
                record["note"] = _append(
                    record["note"],
                    "Date read automatically off the CFP page — confirm before relying on it. "
                    "Evidence: " + " | ".join(evidence))
            elif iso != record["date"] and record["confidence"] != "confirmed":
                record["note"] = _append(
                    record["note"],
                    f"The CFP page also mentions {iso}. Evidence: " + " | ".join(evidence))

    # People to contact. No mailbox is connected, so "last contacted" is whatever
    # you last recorded; the next nudge is derived from it rather than observed.
    for item in config.get("outreach") or []:
        cadence = int(item.get("cadence_days") or 14)
        last = parse_iso(item.get("last_contacted"))
        first_by = parse_iso(item.get("first_contact_by"))
        if last:
            due = last + dt.timedelta(days=cadence)
            stage = f"Follow-up, {cadence} days after you last wrote on {last.isoformat()}."
        elif first_by:
            due = first_by
            stage = "First contact — nothing sent yet."
        else:
            due, stage = None, "No target date set."

        record = {
            "key": item.get("key") or slug("outreach-" + item["name"]),
            "name": item["name"],
            "link": item.get("link") or "",
            "date": due.isoformat() if due else "",
            "approx": False,
            "track": item.get("track") or "Outreach",
            "note": _append((item.get("note") or "").strip(), stage),
            "status": item.get("status", "Not started"),
            "source": "venues.yml",
            "confidence": "manual",
            "kind": "outreach",
            "fit": normalise_fit(item.get("fit"), config.get("projects")),
            "is_outreach": True,
            "person": (item.get("person") or "").strip(),
            "affiliation": (item.get("affiliation") or "").strip(),
            "cadence_days": cadence,
            "last_contacted": last.isoformat() if last else "",
            "first_contact_by": first_by.isoformat() if first_by else "",
        }
        venues.append(record)

    # Odds last, so discovered workshops are scored the same way as hand-written rows.
    raw_projects = config.get("projects") or {}
    kinds = config.get("venue_kinds") or {}
    for venue in venues:
        venue.setdefault("kind", "workshop")
        if venue.get("odds"):
            venue.setdefault("odds_why", "Set by hand in venues.yml.")
        else:
            venue["odds"], venue["odds_why"] = estimate_odds(
                venue["kind"], venue.get("fit") or [], raw_projects, kinds,
                is_award=bool(venue.get("is_award")) or bool(venue.get("is_outreach")))

    # Anything dropped via `board.py --drop` stays dropped. Applied here rather
    # than by editing venues.yml, because OpenReview discovery would otherwise
    # rediscover a deleted workshop on the very next run.
    tasks = load_tasks()
    if tasks:
        venues.extend(tasks)
        print(f"· {len(tasks)} task(s) from tasks.yml")

    state = load_state()
    if state:
        hit = 0
        for v in venues:
            if v.get("key") in state:
                v["status"] = state[v["key"]]
                hit += 1
        if hit:
            print(f"· {hit} status(es) applied from state.yml")

    dropped = load_dropped()
    if dropped:
        before = len(venues)
        venues = [v for v in venues if v.get("key") not in dropped]
        gone = before - len(venues)
        if gone:
            print(f"· {gone} dropped item(s) excluded (see dropped.yml)")

    venues.sort(key=lambda v: (v["date"] == "", v["date"], v["name"]))
    projects = {k: {"name": v.get("name", k), "blurb": v.get("blurb", ""),
                    "readiness": v.get("readiness", "partial"),
                    "readiness_note": (v.get("readiness_note") or "").strip()}
                for k, v in raw_projects.items()}
    save_labs_cache(labs_cache)
    return {"generated": dt.datetime.now(LOCAL).replace(microsecond=0).isoformat(),
            "projects": projects, "venues": venues}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", action="store_true",
                        help="also read dates off CFP pages marked `probe: true`")
    parser.add_argument("--labs", action="store_true",
                        help="also read organizer and speaker pages for industry labs "
                             "(Google DeepMind, OpenAI, Genentech, ...). Slow: it "
                             "fetches up to four pages per workshop.")
    parser.add_argument("-o", "--out", default=OUT_FILE)
    args = parser.parse_args()

    data = build(args.probe, args.labs)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    dated = [v for v in data["venues"] if v["date"]]
    soon = [v for v in dated if 0 <= (parse_iso(v["date"]) - TODAY).days <= 45]
    print(f"\nWrote {args.out}")
    print(f"  {len(data['venues'])} venues · {len(dated)} dated · {len(soon)} open within 45 days\n")
    for venue in soon:
        days = (parse_iso(venue["date"]) - TODAY).days
        print(f"  {days:>3}d  {venue['date']}  {venue['confidence']:<11} {venue['name'][:62]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
