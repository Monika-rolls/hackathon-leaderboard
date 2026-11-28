"""Catalyst: live leaderboard for AI coding-agent activity.

Sessions: each hackathon event/round has its own board and database (hub/registry.py).
Every page and /api/ call takes ?s=<slug>; without it, the active session is used.
Upload tokens belong to one session, so uploads, heartbeats and /join.* find theirs.

Pages:  /  /team?name=  /player?name=  /awards  /stage  /join  /admin
API:    GET  /api/leaderboard | /api/config | /api/player?name= | /api/team?name= | /api/session?participant=&id=
        GET  /api/events?since= | /api/badges | /api/me | /healthz
        POST /api/register {name, team, code?} -> upload token (shown once)
        POST /api/upload   (X-Upload-Token header) raw/gzip agentplay map, or {"mapBase64": ...}
Admin (X-Admin-Token header, ?s=<slug>):
        GET  /api/admin/sessions | /api/admin/participants | /api/admin/participant?name= | /api/admin/export
        POST /api/admin/sessions {action: create|update|activate|archive|reopen|delete, slug, settings?, copyFrom?}
        POST /api/admin/reissue | remove {name} | remove-team {team} | remove-session {name, sessionId}
             | update-participant {name, newName?, newTeam?, hidden?} | clear-feed
Sync:   GET  /join.ps1?token=  /join.sh?token=

Configuration (environment):
  HUB_DATA_DIR            where registry.json and the session databases live (default ./data)
  HUB_PUBLIC_URL          public https URL baked into the sync scripts (set this on any deployment)
  (the next four seed the first session and are defaults for new ones; /admin edits them after)
  HUB_EVENT_NAME          shown on every page
  HUB_EVENT_START/END     ISO times; sessions started before START are ignored, END drives the countdown
  HUB_UTC_OFFSET_MINUTES  venue time zone for night-owl and power-hour clocks (default 330 = IST)
  HUB_REGISTRATION_CODE   if set, /api/register requires it
  HUB_ADMIN_TOKEN         enables the admin endpoints
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hmac
import io
import json
import mimetypes
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from hub import badges as B
from hub import join_scripts
from hub.board import KNOWN_AGENTS, RANKABLE, Board, Config
from hub.metrics import parse_ts
from hub.registry import Registry
from hub.store import NameTaken

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_MAP_BYTES = 60 * 1024 * 1024  # decompressed cap (gzip bombs)
MAX_JSON_BODY = 4096
MAX_LABEL_LEN = 40

PAGES = {"/": "index.html", "/team": "team.html", "/player": "player.html", "/awards": "awards.html",
         "/stage": "stage.html", "/join": "join.html", "/admin": "admin.html"}
STATIC_FILES = {f"/static/{p.name}": p for p in STATIC_DIR.glob("*") if p.is_file()} if STATIC_DIR.is_dir() else {}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors *; base-uri 'none'; form-action 'self'")
CSP_STRICT = CSP.replace("frame-ancestors *", "frame-ancestors 'none'")

_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?$")
_PUBLIC_URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?$")


def clean_label(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isprintable())
    return " ".join(text.split())[:MAX_LABEL_LEN]


def resolve_hub_url(host_hdr: str) -> str | None:
    public = os.environ.get("HUB_PUBLIC_URL", "").strip().rstrip("/")
    if public:
        return public if _PUBLIC_URL_RE.match(public) else None
    if not _HOST_RE.match(host_hdr):
        return None
    local = host_hdr.split(":")[0] in ("127.0.0.1", "localhost") or re.match(r"^\d+\.\d+\.\d+\.\d+(:\d+)?$", host_hdr)
    return f"{'http' if local else 'https'}://{host_hdr}"


def decode_map(raw: bytes) -> tuple[bytes, Any]:
    """Return (decompressed JSON bytes, parsed doc). Accepts raw JSON, gzip, or {"mapBase64"/"map": ...}."""
    def gunzip(data: bytes) -> bytes:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            out = gz.read(MAX_MAP_BYTES + 1)
        if len(out) > MAX_MAP_BYTES:
            raise ValueError("Session map is larger than 60 MB once decompressed.")
        return out

    if raw[:2] == b"\x1f\x8b":
        raw = gunzip(raw)
    doc = json.loads(raw.decode("utf-8"))
    if isinstance(doc, dict) and ("mapBase64" in doc or "map" in doc):
        if doc.get("mapBase64"):
            inner = base64.b64decode(doc["mapBase64"], validate=True)
            raw = gunzip(inner) if inner[:2] == b"\x1f\x8b" else inner
            doc = json.loads(raw.decode("utf-8"))
        else:
            doc = doc["map"]
            raw = json.dumps(doc, sort_keys=True).encode("utf-8")
    return raw, doc


def csv_text(value: Any) -> str:
    """A spreadsheet-safe cell: text starting like a formula (=, +, -, @, tab, CR) gets a leading
    apostrophe, so a participant named '=HYPERLINK(...)' can't run when judges open the CSV."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _public(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if not k.startswith("_")}


class Handler(BaseHTTPRequestHandler):
    server_version = "Catalyst/2"
    timeout = 30  # a client that stalls mid-body can't hold a thread forever
    registry: Registry

    # -- responses ----------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store",
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if content_type.startswith("text/html"):
            csp = CSP_STRICT if self.path.startswith("/admin") else CSP
            self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802  (uptime checks; same headers as GET, no body)
        self.do_GET()

    def do_OPTIONS(self) -> None:  # noqa: N802  (CORS preflight for external showcase sites)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json(self, status: int, payload: Any, cors: bool = False) -> None:
        extra = {"Access-Control-Allow-Origin": "*"} if cors else None
        self._send(status, json.dumps(payload, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8", extra=extra)

    def _text(self, status: int, text: str) -> None:
        self._send(status, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _file(self, path: Path, cache: str) -> None:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, path.read_bytes(), ctype, cache)

    def _read_body(self, limit: int) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > limit:
            self._json(HTTPStatus.BAD_REQUEST, {"error": f"Body must be 1 byte to {limit // 1024} KB."})
            return None
        return self.rfile.read(length)

    def _session(self, slug: str) -> Board | None:
        """The board for ?s=<slug>, or the active session's; answers 404 itself when unknown."""
        board = self.registry.get(slug or None)
        if board is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "No such session"})
        return board

    # -- GET ----------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        qs = parse_qs(url.query)
        arg = lambda k: (qs.get(k) or [""])[0].strip()  # noqa: E731
        path = url.path.rstrip("/") or "/"

        if path in PAGES:
            return self._file(STATIC_DIR / PAGES[path], "no-cache")
        if path in STATIC_FILES:
            return self._file(STATIC_FILES[path], "public, max-age=300")
        if path == "/healthz":
            return self._text(HTTPStatus.OK, "ok\n")
        if path == "/api/badges":
            return self._json(HTTPStatus.OK, {"personal": [b.public() for b in B.PERSONAL],
                                              "team": [b.public() for b in B.TEAM], "tiers": list(B.TIER_NAMES)})
        if path == "/api/me":
            found = self.registry.find_token(self.headers.get("X-Upload-Token", ""))
            return (self._json(HTTPStatus.OK, {**found[1], "slug": found[0].slug}) if found
                    else self._json(HTTPStatus.UNAUTHORIZED, {"error": "Unknown token"}))
        if path in ("/join.ps1", "/join.sh"):
            return self._join_script(path, arg("token"))
        if path.startswith("/api/admin/"):
            return self._admin_get(path, arg)
        if not path.startswith("/api/"):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        board = self._session(arg("s"))
        if board is None:
            return
        if path == "/api/leaderboard":
            return self._json(HTTPStatus.OK, _public(board.payload()), cors=True)
        if path == "/api/config":
            return self._json(HTTPStatus.OK, board.event_info(), cors=True)
        if path == "/api/events":
            try:
                since = int(arg("since") or 0)
            except ValueError:
                since = 0
            return self._json(HTTPStatus.OK, {"events": board.store.events_since(since),
                                              "announcement": board.cfg.announcement,
                                              "winners": board.store.meta_get("winners", None)}, cors=True)
        if path == "/api/player":
            data = board.player(arg("name"))
            return self._json(HTTPStatus.OK, data, cors=True) if data else self._json(HTTPStatus.NOT_FOUND, {"error": "No such participant"}, cors=True)
        if path == "/api/team":
            data = board.team(arg("name"))
            return self._json(HTTPStatus.OK, data, cors=True) if data else self._json(HTTPStatus.NOT_FOUND, {"error": "No such team"}, cors=True)
        if path == "/api/session":
            data = board.session_timeline(arg("participant"), arg("id"))
            return self._json(HTTPStatus.OK, data, cors=True) if data else self._json(HTTPStatus.NOT_FOUND, {"error": "No such session"}, cors=True)
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def _join_script(self, path: str, token: str) -> None:
        found = self.registry.find_token(token)
        if not found:
            return self._text(HTTPStatus.NOT_FOUND, "Unknown token. Register at /join first.\n")
        board, participant = found
        hub = resolve_hub_url(self.headers.get("Host") or "")
        if hub is None:
            return self._text(HTTPStatus.BAD_REQUEST, "Invalid Host header or HUB_PUBLIC_URL.\n")
        gen = join_scripts.ps1 if path == "/join.ps1" else join_scripts.sh
        since = board.event_info()["start"] or ""
        self._text(HTTPStatus.OK, gen(hub, token, participant["name"], participant["team"], since))

    # -- POST ---------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        path = url.path
        slug = (parse_qs(url.query).get("s") or [""])[0].strip()
        if path == "/api/upload":
            return self._upload()
        if path == "/api/register":
            return self._register(slug)
        if path == "/api/heartbeat":
            return self._heartbeat()
        if path.startswith("/api/admin/"):
            return self._admin_post(path, slug)
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def _json_body(self) -> dict[str, Any] | None:
        raw = self._read_body(MAX_JSON_BODY)
        if raw is None:
            return None
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Body must be a JSON object."})
            return None
        return body

    def _register(self, slug: str) -> None:
        body = self._json_body()
        if body is None:
            return
        board = self._session(slug)
        if board is None:
            return
        if board.cfg.archived or not board.cfg.registration_open:
            return self._json(HTTPStatus.FORBIDDEN, {"error": "Registration is closed for this session."})
        code = board.cfg.registration_code
        if code and not hmac.compare_digest(str(body.get("code") or "").encode(), code.encode()):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "Wrong event code."})
        name, team = clean_label(body.get("name")), clean_label(body.get("team"))
        if not name or not team:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": f"Name and team are required (max {MAX_LABEL_LEN} characters)."})
        try:
            participant, token = board.store.register(name, team)
        except NameTaken:
            return self._json(HTTPStatus.CONFLICT, {"error": f"'{name}' is already registered. Ask an organiser to reissue your token."})
        board.store.add_event("joined", {"name": name, "team": team})
        self._json(HTTPStatus.OK, {"name": participant["name"], "team": participant["team"], "token": token,
                                   "slug": board.slug, "hub": resolve_hub_url(self.headers.get("Host") or "")})

    def _heartbeat(self) -> None:
        body = self._json_body()
        if body is None:
            return
        found = self.registry.find_token(self.headers.get("X-Upload-Token", ""))
        if not found:
            return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Missing or unknown X-Upload-Token. Register at /join."})
        self._json(HTTPStatus.OK, found[0].heartbeat(found[1], body))

    def _upload(self) -> None:
        # Read the (size-capped) body before answering: replying first makes clients see a
        # connection reset instead of the 401 that tells them their token is wrong.
        raw = self._read_body(MAX_UPLOAD_BYTES)
        if raw is None:
            return
        found = self.registry.find_token(self.headers.get("X-Upload-Token", ""))
        if not found:
            return self._json(HTTPStatus.UNAUTHORIZED, {"error": "Missing or unknown X-Upload-Token. Register at /join."})
        try:
            data, doc = decode_map(raw)
            result = found[0].ingest(found[1], data, doc)
        except (ValueError, KeyError, TypeError, OSError, EOFError) as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)[:300]})
        self._json(HTTPStatus.OK, result)

    # -- admin --------------------------------------------------------------
    def _is_admin(self) -> bool:
        admin = os.environ.get("HUB_ADMIN_TOKEN", "abcdefg123456").strip()
        given = self.headers.get("X-Admin-Token", "").strip()
        if given and admin and hmac.compare_digest(given.encode(), admin.encode()):
            return True
        self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid admin credentials."})
        return False

    def _admin_get(self, path: str, arg) -> None:
        if not self._is_admin():
            return
        if path == "/api/admin/sessions":
            return self._json(HTTPStatus.OK, {"sessions": self.registry.list(), "active": self.registry.active_slug,
                                              "rankable": list(RANKABLE), "agents": list(KNOWN_AGENTS)})
        board = self._session(arg("s"))
        if board is None:
            return
        if path == "/api/admin/participants":
            return self._json(HTTPStatus.OK, {"participants": board.store.admin_rows()})
        if path == "/api/admin/participant":
            p = board.store.by_name(arg("name"))
            if not p:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "No such participant"})
            rows = sorted(board.store.session_rows(p["id"]), key=lambda r: r["uploaded_at"], reverse=True)
            return self._json(HTTPStatus.OK, {"name": p["name"], "team": p["team"], "sessions": [{
                "id": r["session_id"], "startedAt": r["started_at"], "uploadedAt": r["uploaded_at"],
                "agentTool": (r["metrics"].get("agentTools") or ["unknown"])[0],
                "calls": r["metrics"]["calls"], "tokens": r["metrics"]["tokens"]} for r in rows]})
        if path == "/api/admin/export":
            body = board.store.export_bytes(self.registry.dir)
            return self._send(HTTPStatus.OK, body, "application/vnd.sqlite3",
                              extra={"Content-Disposition": f'attachment; filename="hackathon-{board.slug}.db"'})
        if path == "/api/admin/export-csv":
            import csv
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["Rank", "Participant", "Team", "Agents", "Tool Calls", "Tokens", "Commits", "MCP Calls", "Errors", "Spend USD", "Badges", "Flagged", "Live"])
            for pl in board.payload()["players"]:
                m = pl["metrics"]
                w.writerow([
                    pl["rank"], csv_text(pl["name"]), csv_text(pl["team"]), csv_text(", ".join(pl["agentTools"])),
                    m.get("calls", 0), m.get("tokens", 0), m.get("commits", 0),
                    m.get("mcpCalls", 0), m.get("errors", 0), f"{m.get('costUsd', 0.0):.2f}",
                    len(pl["badges"]), "YES" if pl.get("flagged") else "NO", "YES" if pl["live"] else "NO",
                ])
            return self._send(HTTPStatus.OK, buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8",
                              extra={"Content-Disposition": f'attachment; filename="hackathon-{board.slug}-scores.csv"'})
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def _admin_post(self, path: str, slug: str) -> None:
        if not self._is_admin():
            return
        body = self._json_body()
        if body is None:
            return
        if path == "/api/admin/sessions":
            return self._admin_sessions(body)
        board = self._session(slug)
        if board is None:
            return
        store = board.store
        if path == "/api/admin/clear-all":
            store.clear_all()
            store.meta_set("winners", None)
            board.invalidate()
            return self._json(HTTPStatus.OK, {"ok": True, "message": "All leaderboard data cleared."})
        if path == "/api/admin/end-event":
            top_n = 1 if int(body.get("topN") or 3) == 1 else 3
            raw_ent = str(body.get("entity") or "teams")
            entity = raw_ent if raw_ent in ("teams", "players", "both") else "teams"
            payload = board.payload()
            rank_by = board.cfg.rank_by or "calls"

            def _pick_winners(src, kind):
                out = []
                for idx, item in enumerate(src[:top_n], start=1):
                    m = item.get("metrics") or {}
                    out.append({
                        "place": idx,
                        "name": item["name"],
                        "subtitle": item.get("team", "") if kind == "players" else f"{item.get('memberCount', 1)} member{'s' if item.get('memberCount', 1) != 1 else ''}",
                        "calls": m.get("calls", 0),
                        "commits": m.get("commits", 0),
                        "tokens": m.get("tokens", 0),
                        "score": m.get(rank_by, m.get("calls", 0)),
                        "rankBy": rank_by,
                    })
                return out

            team_winners = _pick_winners(payload["teams"], "teams") if entity in ("teams", "both") else []
            player_winners = _pick_winners(payload["players"], "players") if entity in ("players", "both") else []
            winners_list = player_winners if entity == "players" else team_winners
            import time as _time
            win_doc = {
                "ended": True, "topN": top_n, "entity": entity,
                "winners": winners_list, "teamWinners": team_winners, "playerWinners": player_winners,
                "endedAt": int(_time.time()),
            }
            store.meta_set("winners", win_doc)
            board.update_settings({"uploads_open": False})
            return self._json(HTTPStatus.OK, {"ok": True, "winners": win_doc})
        if path == "/api/admin/revert-event":
            store.meta_set("winners", None)
            board.update_settings({"uploads_open": True})
            return self._json(HTTPStatus.OK, {"ok": True})
        if path == "/api/admin/clear-feed":
            store.clear_events()
            return self._json(HTTPStatus.OK, {"ok": True})
        if path == "/api/admin/remove-team":
            team = clean_label(body.get("team"))
            names = store.remove_team(team)
            board.invalidate()
            return self._json(HTTPStatus.OK if names else HTTPStatus.NOT_FOUND,
                              {"removed": names} if names else {"error": "No such team"})
        name = clean_label(body.get("name"))
        if path == "/api/admin/flag":
            p = store.by_name(name)
            if not p:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "No such participant"})
            store.set_flagged(name, bool(body.get("flagged", True)))
            board.invalidate()
            return self._json(HTTPStatus.OK, {"ok": True, "flagged": bool(body.get("flagged", True))})
        if path == "/api/admin/reissue":
            token = store.reissue(name)
            return self._json(HTTPStatus.OK, {"name": name, "token": token}) if token else self._json(HTTPStatus.NOT_FOUND, {"error": "No such participant"})
        if path == "/api/admin/remove":
            ok = store.remove(name)
            board.invalidate()
            return self._json(HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND, {"removed": ok})
        if path == "/api/admin/remove-session":
            ok = board.remove_session(name, str(body.get("sessionId") or ""))
            return self._json(HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND, {"removed": ok})
        if path == "/api/admin/update-participant":
            p = store.by_name(name)
            if not p:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "No such participant"})
            if "hidden" in body:
                store.set_hidden(name, bool(body["hidden"]))
            if "flagged" in body:
                store.set_flagged(name, bool(body["flagged"]))
            new_name = clean_label(body.get("newName")) or p["name"]
            new_team = clean_label(body.get("newTeam")) or p["team"]
            if (new_name, new_team) != (p["name"], p["team"]):
                try:
                    store.rename(name, new_name, new_team)
                except NameTaken:
                    return self._json(HTTPStatus.CONFLICT, {"error": f"'{new_name}' is already registered."})
            board.invalidate()
            return self._json(HTTPStatus.OK, {"name": new_name, "team": new_team})
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def _admin_sessions(self, body: dict[str, Any]) -> None:
        action, slug = str(body.get("action") or ""), str(body.get("slug") or "").strip().lower()
        settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
        try:
            if action == "create":
                board = self.registry.create(slug, settings, copy_from=str(body.get("copyFrom") or "") or None)
                return self._json(HTTPStatus.OK, {"slug": board.slug})
            board = self.registry.get(slug)
            if board is None:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "No such session"})
            if action == "update":
                board.update_settings(settings)
            elif action == "activate":
                self.registry.set_active(slug)
            elif action in ("archive", "reopen"):
                board.update_settings({"archived": action == "archive"})
            elif action == "delete":
                self.registry.delete(slug)
            else:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "Unknown action"})
        except ValueError as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        self._json(HTTPStatus.OK, {"ok": True})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # access logs would carry upload tokens in /join.* query strings


def load_config() -> Config:
    """Defaults from the environment: they seed the first session, and new ones start from them."""
    def ts(name: str):
        value = os.environ.get(name, "").strip()
        parsed = parse_ts(value) if value else None
        if value and parsed is None:
            raise SystemExit(f"{name} must be an ISO time such as 2026-12-05T09:00:00+05:30")
        return parsed

    return Config(
        event_name=os.environ.get("HUB_EVENT_NAME", "Catalyst").strip()[:80] or "Catalyst",
        event_start=ts("HUB_EVENT_START"),
        event_end=ts("HUB_EVENT_END"),
        utc_offset_min=int(os.environ.get("HUB_UTC_OFFSET_MINUTES", "330")),
        registration_code=os.environ.get("HUB_REGISTRATION_CODE", ""),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Catalyst leaderboard server")
    ap.add_argument("--host", default=os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 7400))
    ap.add_argument("--data-dir", default=os.environ.get("HUB_DATA_DIR") or str(BASE_DIR / "data"))
    args = ap.parse_args()

    Handler.registry = Registry(Path(args.data_dir), load_config())
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"Catalyst on http://{args.host}:{args.port}  (data: {args.data_dir})")
    server.serve_forever()


if __name__ == "__main__":
    main()
