"""Leaderboard assembly: ingest uploads, rank people and teams, award badges, emit live events."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from . import badges as B
from . import metrics as M
from .store import Store, key

LIVE_WINDOW_S = 5 * 60
CONNECTED_WINDOW_S = 90  # sync loops report every 20 s

# What a sync loop may report about itself (counts and labels only; never paths or content).
_SYNC_INTS = ("found", "eligible", "uploaded", "synced")
RANK_CHECKPOINT_S = 15 * 60
CACHE_TTL_S = 5.0
MIN_REBUILD_S = 3.0

POWER_HOUR = (
    ("calls", "Most tool calls this hour"),
    ("verifyRuns", "Most test & build runs this hour"),
    ("tokens", "Most tokens this hour"),
    ("linesChanged", "Most lines changed this hour"),
    ("commits", "Most commits this hour"),
    ("webCalls", "Most research this hour"),
)

MILESTONES = {
    "tokens": (1e6, 1e7, 1e8, 1e9, 1e10),
    "calls": (1e3, 1e4, 5e4, 1e5, 5e5),
    "commits": (10, 50, 100, 500, 1000),
    "participants": (10, 25, 50, 75, 100),
}

# Metrics shown in tables (the rest stay on detail pages).
TABLE_METRICS = ("calls", "tokens", "outputTokens", "commits", "prs", "mcpCalls", "errors", "verifyRuns",
                 "linesChanged", "webCalls", "subagents", "costUsd", "activeMs", "sessions")


# Metrics a session may rank by (all higher-is-better). Ties fall back to calls, then tokens.
RANKABLE = ("calls", "tokens", "outputTokens", "commits", "prs", "linesChanged", "verifyRuns", "mcpCalls", "activeMs")
# Agent names agentplay writes into maps; the admin page offers these for "agents allowed".
KNOWN_AGENTS = ("claude-code", "codex", "cursor", "copilot", "copilot-vscode", "opencode", "kiro", "gemini-cli",
                "antigravity-cli", "command-code", "goose")


@dataclass
class Config:
    """One session's settings. Environment variables seed the first session; /admin edits them after."""
    event_name: str = "Catalyst"
    event_start: datetime | None = None
    event_end: datetime | None = None
    utc_offset_min: int = 330
    registration_code: str = ""
    registration_open: bool = True
    uploads_open: bool = True
    archived: bool = False  # read-only: no registrations, no uploads
    rank_by: str = "calls"
    agents: tuple[str, ...] = field(default_factory=tuple)  # empty = every agent counts
    announcement: str = ""

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("event_start", "event_end"):
            d[k] = d[k].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if d[k] else None
        d["agents"] = list(self.agents)
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any], base: "Config | None" = None) -> "Config":
        """Validated settings; `d` may be partial (missing keys keep `base`). Raises ValueError."""
        cfg = base or cls()
        out: dict[str, Any] = {}
        if "event_name" in d:
            name = " ".join(str(d["event_name"] or "").split())[:80]
            if not name:
                raise ValueError("Name is required.")
            out["event_name"] = name
        for k in ("event_start", "event_end"):
            if k in d:
                v = str(d[k] or "").strip()
                parsed = M.parse_ts(v) if v else None
                if v and parsed is None:
                    raise ValueError(f"{k.replace('event_', '').title()} must be an ISO time.")
                out[k] = parsed
        if "utc_offset_min" in d:
            try:
                off = int(d["utc_offset_min"])
            except (TypeError, ValueError):
                raise ValueError("Time zone offset must be minutes from UTC.") from None
            if not -720 <= off <= 840:
                raise ValueError("Time zone offset must be between -720 and 840 minutes.")
            out["utc_offset_min"] = off
        if "registration_code" in d:
            out["registration_code"] = str(d["registration_code"] or "").strip()[:64]
        for k in ("registration_open", "uploads_open", "archived"):
            if k in d:
                out[k] = bool(d[k])
        if "rank_by" in d:
            if d["rank_by"] not in RANKABLE:
                raise ValueError("Rank by must be one of: " + ", ".join(RANKABLE))
            out["rank_by"] = d["rank_by"]
        if "agents" in d:
            agents = d["agents"] if isinstance(d["agents"], list) else []
            out["agents"] = tuple(sorted({str(a).strip()[:40] for a in agents if str(a).strip()}))[:20]
        if "announcement" in d:
            out["announcement"] = "".join(ch for ch in str(d["announcement"] or "") if ch.isprintable()).strip()[:280]
        cfg = replace(cfg, **out)
        if cfg.event_start and cfg.event_end and cfg.event_end <= cfg.event_start:
            raise ValueError("End must be after start.")
        return cfg


class Rejected(ValueError):
    pass


def _pick(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m.get(k, 0) for k in TABLE_METRICS}


class Board:
    def __init__(self, store: Store, config: Config, slug: str = "main") -> None:
        self.store = store
        self.slug = slug
        saved = store.meta_get("settings")
        try:
            self.cfg = Config.from_json(saved, Config()) if isinstance(saved, dict) else config
        except ValueError:
            self.cfg = config
        if not isinstance(saved, dict):
            store.meta_set("settings", self.cfg.to_json())  # env seeds a session once; /admin owns it after
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None
        self._cache_version = -1
        self._cache_at = 0.0
        self._last_ranks: dict[str, int] | None = None

    # -- sync heartbeat -------------------------------------------------------
    def heartbeat(self, participant: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        """Store what the participant's sync loop last saw, so 'why am I not on the board' is answerable."""
        def num(v: Any) -> int:
            try:
                return max(0, min(10**6, int(v)))
            except (TypeError, ValueError):
                return 0

        def label(v: Any, n: int) -> str:
            return "".join(ch for ch in str(v or "") if ch.isprintable())[:n]

        skipped = body.get("skipped") if isinstance(body.get("skipped"), dict) else {}
        agents = body.get("agents") if isinstance(body.get("agents"), list) else []
        status = {k: num(body.get(k)) for k in _SYNC_INTS}
        status.update({
            "skipped": {label(k, 80): num(v) for k, v in list(skipped.items())[:6]},
            "agents": sorted({label(a, 40) for a in agents[:12] if a}),
            "error": label(body.get("error"), 300),
            "since": label(body.get("since"), 40),
            "client": label(body.get("client"), 40),
        })
        self.store.set_sync_status(participant["id"], status)
        return {"ok": True}

    # -- ingest -------------------------------------------------------------
    def ingest(self, participant: dict[str, Any], raw_json: bytes, doc: Any) -> dict[str, Any]:
        M.validate_map(doc)
        if self.cfg.archived or not self.cfg.uploads_open:
            return {"ok": True, "skipped": "uploads are closed for this session"}
        sess = doc["session"]
        agent = str(sess.get("agentTool") or "unknown")
        if self.cfg.agents and agent not in self.cfg.agents:
            return {"ok": True, "skipped": f"{agent} does not count in this session"}
        if str(sess.get("role") or "root") in ("subagent", "helper"):
            return {"ok": True, "skipped": "subagent session (counted in its parent)"}
        started = M.parse_ts(sess.get("startedAt"))
        if self.cfg.event_start and started and started < self.cfg.event_start:
            return {"ok": True, "skipped": "session started before the event"}
        if self.cfg.event_end and ((started and started > self.cfg.event_end) or datetime.now(UTC) > self.cfg.event_end):
            return {"ok": True, "skipped": "event time window has ended"}

        content_hash = hashlib.sha256(raw_json).hexdigest()
        pid = participant["id"]
        if self.store.is_known_hash(pid, content_hash):
            return {"ok": True, "unchanged": True}

        session_id = str(sess.get("id") or "")[:200]
        if not session_id:
            raise Rejected("Session map has no session id.")
        owner = self.store.session_owner(session_id)
        if owner is not None and owner != pid:  # one agent session counts for one builder only
            return {"ok": True, "skipped": "this session is already synced by another participant"}
        metrics = M.extract(doc, self.cfg.utc_offset_min)
        changed = self.store.upsert_session(pid, session_id, content_hash, sess.get("startedAt"), metrics, M.minimize(doc))
        if changed:
            totals = M.merge([r["metrics"] for r in self.store.session_rows(pid)])
            self.store.snapshot(pid, _pick(totals))
        return {"ok": True, "sessionId": session_id, "calls": metrics["calls"], "tokens": metrics["tokens"]}

    # -- settings -------------------------------------------------------------
    def update_settings(self, changes: dict[str, Any]) -> Config:
        cfg = Config.from_json(changes, self.cfg)
        self.store.meta_set("settings", cfg.to_json())
        self.cfg = cfg
        self.invalidate()
        return cfg

    def _rank_key(self, m: dict[str, Any]) -> tuple[int, int, int]:
        return (m.get(self.cfg.rank_by, 0), m["calls"], m["tokens"])

    def remove_session(self, name: str, session_id: str) -> bool:
        p = self.store.by_name(name)
        if not p or not self.store.remove_session(p["id"], session_id):
            return False
        self.store.snapshot(p["id"], _pick(M.merge([r["metrics"] for r in self.store.session_rows(p["id"])])))
        self.invalidate()
        return True

    # -- build --------------------------------------------------------------
    def invalidate(self) -> None:
        """Rebuild on the next request (after an admin removal, rather than up to MIN_REBUILD_S later)."""
        with self._lock:
            self._cache = None

    def payload(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            # Uploads arrive several times a second at a big event; rebuilding on each would keep
            # the server busy for 100 polling browsers. Serve data at most MIN_REBUILD_S old.
            stale = self._cache_version != self.store.version and now - self._cache_at >= MIN_REBUILD_S
            if self._cache is None or stale or now - self._cache_at > CACHE_TTL_S:
                version = self.store.version
                self._cache = self._build(now)
                self._cache_version, self._cache_at = version, now
            return self._cache

    def _build(self, now: float) -> dict[str, Any]:
        hidden = set(self.store.hidden())
        flagged = set(self.store.flagged())
        people = [p for p in self.store.participants() if key(p["name"]) not in hidden]
        by_pid: dict[int, list[dict[str, Any]]] = {}
        for r in self.store.session_rows():
            by_pid.setdefault(r["participant_id"], []).append(r)

        statuses = self.store.sync_statuses()
        players: list[dict[str, Any]] = []
        for p in people:
            rows = by_pid.get(p["id"], [])
            totals = M.merge([r["metrics"] for r in rows])
            last = max((r["uploaded_at"] for r in rows), default=None)
            status = statuses.get(p["id"])
            connected = bool(status and now - status["ts"] < CONNECTED_WINDOW_S)
            players.append({
                "id": p["id"], "name": p["name"], "team": p["team"], "teamKey": p["team_key"], "key": key(p["name"]),
                "totals": totals, "badges": B.evaluate(B.PERSONAL, totals), "lastActive": last,
                "live": connected or bool(last and now - last < LIVE_WINDOW_S), "connected": connected,
                "flagged": key(p["name"]) in flagged,
                "sync": status, "joinedAt": p["created_at"],
            })

        players.sort(key=lambda x: (self._rank_key(x["totals"]), -x["joinedAt"]), reverse=True)
        for i, pl in enumerate(players, start=1):
            pl["rank"] = i
        checkpoint = self._rank_checkpoint({pl["key"]: pl["rank"] for pl in players}, now)
        for pl in players:
            prev = checkpoint.get(pl["key"])
            pl["delta"] = (prev - pl["rank"]) if prev else 0

        teams = self._teams(players)
        p_crowns = B.crowns(B.PERSONAL, [(pl["key"], pl["totals"], pl["badges"]) for pl in players])
        t_crowns = B.crowns(B.TEAM, [(t["key"], t["totals"], t["badges"]) for t in teams])
        for pl in players:
            pl["crowns"] = [b for b, k in p_crowns.items() if k == pl["key"]]
        for t in teams:
            t["crowns"] = [b for b, k in t_crowns.items() if k == t["key"]]

        room = M.merge([pl["totals"] for pl in players])
        summary = {**_pick(room), "participants": len(players), "teams": len(teams),
                   "live": sum(1 for pl in players if pl["live"]),
                   "badgesEarned": sum(len(pl["badges"]) for pl in players)}

        self._emit(players, teams, p_crowns, t_crowns, summary)
        by_key = {pl["key"]: pl for pl in players}
        by_team = {t["key"]: t for t in teams}

        return {
            "event": self._event_info(now),
            "summary": summary,
            "hourly": room["hourly"],
            "players": [self._public_player(pl) for pl in players],
            "teams": [self._public_team(t) for t in teams],
            "awards": self._awards(players, teams, p_crowns, t_crowns, by_key, by_team),
            "powerHour": self._power_hour(players, now),
            "_players": by_key,
            "_teams": by_team,
        }

    def _teams(self, players: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for pl in players:
            grouped.setdefault(pl["teamKey"], []).append(pl)
        teams = []
        for tkey, members in grouped.items():
            totals = M.merge([m["totals"] for m in members])
            team_view = {**totals, "memberCount": len(members),
                         "activeMembers": sum(1 for m in members if m["totals"]["sessions"] > 0),
                         "memberCalls": [m["totals"]["calls"] for m in members]}
            teams.append({
                "key": tkey, "name": min(members, key=lambda m: m["joinedAt"])["team"], "members": members,
                "totals": team_view, "badges": B.evaluate(B.TEAM, team_view),
                "live": any(m["live"] for m in members),
            })
        teams.sort(key=lambda t: self._rank_key(t["totals"]), reverse=True)
        for i, t in enumerate(teams, start=1):
            t["rank"] = i
        return teams

    def _rank_checkpoint(self, ranks: dict[str, int], now: float) -> dict[str, int]:
        cp = self.store.meta_get("rank_checkpoint")
        if not cp or now - cp["ts"] > RANK_CHECKPOINT_S:
            prev = cp["ranks"] if cp else ranks
            self.store.meta_set("rank_checkpoint", {"ts": now, "ranks": ranks, "prev": prev})
            return prev
        return cp.get("prev") or cp["ranks"]

    # -- live events --------------------------------------------------------
    def _emit(self, players, teams, p_crowns, t_crowns, summary) -> None:
        stored = self.store.badge_tiers()
        for subject_type, items in (("player", players), ("team", teams)):
            for s in items:
                subject = f"{subject_type}:{s['key']}"
                for badge_id, tier in s["badges"].items():
                    if stored.get((subject, badge_id), 0) < tier:
                        self.store.set_badge_tier(subject, badge_id, tier)
                        self.store.add_event("badge", {
                            "subjectType": subject_type, "name": s["name"],
                            "team": s.get("team", s["name"]), "badge": badge_id, "tier": tier,
                        })

        names = {pl["key"]: pl["name"] for pl in players} | {f"team:{t['key']}": t["name"] for t in teams}
        prev_crowns = self.store.meta_get("crowns", {})
        current = {**p_crowns, **{b: f"team:{k}" for b, k in t_crowns.items()}}
        for badge_id, holder in current.items():
            if prev_crowns.get(badge_id) != holder:
                self.store.add_event("crown", {"badge": badge_id, "name": names.get(holder, holder),
                                               "prev": names.get(prev_crowns.get(badge_id, ""), None)})
        if current != prev_crowns:
            self.store.meta_set("crowns", current)

        reached = set(self.store.meta_get("milestones", []))
        new = []
        for metric, steps in MILESTONES.items():
            for step in steps:
                mkey = f"{metric}:{int(step)}"
                if summary.get(metric, 0) >= step and mkey not in reached:
                    new.append(mkey)
                    self.store.add_event("milestone", {"metric": metric, "value": int(step)})
        if new:
            self.store.meta_set("milestones", sorted(reached | set(new)))

        ranks = {pl["key"]: pl["rank"] for pl in players}
        if self._last_ranks is not None:
            for pl in players:
                before = self._last_ranks.get(pl["key"])
                # Only moves into the top 10 are news; churn further down would flood the feed.
                if before and pl["rank"] <= 10 and (before - pl["rank"] >= 3 or pl["rank"] <= 3 < before):
                    self.store.add_event("rank", {"name": pl["name"], "team": pl["team"], "from": before, "to": pl["rank"]})
        self._last_ranks = ranks

    # -- shapes -------------------------------------------------------------
    def event_info(self) -> dict[str, Any]:
        return self._event_info(time.time())

    def _event_info(self, now: float) -> dict[str, Any]:
        iso = lambda d: d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None  # noqa: E731
        c = self.cfg
        winners = self.store.meta_get("winners", None)
        return {"name": c.event_name, "slug": self.slug, "start": iso(c.event_start), "end": iso(c.event_end),
                "utcOffsetMin": c.utc_offset_min, "requiresCode": bool(c.registration_code),
                "registrationOpen": c.registration_open and not c.archived,
                "uploadsOpen": c.uploads_open and not c.archived, "archived": c.archived,
                "status": self.status(now), "rankBy": c.rank_by, "agents": list(c.agents),
                "announcement": c.announcement, "winners": winners, "now": int(now)}

    def status(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        if self.cfg.archived:
            return "archived"
        winners = self.store.meta_get("winners", None)
        if isinstance(winners, dict) and winners.get("ended"):
            return "ended"
        if self.cfg.event_start and now < self.cfg.event_start.timestamp():
            return "upcoming"
        if self.cfg.event_end and now >= self.cfg.event_end.timestamp():
            return "ended"
        return "live"

    @staticmethod
    def _public_player(pl: dict[str, Any]) -> dict[str, Any]:
        t = pl["totals"]
        return {"name": pl["name"], "team": pl["team"], "rank": pl["rank"], "delta": pl["delta"], "live": pl["live"],
                "flagged": pl.get("flagged", False),
                "lastActive": pl["lastActive"], "agentTools": t["agentTools"], "metrics": _pick(t),
                "badges": pl["badges"], "crowns": pl["crowns"]}

    @staticmethod
    def _public_team(t: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": t["name"], "rank": t["rank"], "live": t["live"], "metrics": _pick(t["totals"]),
            "badges": t["badges"], "crowns": t["crowns"], "memberCount": t["totals"]["memberCount"],
            "members": [{"name": m["name"], "rank": m["rank"], "live": m["live"], "metrics": _pick(m["totals"]),
                         "badges": m["badges"]} for m in t["members"]],
        }

    @staticmethod
    def _awards(players, teams, p_crowns, t_crowns, by_key, by_team) -> dict[str, Any]:
        def board(badge: B.Badge, subjects, crown_key, label):
            scored = []
            for s in subjects:
                v = badge.value(s["totals"])
                if v is not None and badge.id in s["badges"]:
                    scored.append((v, s))
            scored.sort(key=lambda x: x[0], reverse=not badge.lower_better)
            holder = crown_key.get(badge.id)
            return {
                **badge.public(),
                "holder": label(holder) if holder else None,
                "top": [{"name": s["name"], "team": s.get("team"), "value": v, "tier": s["badges"][badge.id]}
                        for v, s in scored[:5]],
                "earned": [sum(1 for s in subjects if s["badges"].get(badge.id) == tier) for tier in (1, 2, 3)],
            }

        return {
            "personal": [board(b, players, p_crowns, lambda k: by_key[k]["name"]) for b in B.PERSONAL],
            "team": [board(b, teams, t_crowns, lambda k: by_team[k]["name"]) for b in B.TEAM],
        }

    def _power_hour(self, players: list[dict[str, Any]], now: float) -> dict[str, Any]:
        shift = timedelta(minutes=self.cfg.utc_offset_min)
        local = datetime.fromtimestamp(now, UTC) + shift
        start_local = local.replace(minute=0, second=0, microsecond=0)
        start = int((start_local - shift).timestamp())
        metric, title = POWER_HOUR[(start // 3600) % len(POWER_HOUR)]
        base = self.store.baselines(start)
        scores = []
        for pl in players:
            b = base.get(pl["id"])
            delta = pl["totals"].get(metric, 0) - (b.get(metric, 0) if b else pl["totals"].get(metric, 0))
            if delta > 0:
                scores.append({"name": pl["name"], "team": pl["team"], "value": delta})
        scores.sort(key=lambda s: s["value"], reverse=True)
        return {"metric": metric, "title": title, "endsAt": start + 3600, "top": scores[:10]}

    # -- detail pages -------------------------------------------------------
    def player(self, name: str) -> dict[str, Any] | None:
        data = self.payload()
        pl = data["_players"].get(key(name))
        if not pl:
            return None
        t = pl["totals"]
        rows = sorted(self.store.session_rows(pl["id"]), key=lambda r: r["uploaded_at"], reverse=True)
        return {
            **self._public_player(pl),
            "of": len(data["players"]),
            "connected": pl["connected"],
            "sync": pl["sync"],
            "totals": {k: v for k, v in t.items()},
            "progress": [_progress(b, t, pl["badges"].get(b.id, 0)) for b in B.PERSONAL],
            "sessions": [{
                "id": r["session_id"], "startedAt": r["started_at"], "uploadedAt": r["uploaded_at"],
                "agentTool": (r["metrics"].get("agentTools") or ["unknown"])[0], "calls": r["metrics"]["calls"],
                "tokens": r["metrics"]["tokens"], "errors": r["metrics"]["errors"], "commits": r["metrics"]["commits"],
            } for r in rows[:50]],
        }

    def team(self, name: str) -> dict[str, Any] | None:
        data = self.payload()
        t = data["_teams"].get(key(name))
        if not t:
            return None
        return {
            **self._public_team(t),
            "of": len(data["teams"]),
            "totals": {k: v for k, v in t["totals"].items() if k != "memberCalls"},
            "progress": [_progress(b, t["totals"], t["badges"].get(b.id, 0)) for b in B.TEAM],
        }

    def session_timeline(self, name: str, session_id: str) -> dict[str, Any] | None:
        p = self.store.by_name(name) if key(name) in self.payload()["_players"] else None  # hidden stay hidden
        doc = self.store.session_map(p["id"], session_id) if p else None
        if doc is None:
            return None
        return {"sessionId": session_id, "agentTool": (doc.get("session") or {}).get("agentTool"),
                "steps": M.timeline(doc)}


def _progress(b: B.Badge, m: dict[str, Any], tier: int) -> dict[str, Any]:
    v = b.value(m)
    nxt = b.tiers[tier] if tier < 3 else None
    if v is None or nxt is None:
        pct = 100 if tier == 3 else 0
    elif b.lower_better:
        pct = 100 if v <= nxt else max(0, min(99, round(100 * nxt / v)))
    else:
        pct = max(0, min(99, round(100 * v / nxt))) if nxt else 0
    return {"id": b.id, "tier": tier, "value": v, "next": nxt, "pct": pct}
