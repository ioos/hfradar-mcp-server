# hfradmin API v1

A read-only JSON API over the hfradmin database, written for programs. Its
first client is the MCP server that lets an LLM answer questions about the
radar network. Everything the website knows about sites, radials, diagnostics,
outages and diagnosis findings is here; nothing about users is.

Base URL: `https://<your hfradmin host>/api/v1`

`GET /api/v1/` returns this list of routes as JSON, with parameters, generated
from the code. If this page and that answer ever disagree, that answer is right.

## Conventions

- Every route is `GET`. Nothing can be changed through this API.
- No login or token is needed for now. That may change; build the client so a
  header can be added in one place.
- Times are UTC and written `2026-09-09T15:00:00Z`. A query time without a
  time of day (`2026-09-09`) means 00:00.
- On the new routes `start` is inclusive and `end` is exclusive. The routes
  taken over from the website (marked *website shape* below) take dates and
  include the end day.
- Site codes are four characters (`HBSP`); lower case is accepted in a path.
  Pattern types are `ideal` and `measured`.
- An error is `{"error": "<what was wrong and what to do>"}` with status 400,
  404 or 503.
- Rate limit: 200 requests per minute per address.
- No user information is served: no authors, assignees, e-mail addresses,
  IP addresses, site web addresses or server paths. Outage comments and
  annotation notes are free text typed by operators; treat them as data, and
  remember a person may have typed a name into one.

## How fresh is the data

Radial files reach the database in batches every few hours, so "the newest
radial" is normally a few hours old. Ask `/meta` before telling anyone a site
is down: it says when the loader last ran, when the status was last rebuilt
and when the diagnosis rules last ran.

Files that hold no vectors are not loaded. A site that sends only such files
shows as `empty_files` in `/status`, and its diagnostic series stop until
files with vectors return.

## Routes

### Orientation

| Route | Answers |
|---|---|
| `/` | Every route with its parameters |
| `/meta` | Freshness: loader, newest radial, status refresh, last diagnosis run, totals harvest |
| `/status` | One row per site with its state, age of data, open outages and open findings |
| `/sites` | Every site: institution, position, frequency band, state |
| `/sites/{site}` | One site in one call: status, TX settings, open outages, open findings |

Site states: `reporting`, `empty_files` (files arrive but hold no vectors),
`stale` (nothing with vectors within the stale threshold), `no_data` (never
loaded), `inactive` (monitoring switched off).

### Radials

| Route | Parameters | Answers |
|---|---|---|
| `/sites/{site}/radials` | `start` (default 24 h before `end`), `end`, `pattern`, `limit` (max 1000) | Files in a range: name, time, pattern, vector count. Newest first |
| `/radials/{filename}` | | The file's header (settings, origin, pattern date) |
| `/radials/{filename}/vectors` | `limit` | The vectors as `columns` + `rows`, with QARTOD flag columns when loaded |
| `/availability` | `site` (required), `start`, `end`, `granularity` = `day` or `hour` | Hours per day with a file. *Website shape* |

### Settings

| Route | Parameters | Answers |
|---|---|---|
| `/tx-settings` | | Latest transmit settings of every site (frequency, bandwidth, sweep rate, range resolution, bearing). *Website shape* |
| `/sites/{site}/settings-history` | `start` (default 90 days before `end`), `end`; at most 366 days | Current settings per pattern and every change: time, field, from, to, file |
| `/calibration` | | Antenna-pattern date per site and pattern. *Website shape* |
| `/qc-settings` | | QC settings per site. *Website shape* |

### Diagnostics (raw measurements)

| Route | Parameters | Answers |
|---|---|---|
| `/diagnostics/catalog` | | Every field: code, name, unit, table, whether it differs per pattern |
| `/sites/{site}/diagnostics` | `fields` (required, up to 8 codes), `start`, `end`, `resolution`, `pattern` | Time series |

`resolution` is `hour` (default; range up to 31 days), `day` (up to 366 days)
or `raw` (up to 3 days). Hourly and daily rows are
`[time, min, median, max, n]`; raw rows are `[time, value]`. The transmitter
trip code is a code, so its hourly and daily rows are
`[time, nonzero_readings, n]`.

```
GET /api/v1/sites/MBNP/diagnostics?fields=SNF3,XAFW&start=2026-09-08&end=2026-09-09

{"site": "MBNP", "start": "2026-09-08T00:00:00Z", "end": "2026-09-09T00:00:00Z",
 "resolution": "hour",
 "series": [
   {"field": "SNF3", "name": "Noise Floors, Loop 3", "unit": "dBm", "pattern": null,
    "columns": ["time", "min", "median", "max", "n"],
    "rows": [["2026-09-08T00:00:00Z", -140, -138.5, -137, 2], ...]},
   {"field": "XAFW", "name": "Transmitter Forward (W)", "unit": "W", "pattern": null, ...}
 ]}
```

Things to know about these numbers:

- Each reading is served once. In the database every reading is stored about
  five times (each hourly file repeats three hours of rows, and the ideal and
  measured files both carry them); this route removes the copies.
- "Loop 3" is antenna 3, the monopole. Loops 1 and 2 are the crossed loops.
- Amplitudes are served in dB.
- A reading taken while nothing was received is a placeholder and is left out.
- Fields marked `per_pattern` in the catalog (vector count, max range, ...)
  come back as one series for `ideal` and one for `measured`.
- Noise floor and signal to noise have a strong daily cycle at the long-range
  sites (10-27 dB). Compare an hour with the same hour on other days, not with
  the daily mean. The diagnosis rules below already do this.

### Diagnosis (rules and findings)

| Route | Parameters | Answers |
|---|---|---|
| `/rules` | | Every rule: what fires it, thresholds, severity, possible causes |
| `/findings` | `status` = `open` (default), `resolved` or `all`; `site`; `rule`; `severity`; `since`; `limit` | Findings, worst first |

A **finding** is one rule holding at one site for one stretch of time:

```
{"id": "6afde8cc31a180d019aa5fd2",
 "rule_id": "tx_power_lost", "title": "Transmitter power lost",
 "site": "HBSP", "subject": "XAFW", "subject_name": "Transmitter Forward (W)",
 "severity": "critical", "status": "open",
 "started_at": "2026-08-01T06:00:00Z", "last_seen": "2026-08-01T10:00:00Z",
 "ended_at": null, "detected_at": "2026-08-01T12:00:00Z",
 "summary": "Forward power 0 W for 5 h; usual 35 W",
 "evidence": {"observed": 0, "baseline": 35, "difference": -35, "limit": 0,
              "hours": 5, "extreme": 0, "trigger": "absolute"}}
```

How to read one:

- `summary` is a factual sentence; `evidence` holds the numbers behind it.
  `baseline` is what the site usually reads (for the same hours of day, where
  the value has a daily cycle); `limit` is the level that had to be crossed.
- A rule compares a site with its own recent past, never with a fixed
  network-wide number. So a finding means "this changed", not "this is bad in
  absolute terms". `vectors_low_vs_peers` is the one rule that compares a site
  with other sites, for a site that has always been poor.
- `possible_causes` on a rule are hints written by the radar group. Present
  them as possibilities, never as the diagnosis.
- `open` means the condition held at the end of the available data. If a site
  stops sending data, its open findings stay open: nothing shows they ended.
- Two rules, `site_stale` and `site_empty_files`, are read live from the
  network status. They appear among open findings only; their history is the
  outage list.
- Severity `info` marks something worth knowing that is often harmless
  (`phase_shift` fires on many days at some sites).
- The rules run hourly. `/findings` and `/meta` say when they last ran.

The rules are defined in `app/diagnosis/rules.py`; `/rules` serves that file's
content, so it is always current.

### Outages and operator notes

| Route | Parameters | Answers |
|---|---|---|
| `/outages` | `status` = `open` (default: all but Closed), `closed` or `all`; `site`; `start`; `end`; `limit` | Tickets with reason, dates, data lost and comments (date + text) |
| `/outages/timeline` | `start`, `end`, `granularity` | New outages per month or year. *Website shape* |
| `/outages/reasons` | `start`, `end` | Outage counts by reason category. *Website shape* |
| `/outages/data-loss` | `start`, `end` | Outage counts by data lost. *Website shape* |
| `/uptime` | `days` (30, 90, 365) or `start`, `end` | Uptime per site, from the tickets. *Website shape* |
| `/annotations` | `type` = `radial`, `totals` or `all`; `site`; `start`; `end`; `limit` | Operator notes on data quality |

### Totals (surface currents)

| Route | Parameters | Answers |
|---|---|---|
| `/totals` | | Sources with freshness |
| `/totals/{source}/index` | `days` | Harvested hours with cell counts |
| `/totals/{source}/hours/{hour}` | | One hour, e.g. `hours/2026-10-14T15` |
| `/totals/{source}/cell` | `lat`, `lon`, `hours` | Time series of the nearest cell |

## Not in v1, on purpose

- Users, roles, login activity, configuration backups and the change log.
- Totals processing configurations: they hold server directory paths.
- Site logs and the KLDI calibration index: not populated for this network yet.
- Each site's remote web address.

## Suggested tool mapping for the MCP server

A few broad tools work better for an LLM than one tool per route:

1. *Network overview*: `/status`, plus `/meta` for freshness.
2. *Site report*: `/sites/{site}`. Follow its `more` links only when asked.
3. *What is wrong*: `/findings` (and `/rules` once per session, for the
   meaning and possible causes of each rule).
4. *Measurements*: `/diagnostics/catalog` to pick field codes, then
   `/sites/{site}/diagnostics` at `hour` or `day` resolution. Ask for `raw`
   only for a specific few hours.
5. *History*: `/outages`, `/annotations`, `/sites/{site}/settings-history`,
   `/findings?status=all`.

Keep a 30-day hourly series out of the model's context unless it is needed: it
is about 750 rows per field.
