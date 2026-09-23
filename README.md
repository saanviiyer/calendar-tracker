# Conference Deadline Board

A self-updating board of conference and workshop submission deadlines. You keep
a list of venues in `venues.yml`. A scraper fills in the dates from public
feeds and venue pages, and one self-contained HTML file displays the result.

The tracked list holds 146 venues, weighted toward EEG and neuroimaging,
computational neuroscience, protein and genomic machine learning, and AI
evaluation. Edit `venues.yml` to track something else.

## Install

Python 3 and PyYAML are the only requirements.

```bash
pip3 install pyyaml
```

## Run

```bash
python3 scrape.py --probe && python3 build.py && open index.html
```

`scrape.py` writes `deadlines.json`. `build.py` bakes that data into
`index.html`, so the page opens straight off the filesystem with no server.
Neither generated file is committed, so run this once after cloning.

`board.py` wraps the same loop for terminal use.

```bash
python3 board.py --refresh --open   # re-scrape, rebuild, open the board
python3 board.py --cal              # calendar for this month
python3 board.py --tasks            # open tasks
python3 board.py --days 30          # what is due in the next 30 days
```

## Configuration

Two config files hold personal data and stay out of the repository. Copy the
examples and fill them in.

```bash
cp notify.example.yml notify.yml
cp contacts.example.yml contacts.yml
```

`notify.yml` sets where the digest goes, either iMessage through Messages.app
or an [ntfy](https://ntfy.sh) topic. An ntfy topic name is the only thing
protecting it, so make it long and unguessable. `contacts.yml` holds people you
plan to contact, and only entries carrying a `first_contact_by` date reach the
board.

`notify.py` prints the digest and sends nothing unless you pass `--send`.

```bash
python3 notify.py            # print what would be sent
python3 notify.py --send     # send it
python3 notify.py --days 7   # widen the window from the default 3
```

`install-daily-text.sh` installs a launchd job for a daily digest on macOS.
launchd cannot read files under `~/Downloads`, `~/Documents` or `~/Desktop`, so
keep the folder elsewhere if you want the job to work.

```bash
./install-daily-text.sh          # 08:00 daily
./install-daily-text.sh 7 30     # or 07:30
./install-daily-text.sh --remove
```

## The three scrape modes

`scrape.py` alone reads the structured feeds. It is fast and reliable.

`scrape.py --probe` also fetches the pages of venues marked `probe: true` and
tries to read dates out of the prose. It takes a couple of minutes. Anything it
finds is labelled auto-read on the board, which means a regular expression
thought it saw a deadline. Click through before trusting one.

`scrape.py --labs` also reads each workshop's pages for the industry labs on its
program. It takes about twenty minutes for a full board, so run it when the
workshop season turns over.

## Files

```
venues.yml             the venue list, hand-maintained, the source of truth
scrape.py              reads venues.yml, writes deadlines.json
build.py               bakes deadlines.json into index.html
board.py               terminal interface: calendar, tasks, venues, filters
notify.py              builds and sends the digest
install-daily-text.sh  installs the macOS launchd job
.github/workflows/     refreshes the board twice a week, deploys Pages
```

Generated and personal files are gitignored: `deadlines.json`, `index.html`,
`labs_cache.yml`, `notify.yml`, `contacts.yml`, `tasks.yml`, `state.yml`,
`my_venues.yml` and `dropped.yml`. Each one is either rebuilt by `scrape.py` or
created from the examples above.

## Where the dates come from

`scrape.py` reads each venue's `link` and cross-checks the
[ccfddl](https://ccfddl.github.io) and
[huggingface/ai-deadlines](https://huggingface.co/spaces/huggingface/ai-deadlines)
feeds. Set `pin_date: true` on a venue to keep your own date when a feed
disagrees, and the difference gets noted instead of overwritten.

Dates are best-effort. Check the official call for papers before relying on
one. The `--labs` output is explicitly unverified, because the scanner cannot
tell an organizer from a sponsor logo.

## Hosting

The included workflow runs the scraper every Monday and Thursday at 06:00
Pacific, commits the refreshed board, and deploys it to GitHub Pages. Enable
Pages in the repository settings with GitHub Actions as the source. A fresh
clone builds its board from `venues.yml` alone, since the personal files are
not in the repository.

## A note on venues.yml

Many entries carry my written assessment of fit, odds and what a venue wants.
Those notes are opinions, some marked unverified, and they reflect my own
research interests. Treat them as a starting point and check the call for
papers yourself.
