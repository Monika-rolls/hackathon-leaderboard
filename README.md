# Catalyst

A live leaderboard for AI coding-agent activity at a hackathon. Each participant runs one
command; their agent sessions (Claude Code, Cursor, Codex, Copilot, Antigravity, Gemini CLI,
OpenCode…) are turned into redacted counts by [`agentplay`](https://pypi.org/project/agentplay/)
on their laptop and synced every 20 s. The hub ranks builders and teams by tool calls and
hands out badges for everything else.

**Only counts, tool names, timings, token totals and file *names* leave a laptop.** Never code,
prompts, file contents, command arguments or secrets. The hub additionally drops full file
paths and repo remotes before storing anything.

## Pages

| Route | What it is |
|---|---|
| `/` | Leaderboard: live stats, power hour, live feed, podium, builders/teams tables (sort by any metric), crown holders |
| `/player?name=` | A builder: rank, stats, trophy case, progress to every next badge tier, activity by hour, favourite tools, session replay |
| `/team?name=` | A team: **each member's share** of calls, tokens, commits, lines, MCP, tests, errors and spend; members table; team badges |
| `/awards` | Every badge with its bronze/silver/gold rule, crown holder, runners-up and how many have earned it |
| `/stage` | Projector view. Six rotating scenes (room totals, podium, team battle, power hour, crowns, live feed), full-screen spotlights with confetti for crowns, gold badges, podium entries and milestones. `←/→` scenes, `space` pause, `F` fullscreen, `?scene=N` pins one scene |
| `/admin` | Organisers (needs `HUB_ADMIN_TOKEN`). **Sessions:** create, make active, archive/reopen, delete, export. **Settings** per session: name, start/end, venue time zone, registration code, registration open, uploads open (freeze), rank-by metric, which agents count, announcement banner, clear live feed. **People:** grouped by team with sync-window health; edit name/team, hide (keep data, off the board), new token, remove a builder, a team, or one uploaded session |
| `/join` | Register (name, team, optional event code) → personal upload token → one-line sync command for Windows or macOS/Linux, or a file upload |

## Sessions

One deployment runs any number of **sessions** (an event, a batch, a round), each with its own board,
people, badges and database. Every page takes `?s=<slug>`; without it you get the **active** session,
chosen in `/admin`. Upload tokens belong to one session, so a builder's sync command needs no change.
The first start adopts an existing `hub.db` as the session `main`; nothing moves on disk.

## How the competition works

- **Ranking:** raw activity — tool calls (ties broken by tokens). Rank arrows compare with 15 minutes ago.
- **17 builder badges and 4 team badges**, each with bronze/silver/gold tiers. The best holder of each wears its **crown**,
  which can change hands live: Token Titan, Tool Maestro, Ship It, Test Pilot, Clean Run, Comeback Kid, MCP Explorer,
  Delegator, Cache Wizard, Frugal Builder, Context Whale, Night Owl, Speedrunner, Marathoner, Researcher, Code Sculptor,
  Polyglot; teams: All Hands, Balanced Squad, Token Treasury, Hive Mind. Rules live in `hub/badges.py`.
- **Power hour:** every clock hour has a new challenge (tool calls, test runs, tokens, lines changed, commits,
  research). Only work done *during* that hour counts.
- **Live events:** badge unlocks, crown changes, climbs into the top 10, and room milestones (1M tokens, 10K calls,
  100 commits…) feed the live feed, toasts and the stage spotlights.

## Run locally

```bash
python server_7400.py                       # http://127.0.0.1:7400, data in ./data/hub.db
python scripts/simulate.py --people 24 --teams 6 --rounds 3 --interval 0     # seed demo data
python -m unittest discover -s tests -t .   # offline tests
```

Python 3.11+, standard library only. Load test (100 builders, uploads every 20 s):
`python scripts/simulate.py --people 100 --teams 20 --rounds 6 --interval 20`
(last run: 700 uploads, 0 failures, p95 35 ms).

## Configuration (environment)

The event variables (name, start/end, time zone, registration code) **seed the first session only**
and are defaults for new ones. After the first start, change them in `/admin`; editing `.env` no
longer changes a running session.


| Variable | Purpose |
|---|---|
| `HUB_PUBLIC_URL` | Public `https://` URL written into the sync scripts. **Set it on any deployment.** |
| `HUB_EVENT_NAME` | Shown on every page |
| `HUB_EVENT_START` / `HUB_EVENT_END` | ISO times. Sessions started before the start are ignored; the end drives the countdowns |
| `HUB_UTC_OFFSET_MINUTES` | Venue time zone for Night Owl and the power-hour clock (default 330 = IST) |
| `HUB_REGISTRATION_CODE` | If set, `/join` asks for it (keeps strangers off the board) |
| `HUB_ADMIN_TOKEN` | Enables `/admin` and its API (header `X-Admin-Token`; endpoints listed at the top of `server_7400.py`). Without it nobody can administer the hub |
| `HUB_DATA_DIR` | Where `registry.json` and the session databases live (`hub.db` for `main`, `sessions/<slug>/hub.db` for the rest) |

## Deploy to Google Cloud

See [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md): one Compute Engine VM, Docker Compose, Caddy for HTTPS,
daily disk snapshots — `PROJECT=<id> ./deploy/gcp/deploy.sh`.

## Layout

```
server_7400.py        HTTP server: pages, JSON API, uploads, sync scripts
hub/metrics.py        session map -> activity metrics (and the public replay timeline)
hub/badges.py         badge rules and tiers
hub/board.py          ranking, teams, crowns, power hour, live events
hub/store.py          SQLite storage (one file per session)
hub/registry.py       sessions: list, active one, token routing, create/delete
hub/join_scripts.py   /join.ps1 and /join.sh (every value is a quoted literal)
static/               pages (white theme: theme.css, app.js shared; stage.css for the projector)
scripts/simulate.py   synthetic participants for demos and load tests
sync_to_hub.py        Python alternative to the one-line sync command
deploy/gcp/           Compute Engine deployment
```

`leaderboard_data.json` and `uploaded_sessions/` are from the previous version and are not read any more; they hold
real participants' session data, so delete them before publishing this repository anywhere.
