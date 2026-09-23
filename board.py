#!/usr/bin/env python3
"""Read the deadline board in the terminal.

    python3 board.py                  what closes in the next 30 days
    python3 board.py --days 7         just this week
    python3 board.py --all            everything, including undated
    python3 board.py -p prism         only what PRISM could go to
    python3 board.py -t outreach      only emails you owe people
    python3 board.py --check          only dates that still need verifying
    python3 board.py -s neurips       search names, notes and eligibility
    python3 board.py --show <n>       full detail for row n, including why it fits
    python3 board.py --drop <n>       drop a deadline that no longer applies
    python3 board.py --dropped        list what you have dropped
    python3 board.py --restore <key>  put one back
    python3 board.py --mark <n> submitted   record that you submitted it
    python3 board.py --open           open the web board in your browser
    python3 board.py --refresh        re-scrape, rebuild the page, then show it

  Calendar
    python3 board.py --cal                     this month as a grid
    python3 board.py --cal --month 2026-08     a specific month

  Projects — just name one to see everything you could submit it to
    python3 board.py biocl              every venue that fits biocl, any date
    python3 board.py prism --days 60    narrow it back down

  Tracking a new conference
    python3 board.py --venue "IEEE EMBC 2027" --on 2027-01-17 \
        --link https://embc.embs.org/2027/ --track Bioengineering --fit clera,cortseer
    python3 board.py --venues            list the ones you added this way

  Tasks — things to do with no deadline attached
    python3 board.py --add "email Cognita AI"          open task, no date
    python3 board.py --add "email Ashmitha" --on 8-14  put it on the calendar
    python3 board.py --tasks                            list open tasks
    python3 board.py --due 2 8-14                       give an existing task a date
    python3 board.py --due 2 none                       take the date back off
    python3 board.py --done 2                           check one off
    python3 board.py --undone 2  /  --rm 2              undo  /  delete

Reads deadlines.json, which `scrape.py` writes. Nothing here hits the network,
so it is instant and works offline.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import textwrap
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "deadlines.json")
DROPPED = os.path.join(HERE, "dropped.yml")
PAGE = os.path.join(HERE, "index.html")
STATE = os.path.join(HERE, "state.yml")
TASKS = os.path.join(HERE, "tasks.yml")
MINE = os.path.join(HERE, "my_venues.yml")

TODAY = dt.date.today()

# Colour only when attached to a terminal that wants it.
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text, *codes):
    if not COLOR or not codes:
        return text
    return "\033[" + ";".join(str(x) for x in codes) + "m" + text + "\033[0m"


BOLD, DIM, ITALIC = 1, 2, 3
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, GREY = 31, 32, 33, 34, 35, 36, 90
BRIGHT_RED = 91

SOFT = {"projected", "scraped", "unannounced"}
PROJECT_COLOR = {"prism": GREEN, "clera": CYAN, "biocl": MAGENTA,
                 "cortseer": YELLOW, "deejai": BLUE, "igbot": GREY}

STATUSES = ["not started", "drafting", "submitted", "accepted", "rejected", "skipped"]
STATUS_COLOR = {"drafting": YELLOW, "submitted": BLUE, "accepted": GREEN,
                "rejected": GREY, "skipped": GREY}


def load():
    if not os.path.exists(DATA):
        sys.exit(f"{DATA} not found — run:  python3 scrape.py")
    with open(DATA, encoding="utf-8") as fh:
        return json.load(fh)


def load_dropped() -> dict:
    """Keys you have dropped -> the record of why.

    Kept in its own file rather than by deleting from venues.yml, because the
    scraper rediscovers workshops from OpenReview every run — a deletion there
    would silently come back. scrape.py reads this file and excludes them.
    """
    if not os.path.exists(DROPPED):
        return {}
    try:
        import yaml
        data = yaml.safe_load(open(DROPPED, encoding="utf-8")) or {}
    except Exception:
        return {}
    out = {}
    for d in (data.get("dropped") or []):
        if not isinstance(d, dict) or not d.get("key"):
            continue
        # YAML turns a bare 2026-07-30 into a date object; keep everything as
        # text so formatting never has to care which it got.
        d = dict(d)
        for field in ("at", "name", "reason"):
            if d.get(field) is not None:
                d[field] = str(d[field])
        out[d["key"]] = d
    return out


def save_dropped(records: list) -> None:
    lines = ["# Deadlines you have dropped from the board.",
             "#",
             "# scrape.py excludes these on every run, so a dropped item stays gone even",
             "# though the scraper would otherwise rediscover it. Nothing is deleted —",
             "# restore any of them with:  python3 board.py --restore <key>",
             "",
             "dropped:"]
    for r in sorted(records, key=lambda x: x.get("at", "")):
        lines.append("  - key: %s" % r["key"])
        lines.append("    name: %s" % json.dumps(r.get("name", ""), ensure_ascii=False))
        lines.append("    at: %s" % r.get("at", ""))
        if r.get("reason"):
            lines.append("    reason: %s" % json.dumps(r["reason"], ensure_ascii=False))
    with open(DROPPED, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def load_state() -> dict:
    """Per-venue status you set from the terminal. scrape.py folds this into
    deadlines.json, so the web board picks it up on the next rebuild."""
    if not os.path.exists(STATE):
        return {}
    try:
        import yaml
        data = yaml.safe_load(open(STATE, encoding="utf-8")) or {}
    except Exception:
        return {}
    return {k: str(v) for k, v in (data.get("status") or {}).items()}


def save_state(status: dict) -> None:
    lines = ["# Status you have set from the terminal.",
             "#",
             "# scrape.py folds these into deadlines.json, so the web board shows them",
             "# too after the next `build.py`. Editing a status in the browser overrides",
             "# whatever is here, for that browser only.",
             "",
             "status:"]
    for k in sorted(status):
        lines.append("  %s: %s" % (k, status[k]))
    with open(STATE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def load_my_venues() -> list:
    if not os.path.exists(MINE):
        return []
    try:
        import yaml
        data = yaml.safe_load(open(MINE, encoding="utf-8")) or {}
    except Exception:
        return []
    return [v for v in (data.get("venues") or []) if isinstance(v, dict) and v.get("name")]


def save_my_venues(venues: list) -> None:
    """Kept apart from venues.yml so the curated file and its comments stay intact.
    scrape.py reads this through the same pipeline, so feed matching, odds and
    projection all still apply to whatever you add here."""
    lines = ["# Conferences you added from the terminal with:  board.py --venue \"...\"",
             "# scrape.py reads these exactly like venues.yml entries, so they still get",
             "# feed lookup, odds and project fit. Edit or delete freely.",
             "",
             "venues:"]
    for v in venues:
        lines.append("  - name: %s" % json.dumps(v["name"], ensure_ascii=False))
        for k in ("link", "date", "track", "note"):
            if v.get(k):
                lines.append("    %s: %s" % (k, json.dumps(str(v[k]), ensure_ascii=False)))
        if v.get("match"):
            lines.append("    match: [%s]" % ", ".join(v["match"]))
        else:
            lines.append("    no_feed: true")
        if v.get("pin_date"):
            lines.append("    pin_date: true")
        if v.get("probe"):
            lines.append("    probe: true")
        if v.get("fit"):
            lines.append("    fit: [%s]" % ", ".join(v["fit"]))
        lines.append("    added: %s" % v.get("added", TODAY.isoformat()))
    with open(MINE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def load_tasks() -> list:
    """Your own to-dos. Separate from venues.yml because these are yours, not
    scraped — nothing upstream should ever be able to rewrite them."""
    if not os.path.exists(TASKS):
        return []
    try:
        import yaml
        data = yaml.safe_load(open(TASKS, encoding="utf-8")) or {}
    except Exception:
        return []
    out = []
    for t in (data.get("tasks") or []):
        if not isinstance(t, dict) or not t.get("text"):
            continue
        t = dict(t)
        for f in ("due", "created", "id", "text"):
            if t.get(f) is not None:
                t[f] = str(t[f])
        t["done"] = bool(t.get("done"))
        out.append(t)
    return out


def save_tasks(tasks: list) -> None:
    lines = ["# Your own tasks. Add with:  board.py --add \"...\"",
             "# Give one a date with --on to put it on the calendar; leave it off and it",
             "# lives under 'No date / rolling'. Check off with --done <n>.",
             "",
             "tasks:"]
    for t in tasks:
        lines.append("  - id: %s" % t["id"])
        lines.append("    text: %s" % json.dumps(t["text"], ensure_ascii=False))
        if t.get("due"):
            lines.append("    due: %s" % t["due"])
        lines.append("    done: %s" % ("true" if t.get("done") else "false"))
        lines.append("    created: %s" % t.get("created", ""))
    with open(TASKS, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def next_task_id(tasks: list) -> str:
    n = 0
    for t in tasks:
        m = re.match(r"t(\d+)$", t.get("id", ""))
        if m:
            n = max(n, int(m.group(1)))
    return "t%d" % (n + 1)


def task_as_item(t: dict) -> dict:
    """Shape a task like a board item so one renderer handles both."""
    return {
        "key": "task-" + t["id"], "name": t["text"], "link": "",
        "date": t.get("due") or "", "approx": False, "track": "Task",
        "note": "", "status": "Submitted" if t.get("done") else "Not started",
        "source": "tasks.yml", "confidence": "manual", "kind": "task",
        "fit": [], "odds": "", "odds_why": "", "is_task": True,
        "task_id": t["id"], "done": bool(t.get("done")),
    }


def finish(args, list_tasks):
    """After a change, honour any view flag given in the same command.

    Returns 0 to stop, or None to fall through to the board/calendar renderer —
    so `--due 11 09/15 --cal` sets the date AND shows you the month.
    """
    if args.tasks:
        list_tasks()
        return 0
    if args.cal or args.show is not None:
        return None            # keep going; the renderer runs below
    return 0


def parse_when(text: str):
    """Accept 2026-08-14, 8-14, 8/14, or 14 (this month). Year rolls forward."""
    text = (text or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})$", text)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        y = TODAY.year
        try:
            cand = dt.date(y, mo, d)
        except ValueError:
            return None
        return cand if cand >= TODAY else dt.date(y + 1, mo, d)
    m = re.match(r"^(\d{1,2})$", text)
    if m:
        d = int(m.group(1))
        try:
            cand = TODAY.replace(day=d)
        except ValueError:
            return None
        if cand < TODAY:
            mo, y = TODAY.month + 1, TODAY.year
            if mo > 12:
                mo, y = 1, y + 1
            try:
                cand = dt.date(y, mo, d)
            except ValueError:
                return None
        return cand
    return None


def open_page() -> int:
    """Open the web board. The page is self-contained, so file:// is enough."""
    if not os.path.exists(PAGE):
        sys.exit(f"{PAGE} not found — run:  python3 scrape.py && python3 build.py")
    url = "file://" + PAGE
    # `open` on macOS handles the default browser better than webbrowser does;
    # fall back to the stdlib elsewhere.
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", PAGE], check=True)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", PAGE], check=True)
        else:
            webbrowser.open(url)
    except (OSError, subprocess.CalledProcessError):
        webbrowser.open(url)
    print(c("\n  Opened " + PAGE + "\n", GREY))
    return 0


def refresh() -> int:
    """Re-scrape and rebuild, so the page you open is current."""
    for step, args in (("scraping", ["scrape.py", "--probe"]), ("baking", ["build.py"])):
        print(c("  " + step + "...", GREY))
        r = subprocess.run([sys.executable, os.path.join(HERE, args[0])] + args[1:],
                           cwd=HERE)
        if r.returncode != 0:
            sys.exit("  %s failed (exit %d) — page left untouched." % (step, r.returncode))
    return 0


def parse(value):
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def days_until(value):
    day = parse(value)
    return None if day is None else (day - TODAY).days


def urgency_color(n):
    if n is None:
        return GREY
    if n < 0:
        return GREY
    if n <= 7:
        return BRIGHT_RED
    if n <= 30:
        return YELLOW
    if n <= 90:
        return GREEN
    return GREY


def kind_of(v):
    if v.get("is_task"):
        return "task"
    if v.get("is_outreach"):
        return "outreach"
    if v.get("is_award"):
        return "award"
    return "paper"


MARK = {"paper": "*", "award": "+", "outreach": "@", "task": "o"}


def needs_check(v):
    return v.get("confidence") in SOFT or (v.get("approx") and v.get("date"))


def matches(v, args):
    if args.type and kind_of(v) != args.type:
        return False
    if getattr(args, "lab", None):
        want = args.lab.lower()
        if want == "any":
            if not v.get("labs"):
                return False
        elif not any(want in lab.lower() for lab in v.get("labs") or []):
            return False
    if args.project and not any(f.get("project") == args.project for f in v.get("fit") or []):
        return False
    if args.check and not needs_check(v):
        return False
    if args.search:
        hay = " ".join([
            v.get("name", ""), v.get("note", ""), v.get("track", ""),
            v.get("eligibility", ""), v.get("link", ""),
            " ".join(f.get("project", "") for f in v.get("fit") or []),
            " ".join(v.get("labs") or []), v.get("labs_note", ""),
        ]).lower()
        if args.search.lower() not in hay:
            return False
    return True


def width():
    try:
        return max(60, min(os.get_terminal_size().columns, 120))
    except OSError:
        return 100


def render_row(idx, v, cols):
    n = days_until(v["date"])
    col = urgency_color(n)
    kind = kind_of(v)

    if n is None:
        left = c("  --", GREY)
    elif n < 0:
        left = c(f"{-n:4d}d", GREY)
    else:
        left = c(f"{n:4d}d", col, BOLD if n <= 7 else 0)

    day = parse(v["date"])
    when = c(day.strftime("%b %d"), col) if day else c(" -- --", GREY)
    mark = c(MARK[kind], col)

    fits = v.get("fit") or []
    tag = ""
    if fits:
        p = fits[0]["project"]
        tag = c(p.upper()[:8], PROJECT_COLOR.get(p, GREY))

    odds = v.get("odds", "")
    odds_txt = {"likely": c("likely", GREEN), "plausible": c("plausible", YELLOW),
                "longshot": c("longshot", GREY)}.get(odds, "")

    flag = c(" !", YELLOW) if needs_check(v) else "  "

    # Who is in the room. Bright for the frontier labs, dim for everyone else —
    # the point of the marker is to make a roster worth reading stand out from a
    # roster that merely exists.
    labs = v.get("labs") or []
    if any(l in ("Google DeepMind", "OpenAI", "Anthropic", "Meta FAIR") for l in labs):
        lab_mark = c("◆", GREEN, BOLD)
    elif labs:
        lab_mark = c("◆", GREY)
    else:
        lab_mark = " "

    st = (v.get("status") or "Not started").lower()
    # A task isn't "submitted", it's done — same field, different vocabulary.
    if kind == "task":
        status_txt = c("done", GREEN) if st in ("submitted", "accepted") else ""
    else:
        status_txt = c(st, STATUS_COLOR.get(st, GREY)) if st != "not started" else ""

    # Visible width excludes the ANSI escapes, so budget the name from raw lengths.
    used = 5 + 1 + 6 + 1 + 1 + 1 + 4 + 9 + 10 + 11 + 2 + 2
    name_w = max(20, cols - used)
    name = v["name"]
    if len(name) > name_w:
        name = name[: name_w - 1] + "…"

    return (f"{c(f'{idx:3d}', GREY)} {left} {when} {mark}{flag}{lab_mark} "
            f"{name:<{name_w}} {tag:<8} {odds_txt:<20} {status_txt}")


GROUPS = [
    ("OVERDUE / CLOSED", lambda n: n is not None and n < 0),
    ("CLOSING WITHIN 7 DAYS", lambda n: n is not None and 0 <= n <= 7),
    ("NEXT 30 DAYS", lambda n: n is not None and 7 < n <= 30),
    ("NEXT 90 DAYS", lambda n: n is not None and 30 < n <= 90),
    ("FURTHER OUT", lambda n: n is not None and n > 90),
    ("NO DATE / ROLLING", lambda n: n is None),
]


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def render_calendar(rows, year, month, cols):
    """A month grid, then the same month as an agenda.

    A terminal cell is ~13 characters wide, which cannot hold a venue name. So
    the grid carries the shape and a count, and the agenda underneath carries
    the detail — you get 'when is it busy' and 'what is it' without either
    fighting for the same space.
    """
    import calendar as calmod

    by_day = {}
    for v in rows:
        d = parse(v["date"])
        if d and d.year == year and d.month == month:
            by_day.setdefault(d.day, []).append(v)

    print()
    print("  " + c(MONTH_NAMES[month - 1] + " " + str(year), BOLD))
    print()
    print("  " + c("  Mon    Tue    Wed    Thu    Fri    Sat    Sun", GREY))

    weeks = calmod.Calendar(firstweekday=0).monthdayscalendar(year, month)
    for wk in weeks:
        line = "  "
        for day in wk:
            if day == 0:
                line += "       "
                continue
            items = by_day.get(day, [])
            soonest = None
            for v in items:
                n = days_until(v["date"])
                if n is not None and (soonest is None or n < soonest):
                    soonest = n
            is_today = (year, month, day) == (TODAY.year, TODAY.month, TODAY.day)
            num = "%2d" % day
            num = c(num, BOLD, 7) if is_today else (c(num, urgency_color(soonest))
                                                    if items else c(num, GREY))
            # Count sits in its own column; glued to the day number it read as
            # one number ("3015" for the 30th with 15 items).
            if items:
                open_n = sum(1 for v in items
                             if (v.get("status") or "Not started") not in
                             ("Submitted", "Accepted", "Skipped"))
                raw = ("%d" % len(items)) if open_n else "\u2713"
                badge = c(raw, urgency_color(soonest) if open_n else GREEN)
                line += num + " " + badge + " " * (4 - len(raw))
            else:
                line += num + "     "
        print(line.rstrip())

    if not by_day:
        print()
        print(c("  Nothing this month.", GREY))
        print()
        return

    print()
    for day in sorted(by_day):
        d = dt.date(year, month, day)
        head = d.strftime("%a %-d")
        for i, v in enumerate(sorted(by_day[day], key=lambda x: x["name"])):
            k = kind_of(v)
            st = (v.get("status") or "Not started").lower()
            done = st in ("submitted", "accepted", "skipped")
            label = c("%-7s" % (head if i == 0 else ""), urgency_color(days_until(v["date"])))
            name = v["name"][: max(24, cols - 26)]
            print("  " + label + c(MARK.get(k, "*"), GREY) + " " +
                  (c(name, GREY) if done else name) +
                  (c("  done", GREEN) if done else ""))
    print()


def show_detail(v, cols):
    wrap = lambda s, ind: textwrap.fill(s, width=cols - len(ind),
                                        initial_indent=ind, subsequent_indent=ind)
    print()
    print(c(v["name"], BOLD))
    n = days_until(v["date"])
    bits = []
    if v.get("date"):
        bits.append(("in %d days" % n) if n is not None and n >= 0
                    else ("%d days ago" % -n if n is not None else v["date"]))
        bits.append(v["date"])
    bits.append(kind_of(v))
    if v.get("track"):
        bits.append(v["track"])
    if v.get("confidence"):
        bits.append(v["confidence"])
    if v.get("odds"):
        bits.append("odds: " + v["odds"])
    print(c("  " + "  ".join(bits), GREY))
    if v.get("link"):
        print(c("  " + v["link"], BLUE))
    print()
    for f in v.get("fit") or []:
        head = "  %s (%s)" % (f["project"].upper(), f["strength"])
        print(c(head, PROJECT_COLOR.get(f["project"], GREY), BOLD))
        if f.get("why"):
            print(wrap(f["why"], "    "))
    if v.get("odds_why"):
        print()
        print(c("  ODDS", GREY, BOLD))
        print(wrap(v["odds_why"], "    "))
    if v.get("labs"):
        print()
        head = "  INDUSTRY" + ("  (auto-read — verify before trusting)"
                               if v.get("labs_auto") else "")
        print(c(head, GREY, BOLD))
        print(wrap(", ".join(v["labs"]), "    "))
        if v.get("labs_note"):
            print(wrap(v["labs_note"], "    "))
    for label, key in (("ELIGIBILITY", "eligibility"), ("YOU GET", "award"),
                       ("NOTES", "note")):
        if v.get(key):
            print()
            print(c("  " + label, GREY, BOLD))
            print(wrap(v[key], "    "))
    if v.get("nomination"):
        print()
        print(c("  Requires institutional nomination"
                + (" — internal deadline " + v["campus_deadline"]
                   if v.get("campus_deadline") else ""), YELLOW))
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_or_search", nargs="?", metavar="PROJECT",
                    help="name a project (biocl, prism, clera, cortseer...) to see "
                         "everything it fits; anything else is treated as a search")
    ap.add_argument("--days", type=int, default=30, help="horizon in days (default 30)")
    ap.add_argument("--all", action="store_true", help="no horizon; include undated")
    ap.add_argument("-t", "--type", choices=["paper", "award", "outreach", "task"])
    ap.add_argument("-p", "--project")
    ap.add_argument("-s", "--search")
    ap.add_argument("--lab", metavar="NAME",
                    help="only venues with industry people on the program. "
                         "Use `any`, or a lab name: --lab deepmind")
    ap.add_argument("--check", action="store_true", help="only dates needing verification")
    ap.add_argument("--past", action="store_true", help="include closed items")
    ap.add_argument("--show", type=int, metavar="N", help="full detail for row N")
    ap.add_argument("--drop", type=int, metavar="N",
                    help="drop row N — it stops appearing here and on the web board")
    ap.add_argument("--reason", help="why you dropped it (recorded alongside)")
    ap.add_argument("--dropped", action="store_true", help="list what you have dropped")
    ap.add_argument("--restore", metavar="KEY", help="put a dropped item back")
    ap.add_argument("--open", action="store_true", dest="open_page",
                    help="open the web board in your browser")
    ap.add_argument("--refresh", action="store_true",
                    help="re-scrape and rebuild before showing anything")
    ap.add_argument("--mark", nargs=2, metavar=("N", "STATUS"),
                    help="set row N's status: " + ", ".join(STATUSES))
    ap.add_argument("--cal", action="store_true", help="show the month as a calendar")
    ap.add_argument("--month", metavar="YYYY-MM", help="which month --cal shows")
    ap.add_argument("--add", metavar="TEXT", help="add a task of your own")
    ap.add_argument("--on", metavar="WHEN",
                    help="date for --add: 2026-08-14, 8-14, or 14")
    ap.add_argument("--tasks", action="store_true", help="list your tasks")
    ap.add_argument("--done", type=int, metavar="N", help="check off task N")
    ap.add_argument("--undone", type=int, metavar="N", help="un-check task N")
    ap.add_argument("--rm", type=int, metavar="N", help="delete task N")
    ap.add_argument("--due", nargs=2, metavar=("N", "WHEN"),
                    help="set task N's date; WHEN can be 'none' to clear it")
    ap.add_argument("--venue", metavar="NAME", help="start tracking a conference")
    ap.add_argument("--link", help="its call-for-papers URL")
    ap.add_argument("--track", help="grouping label, e.g. Bioengineering")
    ap.add_argument("--fit", help="comma-separated projects, e.g. clera,cortseer")
    ap.add_argument("--match", help="comma-separated feed titles to look up, e.g. EMBC")
    ap.add_argument("--venues", action="store_true", help="list conferences you added")
    args = ap.parse_args()

    if args.venues:
        mine = load_my_venues()
        if not mine:
            print(c("\n  None yet. Add one:\n"
                    "    board.py --venue \"IEEE EMBC 2027\" --on 2027-01-17\n", GREY))
            return 0
        print()
        print(c("  CONFERENCES YOU ADDED", BOLD, GREY) + c("  %d" % len(mine), GREY))
        for v in mine:
            print("  " + (v.get("date") or c("no date", GREY)) + "  " + v["name"])
            if v.get("link"):
                print(c("     " + v["link"], GREY))
        print(c("\n  Edit them in my_venues.yml. Re-run scrape.py to refresh.\n", GREY))
        return 0

    if args.venue:
        due = None
        if args.on:
            due = parse_when(args.on)
            if not due:
                sys.exit("Couldn't read a date from %r. Try 2027-01-17, 1-17, or 17." % args.on)
        mine = load_my_venues()
        if any(v["name"].lower() == args.venue.strip().lower() for v in mine):
            sys.exit("You already track %r. Edit my_venues.yml to change it." % args.venue)
        rec = {"name": args.venue.strip(), "added": TODAY.isoformat()}
        if due:
            rec["date"] = due.isoformat()
            rec["pin_date"] = True
        if args.link:
            rec["link"] = args.link.strip()
            rec["probe"] = True
        if args.track:
            rec["track"] = args.track.strip()
        if args.fit:
            rec["fit"] = [x.strip().lower() for x in args.fit.split(",") if x.strip()]
        if args.match:
            rec["match"] = [x.strip().upper() for x in args.match.split(",") if x.strip()]
        mine.append(rec)
        save_my_venues(mine)
        print()
        print(c("  Now tracking: ", GREEN) + rec["name"])
        if due:
            print(c("  %s (in %d days)" % (due.isoformat(), (due - TODAY).days), GREY))
        else:
            print(c("  No date yet \u2014 it sits under 'No date / rolling'.", GREY))
        if args.match:
            print(c("  Will look up %s in the aggregator feeds on the next scrape."
                    % ", ".join(rec["match"]), GREY))
        if args.link:
            print(c("  --probe will try to read a date off that page too.", GREY))
        print(c("\n  Run:  python3 scrape.py && python3 build.py   to fold it in.\n", GREY))
        return 0

    # A bare word that names a project filters by it and drops the date horizon —
    # "show me everywhere biocl could go" is a question about the whole calendar.
    if args.project_or_search:
        word = args.project_or_search.strip().lower()
        known = set()
        try:
            known = set(json.load(open(DATA, encoding="utf-8")).get("projects", {}))
        except Exception:
            pass
        if word in known:
            args.project = word
            if args.days == 30:
                args.all = True
        elif not args.search:
            args.search = args.project_or_search

    # ---- task commands, all before anything reads deadlines.json ----
    tasks = load_tasks()


    def list_tasks(open_only=False):
        shown = [t for t in tasks if not (open_only and t.get("done"))]
        if not shown:
            print(c("\n  No tasks yet. Add one:  board.py --add \"email Cognita AI\"\n", GREY))
            return []
        print()
        print(c("  TASKS", BOLD, GREY) + c("  %d" % len(shown), GREY))
        for i, t in enumerate(shown, 1):
            box = c("[x]", GREEN) if t.get("done") else c("[ ]", GREY)
            when = ""
            if t.get("due"):
                n = days_until(t["due"])
                when = c("  %s (%s)" % (t["due"], "today" if n == 0 else
                                        ("%dd" % n if n > 0 else "%dd ago" % -n)),
                         urgency_color(n))
            text = c(t["text"], GREY) if t.get("done") else t["text"]
            print("  %s %s %s%s" % (c("%2d" % i, GREY), box, text, when))
        print(c("\n  board.py --done <n>   --undone <n>   --rm <n>\n", GREY))
        return shown

    if args.add:
        due = None
        if args.on:
            due = parse_when(args.on)
            if not due:
                sys.exit("Couldn't read a date from %r. Try 2026-08-14, 8-14, or 14." % args.on)
        t = {"id": next_task_id(tasks), "text": args.add.strip(),
             "due": due.isoformat() if due else None,
             "done": False, "created": TODAY.isoformat()}
        tasks.append(t)
        save_tasks(tasks)
        print()
        print(c("  Added: ", GREEN) + t["text"])
        if due:
            n = (due - TODAY).days
            print(c("  On %s (%s), so it shows on the calendar." %
                    (due.isoformat(), "today" if n == 0 else "in %d days" % n), GREY))
        else:
            print(c("  No date \u2014 it sits under 'No date / rolling' until you give it one.", GREY))
        print(c("  Check off later with:  board.py --tasks   then   board.py --done <n>\n", GREY))
        rc = finish(args, list_tasks)
        if rc is not None:
            return rc


    # NOTE: --tasks is handled after the mutations below. Putting it first meant
    # `--tasks --due 11 09/15` printed the list and silently dropped the date.
    for flag, action in (("done", True), ("undone", False)):
        idx = getattr(args, flag)
        if idx is not None:
            if not (1 <= idx <= len(tasks)):
                sys.exit("No task %d. See them with: board.py --tasks" % idx)
            tasks[idx - 1]["done"] = action
            save_tasks(tasks)
            print()
            print((c("  Done: ", GREEN) if action else c("  Reopened: ", YELLOW)) +
                  tasks[idx - 1]["text"] + "\n")
            rc = finish(args, list_tasks)
            if rc is not None:
                return rc
            args.done = args.undone = None

    if args.due is not None:
        try:
            idx = int(args.due[0])
        except ValueError:
            sys.exit("First argument to --due is the task number, e.g. --due 2 8-14")
        if not (1 <= idx <= len(tasks)):
            sys.exit("No task %d. See them with: board.py --tasks" % idx)
        raw = args.due[1].strip().lower()
        if raw in ("none", "clear", "-"):
            tasks[idx - 1]["due"] = None
            save_tasks(tasks)
            print(c("\n  Date removed: ", YELLOW) + tasks[idx - 1]["text"])
            print(c("  Back under 'No date / rolling'.\n", GREY))
            rc = finish(args, list_tasks)
            if rc is not None:
                return rc
            args.done = args.undone = None
        when = parse_when(args.due[1])
        if not when:
            sys.exit("Couldn't read a date from %r. Try 2026-08-14, 8-14, or 14." % args.due[1])
        tasks[idx - 1]["due"] = when.isoformat()
        save_tasks(tasks)
        n = (when - TODAY).days
        print()
        print(c("  Due %s: " % when.isoformat(), GREEN) + tasks[idx - 1]["text"])
        print(c("  %s \u2014 it now shows on the calendar." %
                ("today" if n == 0 else "in %d days" % n if n > 0 else "%d days ago" % -n), GREY))
        print(c("  Run scrape.py && build.py to show it on the web board too.\n", GREY))
        rc = finish(args, list_tasks)
        if rc is not None:
            return rc

    if args.rm is not None:
        if not (1 <= args.rm <= len(tasks)):
            sys.exit("No task %d. See them with: board.py --tasks" % args.rm)
        gone = tasks.pop(args.rm - 1)
        save_tasks(tasks)
        print(c("\n  Deleted: ", YELLOW) + gone["text"] + "\n")
        rc = finish(args, list_tasks)
        if rc is not None:
            return rc

    if args.tasks:
        list_tasks()
        return 0

    if args.refresh:
        refresh()
        if args.open_page:
            return open_page()

    if args.open_page and not args.refresh:
        return open_page()

    dropped = load_dropped()

    if args.dropped:
        if not dropped:
            print(c("\n  Nothing dropped.\n", GREY))
            return 0
        print()
        print(c("  DROPPED", BOLD, GREY) + c("  %d" % len(dropped), GREY))
        for k, r in sorted(dropped.items(), key=lambda kv: kv[1].get("at", "")):
            print("  " + c(r.get("at", ""), GREY) + "  " + r.get("name", k)[:60])
            print("     " + c("key: " + k, GREY) + (("  " + r["reason"]) if r.get("reason") else ""))
        print(c("\n  Restore with:  board.py --restore <key>\n", GREY))
        return 0

    if args.restore:
        if args.restore not in dropped:
            sys.exit("Not in the dropped list: %s\nSee them with: board.py --dropped" % args.restore)
        name = dropped[args.restore].get("name", args.restore)
        del dropped[args.restore]
        save_dropped(list(dropped.values()))
        print(c("\n  Restored: ", GREEN) + name)
        print(c("  Run scrape.py to bring it back onto the web board.\n", GREY))
        return 0

    data = load()
    cols = width()
    state = load_state()
    for v in data["venues"]:
        if v.get("key") in state:
            v["status"] = state[v["key"]]
    # deadlines.json also carries a baked copy of the tasks (for the web board).
    # Drop it and use tasks.yml directly, so the terminal reflects an --add or
    # --done immediately rather than only after the next scrape.
    everything = [v for v in data["venues"] if not v.get("is_task")] + \
                 [task_as_item(t) for t in tasks]
    rows = [v for v in everything
            if v.get("key") not in dropped and matches(v, args)]
    rows.sort(key=lambda v: (v["date"] == "", v["date"], v["name"]))

    if args.mark is not None:
        try:
            idx = int(args.mark[0])
        except ValueError:
            sys.exit("First argument to --mark is the row number, e.g. --mark 1 submitted")
        want = args.mark[1].strip().lower()
        if want not in STATUSES:
            sys.exit("Unknown status %r. Use one of: %s" % (args.mark[1], ", ".join(STATUSES)))
        if not args.all:
            rows = [v for v in rows
                    if days_until(v["date"]) is not None
                    and not (days_until(v["date"]) < 0 and not args.past)
                    and days_until(v["date"]) <= args.days]
        if not (1 <= idx <= len(rows)):
            sys.exit("No row %d in this view — there are %d. Row numbers come from "
                     "the view you ran, so pass the same filters." % (idx, len(rows)))
        target = rows[idx - 1]
        state = load_state()
        pretty = want.title() if want != "not started" else "Not started"
        if pretty == "Not started":
            state.pop(target["key"], None)
        else:
            state[target["key"]] = pretty
        save_state(state)
        print()
        print(c("  " + pretty + ": ", GREEN) + target["name"])
        print(c("  Recorded in state.yml. It stays on the board with its date, so you", GREY))
        print(c("  keep the record — 'Next up' just stops nagging you about it.", GREY))
        print(c("  Show on the web board with:  python3 board.py --refresh --open\n", GREY))
        return 0

    if args.drop is not None:
        # Resolve against the SAME filtered view the user just looked at, so the
        # row number they type means what they saw.
        if not args.all:
            keep = []
            for v in rows:
                n = days_until(v["date"])
                if n is None or (n < 0 and not args.past) or n > args.days:
                    continue
                keep.append(v)
            rows = keep
        if not (1 <= args.drop <= len(rows)):
            sys.exit("No row %d in this view — there are %d.\n"
                     "Row numbers come from the view you ran, so pass the same "
                     "filters you used to see it." % (args.drop, len(rows)))
        target = rows[args.drop - 1]
        dropped[target["key"]] = {
            "key": target["key"], "name": target["name"],
            "at": TODAY.isoformat(), "reason": args.reason or "",
        }
        save_dropped(list(dropped.values()))
        print()
        print(c("  Dropped: ", YELLOW) + target["name"])
        if target.get("date"):
            print(c("           was " + target["date"], GREY))
        print(c("  Recorded in dropped.yml — it will stay gone through future scrapes.", GREY))
        print(c("  Undo:  board.py --restore " + target["key"], GREY))
        print(c("  Then:  python3 scrape.py && python3 build.py   to update the web board.\n", GREY))
        return 0

    if args.cal:
        y, mo = TODAY.year, TODAY.month
        if args.month:
            m = re.match(r"^(\d{4})-(\d{1,2})$", args.month.strip())
            if not m:
                sys.exit("--month wants YYYY-MM, e.g. 2026-08")
            y, mo = int(m.group(1)), int(m.group(2))
        render_calendar(rows, y, mo, cols)
        print(c("  " + MARK["paper"] + " paper   " + MARK["award"] + " program   " +
                MARK["outreach"] + " email   " + MARK["task"] + " task", GREY))
        print(c("  board.py --cal --month 2026-09   for another month\n", GREY))
        return 0

    if args.show is not None:
        if 1 <= args.show <= len(rows):
            show_detail(rows[args.show - 1], cols)
        else:
            sys.exit("No row %d — there are %d rows in this view." % (args.show, len(rows)))
        return 0

    if not args.all:
        keep = []
        for v in rows:
            n = days_until(v["date"])
            if n is None:
                continue
            if n < 0 and not args.past:
                continue
            if n > args.days:
                continue
            keep.append(v)
        rows = keep

    gen = data.get("generated", "")
    age = ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", gen or "")
    if m:
        delta = (TODAY - dt.date(*(int(x) for x in m.groups()))).days
        age = "refreshed today" if delta == 0 else "refreshed %d day%s ago" % (delta, "" if delta == 1 else "s")
        if delta >= 7:
            age = c(age + " — run scrape.py", YELLOW)

    print()
    print(c("  DEADLINE BOARD", BOLD) + c("   %d shown of %d  %s"
          % (len(rows), len(data["venues"]), age), GREY))
    print(c("  " + "-" * (cols - 2), GREY))

    if not rows:
        print(c("\n  Nothing matches. Try --all, a longer --days, or drop a filter.\n", GREY))
        return 0

    idx = 0
    for title, test in GROUPS:
        members = [v for v in rows if test(days_until(v["date"]))]
        if not members:
            continue
        print()
        print(c("  " + title, BOLD, GREY) + c("  %d" % len(members), GREY))
        for v in members:
            idx += 1
            print("  " + render_row(idx, v, cols - 2))

    print()
    print(c("  * paper   + program/award   @ email      ! date needs verifying   "
            "◆ industry on the program (bright = frontier lab)", GREY))
    print(c("  board.py --show N for detail   --all for everything   -p prism to filter", GREY))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
