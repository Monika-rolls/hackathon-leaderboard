"""Participant sync scripts served at /join.ps1 and /join.sh.

Both wrap the same small Python sync loop (SYNC_PY): agentplay needs Python anyway, and one
loop keeps Windows and macOS/Linux behaving identically. The loop picks sessions from
agentplay's index (not file times: `agentplay build` rewrites every map) and uploads only
sessions run in the folder the command was started in and active in the last hour (and,
with an event start, started after it). Every other session is ignored without being
counted or reported. A session is re-uploaded only when its content hash changes.

Every value placed in a script is a quoted literal (no expansion), so names, teams, tokens
and the hub URL can never become code. SYNC_PY itself is a constant.
"""

from __future__ import annotations

import re

SYNC_INTERVAL_S = 20

SYNC_PY = r'''
import gzip, json, os, shutil, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

HUB = os.environ["HC_HUB"].rstrip("/")
TOKEN = os.environ["HC_TOKEN"]
INTERVAL = int(os.environ.get("HC_INTERVAL") or 20)
ROOT = os.path.join(os.path.expanduser("~"), ".agentplay")
CLIENT = "sync/2"
TOOLS = "Claude Code, Cursor, Codex, Copilot, Gemini CLI, OpenCode, Antigravity"


def ts(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# Only sessions with activity in the last ACTIVE_HOURS are read or uploaded. With an event
# start, a session must also have started after it (the hub applies the same rule).
ACTIVE_HOURS = 1
SINCE = ts(os.environ.get("HC_SINCE") or "")
BEFORE_EVENT = "started before the event"


def active_cutoff():
    return datetime.now(timezone.utc) - timedelta(hours=ACTIVE_HOURS)

_AG = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity")
if os.path.isdir(_AG):
    os.environ.setdefault("AGENTPLAY_ANTIGRAVITY_HOMES", _AG)


def norm(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(str(p)))))


# Only sessions whose agent ran in this folder (or below it) count: the folder the
# participant ran the join command in.
FOLDER = norm(os.environ.get("HC_FOLDER") or os.getcwd())


def within(path):
    p = norm(path)
    return p == FOLDER or p.startswith(FOLDER.rstrip(os.sep) + os.sep)


def session_folders():
    """sessionId -> the folder its agent ran in. agentplay knows it for most tools but keeps
    it out of its index, so this reads its source index; any failure falls back to repos."""
    try:
        import contextlib, io, logging
        from agentplay import discover
        logging.disable(logging.WARNING)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                idx = discover.local_source(discover.Homes.default(), set(discover.AGENTS))._build_index()
        finally:
            logging.disable(logging.NOTSET)
        return {str(sid): str(ref.cwd) for sid, ref in idx.items() if getattr(ref, "cwd", None)}
    except Exception:
        return {}


def repo_roots():
    """Repo name -> its checkout (agentplay's repos.json): the fallback for tools with no cwd."""
    try:
        with open(os.path.join(ROOT, "repos.json"), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


folders = {}


def in_folder(r):
    """True / False, or None when the session's folder can't be told."""
    sid = str(r.get("sessionId") or "")
    if sid not in folders:
        folders.update(session_folders())
        folders.setdefault(sid, "")  # don't rescan for it every cycle
    if folders[sid]:
        return within(folders[sid])
    roots = repo_roots()
    paths = [roots[n] for n in (r.get("repos") or []) if isinstance(roots.get(n), str)]
    return any(within(p) for p in paths) if paths else None


def build():
    """Run agentplay build; return an error line, or '' on success."""
    exe = shutil.which("agentplay")
    # Build only recently active sessions, with repo-relative paths (never full laptop paths).
    args = ["build", "--since", "%dh" % ACTIVE_HOURS, "--path-mode", "relative"]
    cmd = [exe] + args if exe else [sys.executable, "-m", "agentplay"] + args
    env = dict(os.environ)
    try:
        res = subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
    except OSError as e:
        return "could not run agentplay: %s" % e
    if res.returncode != 0:
        lines = res.stderr.decode("utf-8", "replace").strip().splitlines()
        return (lines[-1] if lines else "agentplay build exited with code %s" % res.returncode)[:300]
    return ""


def post(path, body, ctype):
    req = urllib.request.Request(HUB + path, data=body, method="POST",
                                 headers={"X-Upload-Token": TOKEN, "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


sent = {}


def eligible(r, cutoff):
    """Main session, active within the window, run in FOLDER. Anything else is ignored
    silently: other folders' sessions are never counted, reported or uploaded."""
    if r.get("role") in ("subagent", "helper") or not r.get("calls"):
        return False
    last = ts(r.get("endedAt") or r.get("updatedAt") or r.get("startedAt") or "")
    if last is None or last < cutoff:
        return False
    return in_folder(r) is True  # unknown folder never counts


def sync():
    stats = {"found": 0, "eligible": 0, "uploaded": 0, "skipped": {}, "agents": set()}
    cutoff = active_cutoff()

    def skip(reason):
        stats["skipped"][reason] = stats["skipped"].get(reason, 0) + 1

    try:
        with open(os.path.join(ROOT, "index.json"), encoding="utf-8") as f:
            rows = list((json.load(f).get("sessions") or {}).values())
    except (OSError, ValueError):
        rows = []
    for r in rows:
        if not eligible(r, cutoff):
            continue
        stats["found"] += 1
        stats["agents"].add(str(r.get("agentTool") or "unknown"))
        if SINCE:
            started = ts(r.get("startedAt") or "")
            if started is None or started < SINCE:
                skip(BEFORE_EVENT)
                continue
        stats["eligible"] += 1
        sid = str(r.get("sessionId") or "")
        digest = "%s|%s" % (r.get("contentHash"), r.get("updatedAt"))
        if not sid or sent.get(sid) == digest:
            continue
        path = os.path.join(ROOT, "maps", "%s.json" % r.get("key"))
        if not os.path.isfile(path):
            skip("map file missing")
            continue
        with open(path, "rb") as f:
            body = gzip.compress(f.read())
        try:
            out = post("/api/upload", body, "application/gzip")
            first_time = sid not in sent
            sent[sid] = digest
            if out.get("calls"):
                stats["uploaded"] += 1
                print("[+] %s session synced: %s tool calls" % (r.get("agentTool"), out["calls"]), flush=True)
            elif out.get("skipped"):
                skip(out["skipped"])
                if first_time:
                    print("[!] %s session not counted: %s" % (r.get("agentTool"), out["skipped"]), flush=True)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            if e.code == 401:
                sys.exit("[-] Your token was not accepted. Get a fresh command from the /join page.")
            sent[sid] = digest  # a map the hub rejects won't change by retrying
            skip("rejected by the hub")
            print("[-] Upload refused (%s): %s" % (e.code, detail), flush=True)
        except (urllib.error.URLError, OSError) as e:
            print("[-] Hub unreachable, will retry: %s" % e, flush=True)
    return stats


def heartbeat(stats, error):
    body = {"found": stats["found"], "eligible": stats["eligible"], "uploaded": stats["uploaded"],
            "synced": len(sent), "skipped": stats["skipped"], "agents": sorted(stats["agents"]),
            "error": error, "since": max(filter(None, [SINCE, active_cutoff()])).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "client": CLIENT}
    try:
        post("/api/heartbeat", json.dumps(body).encode("utf-8"), "application/json")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("[-] Your token was not accepted. Get a fresh command from the /join page.")
    except (urllib.error.URLError, OSError):
        pass


def explain(stats, error):
    if error:
        print("[-] agentplay build failed: %s" % error, flush=True)
    if not stats["found"]:
        print("[!] No agent session active in the last %dh in this folder yet. Start one here with a" % ACTIVE_HOURS, flush=True)
        print("    supported tool (%s); it appears within %ss." % (TOOLS, INTERVAL), flush=True)
        return
    reasons = ", ".join("%d %s" % (n, why) for why, n in sorted(stats["skipped"].items()))
    print("[*] %d active session(s) in this folder (%s); %d count%s." % (
        stats["found"], ", ".join(sorted(stats["agents"])), stats["eligible"], " - not counted: " + reasons if reasons else ""), flush=True)
    if not stats["eligible"]:
        print("[!] None count yet. Start (or keep using) an agent session in %s; it appears within %ss." % (FOLDER, INTERVAL), flush=True)


if FOLDER == norm("~") or os.path.dirname(FOLDER) == FOLDER:
    sys.exit("[-] %s is your home folder or a drive root. cd into your project folder and run this again." % FOLDER)
print("[*] Watching only: %s (sessions active in the last %dh). Nothing else is uploaded." % (FOLDER, ACTIVE_HOURS), flush=True)
print("[*] Building your local session index (first run can take a minute)...", flush=True)
error = build()
stats = sync()
explain(stats, error)
heartbeat(stats, error)
print("[+] Live sync on, every %ss. Keep this window open while you build. Ctrl+C to stop." % INTERVAL, flush=True)
shown = (stats["found"], stats["eligible"], error)
try:
    while True:
        time.sleep(INTERVAL)
        error = build()
        stats = sync()
        heartbeat(stats, error)
        if (stats["found"], stats["eligible"], error) != shown and (not shown[1] or error):
            explain(stats, error)  # e.g. the first session that counts, or a new build error
        shown = (stats["found"], stats["eligible"], error)
except KeyboardInterrupt:
    print("Stopped.")
'''


def ps_quote(value: str) -> str:
    # Single-quoted PowerShell literal; PowerShell also treats the Unicode single quotes as quotes.
    return "'" + re.sub("(['‘’‚‛])", r"\1\1", value) + "'"


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def ps1(hub_url: str, token: str, name: str, team: str, since: str = "") -> str:
    head = (
        "# Catalyst - live sync (Windows PowerShell). Press Ctrl+C to stop.\n"
        f"$env:HC_HUB = {ps_quote(hub_url)}\n"
        f"$env:HC_TOKEN = {ps_quote(token)}\n"
        f"$env:HC_SINCE = {ps_quote(since)}\n"
        f"$env:HC_INTERVAL = '{SYNC_INTERVAL_S}'\n"
        f"$Name = {ps_quote(name)}\n"
        f"$Team = {ps_quote(team)}\n"
    )
    body = r"""
Write-Host ""
Write-Host "  Catalyst  |  $Name  ($Team)" -ForegroundColor Cyan
Write-Host "  Syncing to $env:HC_HUB" -ForegroundColor DarkGray
Write-Host ""

$py = $null
foreach ($cand in @('python', 'py', 'python3')) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) {
        & $cand -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $cand; break }
    }
}
if (-not $py) {
    Write-Host "[-] Python 3.10+ is required. Install it from https://www.python.org/downloads/ and run this again." -ForegroundColor Red
    return
}

if (-not (Get-Command agentplay -ErrorAction SilentlyContinue)) {
    & $py -c "import agentplay" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[*] Installing agentplay (pip --user)..." -ForegroundColor Yellow
        & $py -m pip install --user --quiet agentplay
    }
}

$sync = Join-Path ([IO.Path]::GetTempPath()) "catalyst-sync.py"
Set-Content -Path $sync -Value @'
""" + SYNC_PY.strip("\n") + r"""
'@ -Encoding ASCII
& $py $sync
"""
    return head + body


def sh(hub_url: str, token: str, name: str, team: str, since: str = "") -> str:
    head = (
        "#!/usr/bin/env bash\n"
        "# Catalyst - live sync (macOS / Linux). Press Ctrl+C to stop.\n"
        f"export HC_HUB={sh_quote(hub_url)}\n"
        f"export HC_TOKEN={sh_quote(token)}\n"
        f"export HC_SINCE={sh_quote(since)}\n"
        f"export HC_INTERVAL={SYNC_INTERVAL_S}\n"
        f"NAME={sh_quote(name)}\n"
        f"TEAM={sh_quote(team)}\n"
    )
    body = r"""
printf '\n  Catalyst  |  %s  (%s)\n  Syncing to %s\n\n' "$NAME" "$TEAM" "$HC_HUB"

PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        PY="$cand"; break
    fi
done
if [ -z "$PY" ]; then
    echo "[-] Python 3.10+ is required. Install it and run this again."; exit 1
fi

if ! command -v agentplay >/dev/null 2>&1 && ! "$PY" -c 'import agentplay' >/dev/null 2>&1; then
    echo "[*] Installing agentplay (pip --user)..."
    "$PY" -m pip install --user --quiet agentplay || pipx install agentplay || true
fi

exec "$PY" - <<'HC_SYNC_PY'
""" + SYNC_PY.strip("\n") + """
HC_SYNC_PY
"""
    return head + body
