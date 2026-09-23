# Conference Deadline Board

A sorted, self-updating board of conference and workshop submission deadlines.
`scrape.py` collects the dates, `index.html` displays them, and your own notes
and statuses live in the browser, separate from the scraped data.

```
venues.yml        which venues to track + what counts as "relevant to me"
scrape.py         collects deadlines -> deadlines.json
build.py          bakes deadlines.json into index.html as an offline fallback
index.html        the board (one self-contained file, no build step, no deps)
deadlines.json    generated; don't hand-edit
```

## Run it on your computer

You need Python 3 and PyYAML. Once:

```bash
pip3 install pyyaml
```

Then, from this folder, refresh the data and open the board:

```bash
cd ~/Downloads/CALTECH/calendar-tracker && python3 scrape.py --probe && python3 build.py && open index.html
```

That is the whole loop. `open index.html` uses your default browser; the board
works fine straight off the filesystem.

If you want the board to read the live `deadlines.json` rather than the copy
baked into the page — which matters only if you skip `build.py` — serve the
folder instead, because browsers block `fetch()` on `file://` URLs:

```bash
cd ~/Downloads/CALTECH/calendar-tracker && python3 -m http.server 8000
```

Then visit `http://localhost:8000`. Stop it with Ctrl-C.

### Opening the web board from the terminal

```bash
python3 board.py --open
```

The page is fully self-contained — fonts and data are baked in — so it opens
straight off the filesystem with no server. `--refresh` re-scrapes and rebuilds
first, so what you open is current:

```bash
python3 board.py --refresh --open
```

If you'd rather not go through `board.py`, `open index.html` does the same thing
(`xdg-open` on Linux).

### Calendar, in the terminal

```bash
python3 board.py --cal                    # this month
python3 board.py --cal --month 2026-10    # any month
```

A terminal cell is about thirteen characters wide, which will not hold a venue
name — so the grid carries the shape and a per-day count, and an agenda beneath
it carries the detail. You get "when is it busy" and "what is it" without the two
fighting for the same space. Days where everything is done show a tick instead of
a count. All the usual filters apply, so `--cal -p cortseer` is a project calendar.

### Tracking a new conference from the terminal

```bash
python3 board.py --venue "IEEE EMBC 2027" --on 2027-01-17 \
    --link https://embc.embs.org/2027/important-dates/ \
    --track Bioengineering --fit clera,cortseer --match EMBC
```

Only `--venue` is required. `--on` accepts the same loose dates as tasks and pins
whatever you give it. `--match` names the titles to look up in the aggregator
feeds, so a published date can correct yours later. `--link` also enables page
probing under `--probe`.

These land in `my_venues.yml`, deliberately apart from the curated `venues.yml`
so its comments and structure stay intact — but `scrape.py` reads them through the
identical pipeline, so an added venue still gets feed matching, projection, odds
and project fit. `board.py --venues` lists what you've added; edit or delete them
in the file directly. Run `scrape.py && build.py` to fold changes into the board.

### Seeing everything one project could go to

```bash
python3 board.py biocl          # every venue that fits biocl, any date
python3 board.py prism --days 60
```

Naming a project drops the 30-day horizon, because "where could this go?" is a
question about the whole calendar rather than the next month. A word that isn't a
project is treated as a search instead.

### Getting texted what's due

```bash
python3 notify.py                 # print the digest, send nothing
python3 notify.py --send          # actually send it
python3 notify.py --days 7        # widen the window (default 3)
```

The first run writes `notify.yml`; put your number in `to:` (with country code)
and you're done. **Nothing sends without `--send`** — plain `notify.py` always
just prints, so you can see the message before it goes anywhere.

Two channels:

- **`messages`** (default) sends an iMessage/SMS through Messages.app. Free, no
  account, no third party — it goes out from your own Apple ID. macOS asks once
  for permission to control Messages; if you decline it fails loudly rather than
  silently doing nothing.
- **`ntfy`** posts a push notification to [ntfy.sh](https://ntfy.sh) instead.
  No account either, and it's the fallback if the Messages permission is awkward.
  One caveat worth taking seriously: the topic name is the only thing protecting
  it, so pick something long and unguessable rather than `saanvi-deadlines`.

For a daily digest:

```bash
./install-daily-text.sh          # 08:00 every day
./install-daily-text.sh 7 30     # or 07:30
./install-daily-text.sh --remove
```

That installs a launchd agent — chosen over cron because it survives reboots, and
over an in-app scheduler because it runs whether or not any app is open. If the
Mac is asleep at the scheduled time, the job fires when it next wakes. Output
goes to `notify.log`, and `launchctl kickstart -k gui/$(id -u)/com.saanvi.deadline-text`
triggers a run immediately so you can check it end to end.

Two knobs in `notify.yml` control how much fits on a lock screen: `max_items`
(default 6) and `name_width` (default 34). Both are deliberately small — a phone
clips a long notification, so a digest you have to open to read is one you won't.
Titles are compressed rather than blindly truncated: a repeated acronym is
dropped ("SLM-Agents — SLM-Agents: 1st NeurIPS Workshop on…" becomes
"SLM-Agents: SLMs Agentic Systems"), boilerplate like "Workshop on" and the year
is stripped, and cuts land on word boundaries.

The digest only counts things that are actually outstanding — anything you've
marked submitted, accepted or skipped is left out, and by default no message is
sent at all on a quiet day.

### Your own tasks

Things you need to do that no conference set a date for.

```bash
python3 board.py --add "email Cognita AI"             # no date
python3 board.py --add "email Ashmitha" --on 8-14     # dated, so it hits the calendar
python3 board.py --tasks                              # list them
python3 board.py --due 2 8-14                         # give an existing task a date
python3 board.py --due 2 none                         # take the date back off
python3 board.py --done 2                             # check one off
python3 board.py --undone 2                           # changed your mind
python3 board.py --rm 2                               # delete it
```

`--on` takes `2026-08-14`, `8-14`, or bare `14` for the next 14th — a past date
rolls forward rather than silently landing in the past. Undated tasks sit under
**No date / rolling**; give one a date and it appears on the calendar alongside
real deadlines.

Tasks live in `tasks.yml`, kept separate from `venues.yml` because they are yours
and nothing upstream should ever be able to rewrite them. `scrape.py` folds them
into `deadlines.json` so the web board shows them too, under the **My tasks**
type filter — but the terminal reads `tasks.yml` directly, so an `--add` or
`--done` shows up immediately without waiting for a re-scrape.

### Dropping deadlines that no longer apply

```bash
python3 board.py --days 30                       # find the row number
python3 board.py --days 30 --drop 4 --reason "wrong subfield"
```

Row numbers belong to the view you just ran, so pass the same filters when
dropping as when looking. The drop is recorded in `dropped.yml` with the date and
your reason, and `scrape.py` excludes it on every subsequent run.

That indirection matters: deleting the entry from `venues.yml` would not stick,
because OpenReview discovery rediscovers workshops each run and would quietly
bring it back. Nothing is ever deleted — `dropped.yml` is a filter, not a grave.

```bash
python3 board.py --dropped                       # what you have dropped, and why
python3 board.py --restore <key>                 # put one back
```

After dropping or restoring, re-run `scrape.py && build.py` to update the web
board; the terminal view applies it immediately.

### The three commands

`python3 scrape.py` hits the structured sources only — fast, quiet, reliable.

`python3 scrape.py --labs` also reads each workshop's own pages for the industry
labs on its program, so the board can tell you who you would be in a room with —
see [Who is running the workshop](#who-is-running-the-workshop). Slow, roughly
twenty minutes for a full board, so run it when the workshop season turns over
rather than on every refresh.

`python3 scrape.py --probe` also fetches the CFP pages of venues marked
`probe: true` in `venues.yml` and tries to read dates out of the prose. It takes
a couple of minutes and prints failures for sites that block bots. Anything it
finds is labelled **auto-read** on the board, meaning *a regex thought this was a
deadline* — always click through before trusting one.

## Host it free, no domain needed

GitHub Pages gives you a URL at `https://<your-username>.github.io/<repo>/` and
runs the scraper for you on a schedule. From this folder:

```bash
git init && git add . && git commit -m "Conference deadline board"
```

Create an empty repo on GitHub, then:

```bash
git remote add origin https://github.com/<your-username>/<repo>.git && git branch -M main && git push -u origin main
```

In the repo, go to **Settings → Pages** and set **Source** to **GitHub Actions**.
That's it — `.github/workflows/refresh.yml` then rescrapes every Monday and
Thursday at 06:00 Pacific, commits any changed dates, and redeploys the page. You
can also trigger it by hand from the **Actions** tab.

Make the repo **private** if you'd rather your submission plans not be public;
Pages still works on private repos for personal accounts on the free plan, and
the site itself is public either way. Keep notes you don't want public out of
`venues.yml` and put them in the board's own notes field instead, which never
leaves your browser.

Netlify Drop (drag the folder onto `app.netlify.com/drop`) and Cloudflare Pages
also host it free on a subdomain, but neither runs the scraper — you'd be
uploading a snapshot by hand each time.

## Tracking a new venue

Add an entry to `venues.yml`:

```yaml
  - name: MICCAI 2028
    link: https://miccai.org/
    track: Medical imaging
    match: [MICCAI]      # look this title up in the aggregator feeds
    probe: true          # and read its CFP page with --probe
```

Useful keys: `date` to set one yourself, `approx: true` when it's a guess at the
month, `pin_date: true` to keep your date even when a feed disagrees (the
disagreement gets noted rather than applied), `no_feed: true` for venues no
aggregator covers, `fit: [prism, clera]` to say which of your projects suits it,
and `openreview: NeurIPS.cc/2026/Workshop/Foo` to take the exact deadline off a
workshop's OpenReview submission invitation.

## Project fit

The `projects:` block at the top of `venues.yml` lists what you're working on.
Every venue shows a coloured chip for the projects that suit it, and the toolbar
has a project filter — pick **PRISM** and you get only the venues worth
submitting PRISM to, in deadline order.

A venue's `fit:` is used when present. Otherwise each project's `keywords` are
matched against the venue name, which is what gives auto-discovered workshops a
sensible chip without hand-editing. Explicit always wins, so correct a bad guess
by writing `fit:` on that venue.

## Awards and fellowships

The `awards:` block in `venues.yml` holds fellowships, scholarships and student
prizes. They land in the same chronological board as paper deadlines — the point
is seeing a nomination deadline sitting next to a submission deadline — and the
toolbar's type filter narrows to one or the other.

Awards carry fields conferences don't:

```yaml
awards:
  - name: NSF GRFP
    link: https://www.nsfgrfp.org/
    date: 2026-10-19
    kind: fellowship          # sets the odds ceiling
    eligibility: US citizen, national or permanent resident; senior year or first/second-year grad
    award: Three years of support, $37k annual stipend plus tuition allowance
    nomination: true          # institution must put you forward
    campus_deadline: 2026-09-15
    fit: [{project: prism, strength: strong, why: "..."}]
```

**`campus_deadline` wins.** When a nomination deadline exists and falls earlier
than the sponsor's, the board sorts on the internal one and says so — that is the
date that actually binds, and it's the one people miss. Rows needing nomination
carry a red **nomination needed** badge.

## One venue, several deadlines

Workshops frequently run more than one track — commonly an archival paper track
and a later non-archival extended-abstract track — and the choice matters beyond
the date, since archival publication can block a later submission of the full
work elsewhere. OpenReview exposes only one submission invitation per workshop,
so the scraper sees only one of them and says so in the note.

When both tracks matter, give each its own row (DevAI is set up this way: Aug 29
archival, Oct 13 non-archival). Use `pin_date: true` on the row the scraper can't
see so a refresh can't overwrite it.

## How workshops get found

Workshops inside big conferences are the hard case: they're announced late, their
websites often lag, and no aggregator indexes them. But every accepted
NeurIPS/ICLR/ICML workshop registers a submission invitation on OpenReview
carrying an exact due date, usually before its own site is finished.

`scrape.py` walks the workshop listing for each conference in
`workshop_discovery.domains`, keeps the ones whose name or title matches
`workshop_discovery.keywords`, and reads the deadline off each invitation. Adding
a conference year that hasn't opened yet is deliberate: it returns nothing today
and starts producing workshops the moment they're accepted.

Tune `keywords` to change what counts as relevant, and `exclude_keywords` to drop
recurring matches you don't care about.

Keywords have a blind spot worth knowing about. A manual sweep of all 100 NeurIPS
2026 workshops on 31 July 2026 turned up three strong fits that title matching had
missed entirely — "AI for Science Workshop: Verification in the Age of AI
Scientists" and "Sim2Science: ML with Imperfect Scientific Models" contain none of
the subject words the list had, yet the first is the least selective workshop on
the board and the second is the only one Google DeepMind sponsors. The fix was to
add the *methodology* vocabulary (verification, reproducibility, falsifiability,
measurement, simulation) alongside the subject vocabulary. When a sweep finds
something the keywords missed, add the word that would have caught it.

## Who is running the workshop

Topic matching finds workshops about your subject. It cannot tell you which ones
put you in a room with people who hire, and that is often the reason to submit.

`scrape.py --labs` reads each workshop's own pages — the front page plus
`organizers`, `speakers` and friends — and records the industry labs named on
them, under `labs:`. Sponsor logos count: the lab name usually lives in an `alt`
or `title` attribute rather than in body text, so those are harvested too. A lab
that paid for the workshop is more involved than one that sent a speaker.

```bash
python3 board.py --lab deepmind      # only venues with DeepMind on the program
python3 board.py --lab any           # any industry presence at all
```

Rows carry a `*` when a lab is named, bright when it's a frontier lab. `--show N`
prints the roster.

**This is a signal to go look, not a verified roster.** It cannot tell an
organizer from a sponsor logo from a PhD student's summer internship, and a
workshop that merely mentions a lab in a topic list will register. Two real
limitations: sites that render from JavaScript (a React app serving an empty
`<div id="root">`) yield nothing, and a name only in a linked PDF is invisible.

Anything auto-read is badged as such. To record a roster you have actually
checked, write `labs:` and `labs_note:` by hand in `venues.yml` — hand-written
entries are never overwritten, and the note is where you say who and in what role.

Because the scan is slow it is opt-in, so what it finds is cached in
`labs_cache.yml` and reused by every later run. Without that, a plain
`scrape.py` — which is every routine refresh — would blank the rosters, and the
feature would appear to work once and then quietly delete itself. Delete a line
from the cache to force a re-read of that venue; delete the file to re-read all.

One trap this pays for directly: workshop sites routinely leave the previous
edition's speakers and schedule commented out in the HTML. ATTRIB 2026's page
still carries its 2024 lineup that way, and Interp4Discovery has a full six-person
speaker list — including Been Kim of Google DeepMind — sitting inside a
`<!-- preserved for publication later -->` block that the live page never shows.
Both the lab scan and the date probe strip comments before reading, so neither
reports last year's news as this year's.

## Where the dates come from

Each row carries a badge when its date isn't a published fact:

| Badge | Meaning |
| --- | --- |
| *(none)* | Published deadline from OpenReview or an aggregator feed |
| **projected** | No call posted yet; the last edition's date rolled forward a year |
| **auto-read** | A regex pulled it off a CFP page — verify it |
| **not posted** | Venue is real, no date exists anywhere yet |
| **approximate** | Only the month is known |
| **you edited the date** | Your value, pinned against future refreshes |

Sources, in the order they win: a workshop's OpenReview invitation, then
[ccfddl](https://ccfddl.com) and
[huggingface/ai-deadlines](https://github.com/huggingface/ai-deadlines), then
`venues.yml`, then page probing.

## Your edits vs. refreshed data

Statuses, notes, hidden venues and venues you add are stored in your browser
under `cfp-board-v2`, keyed per venue and kept apart from the scraped fields. A
refresh updates dates and links around them without touching your notes.

Two consequences worth knowing: the notes are per-browser and per-device, so use
**Export JSON** for a real backup; and **Reset my edits** discards all of it and
returns to purely scraped data.
