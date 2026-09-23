#!/usr/bin/env python3
"""Text yourself what's due.

    python3 notify.py                 print the digest, send nothing
    python3 notify.py --send          actually send it
    python3 notify.py --days 7        widen the window (default 3)
    python3 notify.py --channel ntfy  push notification instead of a text

Channels
--------
messages  iMessage/SMS through Messages.app on this Mac. Free, no account, and
          nothing leaves the machine except through your own Apple ID. macOS will
          ask once for permission to control Messages — if you decline, it fails
          with a clear error rather than silently doing nothing.
ntfy      A push notification via ntfy.sh. No account, works on any phone with
          the app, and is the fallback if Messages permission is a problem. Note
          the topic name is the only secret, so pick an unguessable one.

Configure both in notify.yml. Nothing is ever sent without --send.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required:  pip3 install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "deadlines.json")
CONF = os.path.join(HERE, "notify.yml")
TODAY = dt.date.today()

DEFAULT_CONF = """# Where notify.py sends your digest. Nothing sends without --send.
#
# channel: messages | ntfy
channel: messages

# For channel: messages — your own number or the Apple ID you iMessage yourself at.
# Include the country code, e.g. "+15551234567".
to: ""

# For channel: ntfy — install the ntfy app, subscribe to this exact topic.
# The topic name IS the password, so make it long and unguessable.
ntfy_topic: ""

# How many days ahead counts as "coming up".
days: 3

# If the main channel fails, try this one. Worth setting: Messages cannot be
# driven from a scheduled background job (macOS withholds the Automation grant),
# so the daily run needs a channel that requires no permissions.
fallback: ntfy

# How many lines the message carries, and how wide each name may be. Kept small
# on purpose: a phone's notification preview clips a long message, and a digest
# you have to open to read is a digest you will not read.
max_items: 6
name_width: 34

# Skip the message entirely when there is nothing to report.
quiet_when_empty: true
"""


def load_conf() -> dict:
    if not os.path.exists(CONF):
        with open(CONF, "w", encoding="utf-8") as fh:
            fh.write(DEFAULT_CONF)
        print(f"Wrote {CONF} — fill in `to:` (or `ntfy_topic:`) and run again.")
        raise SystemExit(0)
    with open(CONF, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def parse(value):
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


SHORT_KIND = {"task": "o", "award": "+", "outreach": "@"}

# Boilerplate that eats a lock-screen line without identifying anything.
_NOISE = re.compile(
    r"\b(the|a|an|1st|2nd|3rd|\d+th|first|second|third|fourth|annual|"
    r"international|neurips|icml|iclr|20\d\d|workshop|conference|symposium|"
    r"on|for)\b", re.I)
# A leading code: up to two words, e.g. "IAB", "NeurReps 2026", "ISMRM Workshop".
_CODE = re.compile(r"^([A-Za-z0-9][\w\-\+/&\.]*(?:\s+[\w\-\+/&\.]+)?)\s*[\u2014\-:]\s*(.+)$")


def short_name(name: str, width: int) -> str:
    """Squeeze a venue title down to something readable on a lock screen.

    Auto-discovered workshops arrive as "SLM-Agents \u2014 SLM-Agents: 1st NeurIPS
    Workshop on SLMs for Agentic Systems": the acronym is repeated, and most of
    the rest is boilerplate. Drop the duplicate, strip the filler, and cut on a
    word boundary — "1st NeurIPS Worksh" reads like a bug, not a reminder.
    """
    name = " ".join(name.replace("_", " ").split())

    for sep in (" \u2014 ", " - ", " -- "):
        if sep in name:
            head, tail = name.split(sep, 1)
            if tail.lower().startswith(head.lower()):
                name = tail
            break

    if len(name) <= width:
        return name

    m = _CODE.match(name)
    if m:
        code, rest = m.group(1), m.group(2)
        rest = re.sub(r"\s+", " ", _NOISE.sub(" ", rest)).strip(" :,-\u2014")
        candidate = (code + ": " + rest).strip(": ")
        if len(candidate) <= width:
            return candidate
        room = width - len(code) - 2
        if room > 8:
            cut = rest[:room]
            if " " in cut:
                cut = cut.rsplit(" ", 1)[0]
            return code + ": " + cut.rstrip(" ,:;-\u2014") + "\u2026"
        return code[:width]

    cut = name[:width]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,:;-\u2014") + "\u2026"


def build_digest(days: int, max_items: int = 6, width: int = 34) -> tuple[str, int]:
    """Built for a lock-screen preview, so: short header, one tight line each."""
    if not os.path.exists(DATA):
        raise SystemExit(f"{DATA} not found — run scrape.py first.")
    with open(DATA, encoding="utf-8") as fh:
        data = json.load(fh)

    done = {"Submitted", "Accepted", "Skipped", "Rejected"}
    live = []
    for v in data["venues"]:
        d = parse(v.get("date"))
        if not d or (v.get("status") or "Not started") in done:
            continue
        n = (d - TODAY).days
        if n < 0 or n > days:
            continue
        live.append((n, v))
    live.sort(key=lambda p: (p[0], p[1]["name"]))

    if not live:
        return "", 0

    today_n = sum(1 for n, _ in live if n == 0)
    head = TODAY.strftime("%-d %b")
    if today_n:
        head += " \u00b7 %d due today" % today_n
    head += " \u00b7 %d in %dd" % (len(live), days)

    lines = [head]
    for n, v in live[:max_items]:
        when = "NOW" if n == 0 else ("%dd" % n)
        kind = ("task" if v.get("is_task") else "award" if v.get("is_award")
                else "outreach" if v.get("is_outreach") else "")
        # Marker goes in FRONT, so truncation can never eat it.
        mark = SHORT_KIND.get(kind, " ")
        lines.append("%-3s %s %s" % (when, mark, short_name(v["name"], width)))
    if len(live) > max_items:
        lines.append("+%d more \u00b7 board.py" % (len(live) - max_items))
    return "\n".join(lines), len(live)


class SendFailed(Exception):
    """Raised so main() can try the fallback channel instead of dying."""


def _messages_running() -> bool:
    return subprocess.run(["pgrep", "-x", "Messages"],
                          capture_output=True).returncode == 0


def send_messages(text: str, to: str) -> None:
    if not to:
        raise SendFailed("Set `to:` in notify.yml (your number, with country code).")

    # Both 08:00 failures were Messages being COLD. AppleScript's `launch` returns
    # before the app can answer events, so the first Apple event hit the default
    # timeout (-1712). Start it out-of-band and wait for the process to actually
    # exist before sending anything.
    if not _messages_running():
        subprocess.run(["open", "-g", "-a", "Messages"], capture_output=True)
        for _ in range(30):                 # up to ~15s, polled
            if _messages_running():
                break
            time.sleep(0.5)
        time.sleep(2)                       # a beat to finish waking

    script = (
        'on run {targetBuddy, targetMessage}\n'
        '  with timeout of 45 seconds\n'
        '    tell application "Messages"\n'
        '      set targetService to 1st account whose service type = iMessage\n'
        '      set theBuddy to participant targetBuddy of targetService\n'
        '      send targetMessage to theBuddy\n'
        '    end tell\n'
        '  end timeout\n'
        'end run'
    )
    try:
        proc = subprocess.run(["osascript", "-", to, text],
                              input=script, text=True, capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:
        raise SendFailed("osascript hung for 90s talking to Messages.")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        hint = ""
        if "-1743" in err or "not allowed" in err:
            hint = ("\n\nmacOS blocked it. A scheduled background job often cannot get "
                    "the Automation grant that Messages needs, which is exactly why "
                    "`fallback: ntfy` exists in notify.yml.")
        elif "-1712" in err or "timed out" in err:
            hint = ("\n\nMessages still didn't answer. It is unreliable from a "
                    "background job; the ntfy fallback is the dependable path.")
        elif "-1728" in err:
            hint = ("\n\nMessages could not resolve that recipient. Check the number has "
                    "a country code, or use your Apple ID email.")
        raise SendFailed("Messages refused to send:\n" + err + hint)


def send_ntfy(text: str, topic: str) -> None:
    if not topic:
        raise SendFailed("Set `ntfy_topic:` in notify.yml, then subscribe to it in the ntfy app.")
    req = urllib.request.Request(
        "https://ntfy.sh/" + topic, data=text.encode("utf-8"),
        headers={"Title": "Deadline board", "Priority": "default"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 300:
            raise SendFailed("ntfy returned %s" % resp.status)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", action="store_true", help="actually send (default: print only)")
    ap.add_argument("--days", type=int, help="how far ahead counts as due")
    ap.add_argument("--channel", choices=["messages", "ntfy"], help="override notify.yml")
    args = ap.parse_args()

    conf = load_conf()
    days = args.days if args.days is not None else int(conf.get("days", 3))
    channel = args.channel or conf.get("channel", "messages")

    text, count = build_digest(days,
                               int(conf.get("max_items", 6)),
                               int(conf.get("name_width", 34)))
    if not count:
        if conf.get("quiet_when_empty", True):
            print("Nothing due in the next %d days — no message sent." % days)
            return 0
        text = "Deadline board - nothing due in the next %d days." % days

    print(text)
    print()
    if not args.send:
        print("(dry run — add --send to actually deliver this via %s)" % channel)
        return 0

    def deliver(name):
        if name == "messages":
            send_messages(text, str(conf.get("to") or ""))
        else:
            send_ntfy(text, str(conf.get("ntfy_topic") or ""))

    try:
        deliver(channel)
        print("Sent via %s." % channel)
        return 0
    except SendFailed as first:
        fallback = conf.get("fallback")
        if not fallback or fallback == channel:
            print(str(first), file=sys.stderr)
            return 1
        # Messages needs an Automation grant that a launchd job cannot obtain,
        # so the scheduled run falls through to a channel that needs no
        # permissions at all rather than failing into a log nobody reads.
        print("%s failed, trying %s..." % (channel, fallback), file=sys.stderr)
        try:
            deliver(fallback)
            print("Sent via %s (after %s failed)." % (fallback, channel))
            return 0
        except SendFailed as second:
            print(str(first), file=sys.stderr)
            print(str(second), file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
