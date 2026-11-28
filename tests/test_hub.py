"""Offline tests: metrics, badges, storage, live events, join scripts and HTTP handling.

    python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import gzip
import json
import os
import shlex
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path

import server_7400 as srv
from hub import badges as B
from hub import join_scripts
from hub import metrics as M
from hub.board import Board, Config
from hub.registry import Registry
from hub.store import NameTaken, Store


def make_map(sid="s1", calls=10, errors=0, verify_after_errors=False, mcp=None, tokens=0, started="2026-12-05T20:00:00Z",
             role="root", edits=0):
    events = []
    t = 0
    for i in range(calls):
        t += 1000
        events.append({"kind": "exec", "tool": "Bash", "t": t, "err": i < errors})
    if verify_after_errors:
        events.append({"kind": "verify", "tool": "Bash", "t": t + 1000, "err": False})
    for i in range(edits):
        events.append({"kind": "edit", "tool": "Edit", "t": t + 2000 + i, "ins": 10, "del": 5})
    for server in mcp or []:
        events.append({"kind": "mcp", "tool": "x", "server": server, "t": t + 3000})
    if tokens:
        events.append({"kind": "llm", "in": tokens // 4, "cr": tokens // 2, "cw": tokens // 8, "out": tokens // 8, "t": 1})
    return {"schema": "agentplay-v1",
            "session": {"id": sid, "agentTool": "claude-code", "startedAt": started, "durationMs": t, "role": role,
                        "models": ["m1"]},
            "totals": {"maxContext": 1234}, "events": events,
            "files": [{"id": 1, "path": "C:\\Users\\someone\\secret-project\\src\\main.py"}],
            "repos": [{"name": "proj", "remote": "https://example.com/private/proj.git"}]}


class MetricsTests(unittest.TestCase):
    def test_extract_counts(self):
        m = M.extract(make_map(calls=12, errors=3, verify_after_errors=True, mcp=["github", "linear"], tokens=800, edits=2))
        self.assertEqual(m["calls"], 12 + 1 + 2 + 2)
        self.assertEqual(m["errors"], 3)
        self.assertEqual(m["comebacks"], 1)
        self.assertEqual(m["mcpServers"], ["github", "linear"])
        self.assertEqual(m["tokens"], 200 + 400 + 100 + 100)
        self.assertEqual(m["linesChanged"], 30)
        self.assertEqual(m["verifyPass"], 1)

    def test_night_calls_use_venue_offset(self):
        # 20:00Z = 01:30 IST, so every call is a night call at +330 and none at UTC.
        self.assertEqual(M.extract(make_map(calls=5), utc_offset_min=330)["nightCalls"], 5)
        self.assertEqual(M.extract(make_map(calls=5), utc_offset_min=0)["nightCalls"], 0)

    def test_merge_sums_and_unions(self):
        a, b = M.extract(make_map("a", calls=3, mcp=["github"])), M.extract(make_map("b", calls=4, mcp=["slack"]))
        m = M.merge([a, b])
        self.assertEqual(m["sessions"], 2)
        self.assertEqual(m["calls"], a["calls"] + b["calls"])
        self.assertEqual(m["mcpServers"], ["github", "slack"])

    def test_minimize_strips_paths_and_remotes(self):
        doc = M.minimize(make_map())
        self.assertEqual(doc["files"][0]["path"], "main.py")
        self.assertEqual(doc["repos"], [{"name": "proj"}])

    def test_invalid_map(self):
        with self.assertRaises(ValueError):
            M.extract({"session": {}})

    def test_hostile_numbers_are_clamped(self):
        doc = make_map()
        doc["totals"] = {"calls": "1e99", "tokens": -5, "costUsd": "nan"}
        m = M.extract(doc)
        self.assertEqual(m["tokens"], 0)
        self.assertEqual(m["costUsd"], 0.0)


class BadgeTests(unittest.TestCase):
    def test_tiers(self):
        m = M.merge([M.extract(make_map(calls=600))])
        self.assertEqual(B.BY_ID["tool_maestro"].tier(m), 2)

    def test_lower_is_better(self):
        m = M.merge([M.extract(make_map(calls=200, errors=3))])
        self.assertEqual(B.BY_ID["clean_run"].tier(m), 3)  # 1.5% error rate
        small = M.merge([M.extract(make_map(calls=50))])
        self.assertEqual(B.BY_ID["clean_run"].tier(small), 0)  # needs 100+ calls

    def test_crowns_pick_best_holder(self):
        hi = M.merge([M.extract(make_map(calls=700))])
        lo = M.merge([M.extract(make_map(calls=150))])
        crowns = B.crowns(B.PERSONAL, [("lo", lo, B.evaluate(B.PERSONAL, lo)), ("hi", hi, B.evaluate(B.PERSONAL, hi))])
        self.assertEqual(crowns["tool_maestro"], "hi")


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "hub.db")
        self.board = Board(self.store, Config())

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def upload(self, p, doc):
        raw = json.dumps(doc).encode()
        return self.board.ingest(p, raw, doc)

    def test_register_unique_names(self):
        self.store.register("Ada", "Team A")
        with self.assertRaises(NameTaken):
            self.store.register("  ada ", "Other")

    def test_upload_rank_and_events(self):
        a, _ = self.store.register("Ada", "Team A")
        b, _ = self.store.register("Bob", "Team A")
        self.upload(a, make_map("s1", calls=150))
        self.upload(b, make_map("s2", calls=40))
        data = self.board.payload()
        self.assertEqual([p["name"] for p in data["players"]], ["Ada", "Bob"])
        self.assertEqual(data["teams"][0]["metrics"]["calls"], 190)
        types = {e["type"] for e in self.store.events_since(0)}
        self.assertTrue({"badge", "crown", "milestone"} <= types or {"badge", "crown"} <= types)
        self.assertIn("tool_maestro", data["players"][0]["crowns"])

    def test_remove_participant_and_team_leaves_nothing_behind(self):
        a, _ = self.store.register("Ada", "Team A")
        b, _ = self.store.register("Bob", "team a")
        c, _ = self.store.register("Cy", "Team C")
        for p, sid in ((a, "s1"), (b, "s2"), (c, "s3")):
            self.upload(p, make_map(sid, calls=150))
        self.store.add_event("joined", {"name": "Ada", "team": "Team A"})
        self.board.payload()
        self.assertTrue(self.store.remove("ADA"))
        self.assertFalse(self.store.remove("Ada"))
        self.assertEqual(self.store.remove_team("TEAM A"), ["Bob"])
        self.assertEqual(self.store.remove_team("Team A"), [])
        self.board.invalidate()
        data = self.board.payload()
        self.assertEqual([p["name"] for p in data["players"]], ["Cy"])
        self.assertEqual([t["name"] for t in data["teams"]], ["Team C"])
        gone = {"ada", "bob", "team a"}
        self.assertFalse([e for e in self.store.events_since(0) if str(e.get("name", "")).lower() in gone])
        self.assertFalse(any(s.split(":", 1)[1] in gone for s, _ in self.store.badge_tiers()))
        for table in ("sessions", "snapshots"):
            self.assertEqual(self.store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1)
        self.store.register("Ada", "New team")  # the name is free again

    def test_same_content_is_unchanged(self):
        a, _ = self.store.register("Ada", "T")
        self.upload(a, make_map("s1"))
        self.assertTrue(self.upload(a, make_map("s1"))["unchanged"])

    def test_session_counts_once_and_cannot_be_overwritten(self):
        a, _ = self.store.register("Ada", "T")
        m, _ = self.store.register("Mallory", "X")
        self.upload(a, make_map("shared", calls=50))
        self.assertIn("skipped", self.upload(m, make_map("shared", calls=0)))
        self.assertEqual(len(self.board.session_timeline("Ada", "shared")["steps"]), 50)
        self.assertIsNone(self.board.session_timeline("Mallory", "shared"))
        self.assertEqual(self.board.payload()["teams"][0]["metrics"]["calls"], 50)
        # The owner keeps syncing growth of its session after anyone else's attempt.
        self.assertEqual(self.upload(a, make_map("shared", calls=60))["calls"], 60)
        self.assertIn("skipped", self.upload(m, make_map("shared", calls=70)))

    def test_subagent_and_pre_event_sessions_skipped(self):
        board = self.board
        board.update_settings({"event_start": "2026-12-06T00:00:00Z"})
        a, _ = self.store.register("Ada", "T")
        self.assertIn("skipped", board.ingest(a, b"x", make_map(role="subagent")))
        self.assertIn("skipped", board.ingest(a, b"y", make_map(started="2026-12-05T00:00:00Z")))

    def test_heartbeat_makes_builder_live_and_is_sanitised(self):
        a, _ = self.store.register("Ada", "T")
        self.assertFalse(self.board.payload()["players"][0]["live"])
        self.board.heartbeat(a, {"found": 3, "eligible": "x", "skipped": {"no activity in the last 12 hours": 3},
                                 "agents": ["claude-code"], "error": "boom\x00" + "e" * 500, "extra": "ignored"})
        self.board._cache = None  # heartbeats don't invalidate the cache on purpose; force a rebuild here
        detail = self.board.player("Ada")
        self.assertTrue(detail["live"] and detail["connected"])
        self.assertEqual(detail["sync"]["found"], 3)
        self.assertEqual(detail["sync"]["eligible"], 0)
        self.assertEqual(len(detail["sync"]["error"]), 300)
        self.assertNotIn("extra", detail["sync"])

    def test_power_hour_ignores_first_dump(self):
        a, _ = self.store.register("Ada", "T")
        self.upload(a, make_map("s1", calls=100))
        self.assertEqual(self.board.payload()["powerHour"]["top"], [])

    def test_team_progress_and_player_detail(self):
        a, _ = self.store.register("Ada", "T")
        self.upload(a, make_map("s1", calls=120))
        self.assertEqual(self.board.player("ada")["name"], "Ada")
        self.assertEqual(self.board.team("t")["members"][0]["name"], "Ada")
        self.assertIsNone(self.board.player("nobody"))

    def test_settings_validate_and_persist(self):
        with self.assertRaises(ValueError):
            self.board.update_settings({"rank_by": "errors"})
        with self.assertRaises(ValueError):
            self.board.update_settings({"event_start": "2026-12-06T10:00:00Z", "event_end": "2026-12-06T09:00:00Z"})
        with self.assertRaises(ValueError):
            self.board.update_settings({"event_name": "   "})
        self.board.update_settings({"event_name": "Batch 2", "announcement": "Lunch\x07 at 1", "agents": ["codex", "codex"]})
        again = Board(self.store, Config(event_name="ignored once saved"))
        self.assertEqual((again.cfg.event_name, again.cfg.announcement, again.cfg.agents), ("Batch 2", "Lunch at 1", ("codex",)))

    def test_closed_uploads_and_agent_filter(self):
        a, _ = self.store.register("Ada", "T")
        self.board.update_settings({"uploads_open": False})
        self.assertIn("closed", self.upload(a, make_map("s1"))["skipped"])
        self.board.update_settings({"uploads_open": True, "agents": ["codex"]})
        self.assertIn("does not count", self.upload(a, make_map("s1"))["skipped"])
        self.board.update_settings({"agents": []})
        self.assertEqual(self.upload(a, make_map("s1"))["calls"], 10)

    def test_event_times_are_published_in_utc(self):
        self.board.update_settings({"event_start": "2026-11-24T09:00:00+05:30"})
        self.assertEqual(self.board.event_info()["start"], "2026-11-24T03:30:00Z")

    def test_rank_by_setting(self):
        a, _ = self.store.register("Ada", "T")
        b, _ = self.store.register("Bob", "T")
        self.upload(a, make_map("s1", calls=100))
        self.upload(b, make_map("s2", calls=10, tokens=10**6))
        self.assertEqual(self.board.payload()["players"][0]["name"], "Ada")
        self.board.update_settings({"rank_by": "tokens"})
        self.assertEqual(self.board.payload()["players"][0]["name"], "Bob")

    def test_hide_rename_and_remove_session(self):
        a, _ = self.store.register("Ada", "Team A")
        self.store.register("Bob", "Team B")
        self.upload(a, make_map("s1", calls=150))
        self.upload(a, make_map("s2", calls=5))
        self.board.payload()
        self.assertTrue(self.store.set_hidden("ada", True))
        self.board.invalidate()
        self.assertEqual([p["name"] for p in self.board.payload()["players"]], ["Bob"])
        self.assertIsNone(self.board.player("Ada"))
        self.assertIsNone(self.board.session_timeline("Ada", "s1"))
        self.store.set_hidden("Ada", False)
        with self.assertRaises(NameTaken):
            self.store.rename("Ada", "bob", "Team A")
        self.store.rename("Ada", "Ada L", "Team B")
        self.assertTrue(any(s == "player:ada l" for s, _ in self.store.badge_tiers()))
        self.assertTrue(self.board.remove_session("Ada L", "s1"))
        self.assertFalse(self.board.remove_session("Ada L", "s1"))
        data = self.board.payload()
        self.assertEqual([t["name"] for t in data["teams"]], ["Team B"])
        self.assertEqual(next(p for p in data["players"] if p["name"] == "Ada L")["metrics"]["calls"], 5)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        for r in getattr(self, "regs", []):
            for b in r.boards.values():
                b.store.close()
        self.tmp.cleanup()

    def open(self):
        r = Registry(self.dir, Config(event_name="From env"))
        self.regs = [*getattr(self, "regs", []), r]
        return r

    def test_adopts_existing_db_and_routes_tokens(self):
        legacy = Store(self.dir / "hub.db")
        _, old_token = legacy.register("Ada", "T")
        legacy.close()
        reg = self.open()
        self.assertEqual(reg.active_slug, "main")
        self.assertEqual(reg.get().cfg.event_name, "From env")
        self.assertEqual(reg.find_token(old_token)[0].slug, "main")
        b2 = reg.create("batch-2", {"event_name": "Batch 2"}, copy_from="main")
        _, new_token = b2.store.register("Ada", "T")  # same name, different session
        self.assertEqual(reg.find_token(new_token)[0].slug, "batch-2")
        self.assertIsNone(reg.find_token("nope"))
        with self.assertRaises(ValueError):
            reg.create("Bad Slug", {})
        with self.assertRaises(ValueError):
            reg.create("batch-2", {})
        reg.set_active("batch-2")
        with self.assertRaises(ValueError):
            reg.delete("batch-2")
        reopened = self.open()
        self.assertEqual((reopened.active_slug, sorted(reopened.boards)), ("batch-2", ["batch-2", "main"]))
        self.assertEqual(reopened.get().cfg.event_name, "Batch 2")

    def test_delete_session_removes_its_files(self):
        reg = self.open()
        reg.create("old", {"event_name": "Old"})
        self.assertTrue((self.dir / "sessions" / "old" / "hub.db").is_file())
        reg.delete("old")
        self.assertFalse((self.dir / "sessions" / "old").exists())
        self.assertEqual([s["slug"] for s in reg.list()], ["main"])


class JoinScriptTests(unittest.TestCase):
    PAYLOADS = ["$(echo PWNED)", "x'; echo PWNED; '", "`echo PWNED`", 'a"b\\c', "x’; Write-Host PWNED; ‘"]

    def test_sync_counts_only_sessions_in_launch_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = os.path.join(tmp, "proj")
            env = {"HC_HUB": "http://x", "HC_TOKEN": "t", "HC_FOLDER": proj}
            with mock.patch.dict(os.environ, env):
                g: dict = {}
                exec(join_scripts.SYNC_PY.split('if FOLDER == norm("~")')[0], g)
            g["ROOT"] = tmp
            with open(os.path.join(tmp, "repos.json"), "w", encoding="utf-8") as f:
                json.dump({"mine": os.path.join(proj, "sub"), "other": os.path.join(tmp, "proj2")}, f)
            g["session_folders"] = lambda: {"a": os.path.join(proj, "x"), "b": os.path.join(tmp, "proj2")}
            self.assertTrue(g["in_folder"]({"sessionId": "a"}))
            self.assertFalse(g["in_folder"]({"sessionId": "b"}))  # sibling folder with a shared prefix
            self.assertTrue(g["in_folder"]({"sessionId": "c", "repos": ["mine"]}))  # no cwd: repo fallback
            self.assertFalse(g["in_folder"]({"sessionId": "d", "repos": ["other"]}))
            self.assertIsNone(g["in_folder"]({"sessionId": "e"}))

    def test_source_folder_reads_each_tools_own_session_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HC_HUB": "http://x", "HC_TOKEN": "t", "HC_FOLDER": tmp}
            with mock.patch.dict(os.environ, env):
                g: dict = {}
                exec(join_scripts.SYNC_PY.split('if FOLDER == norm("~")')[0], g)
            proj = os.path.join(tmp, "proj")
            proj_uri = "file:///" + proj.replace(os.sep, "/").replace(":", "%3A")

            def write(rel, text):
                path = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                return path

            line = lambda d: json.dumps(d) + chr(10)  # noqa: E731
            cases = {
                "claude/codex/copilot cli": write("a.jsonl", line({"type": "meta", "payload": {"cwd": proj}})),
                "goose": write("b.jsonl", line({"working_dir": proj})),
                "opencode": write("c.json", json.dumps({"id": "x", "directory": proj})),
                "tool input before cwd": write("d.jsonl", line({"input": {"directory": "/elsewhere"}}) + line({"cwd": proj})),
                "vscode workspace": write(os.path.join("workspaceStorage", "h", "chatSessions", "e.jsonl"), "{}"),
                "gemini": write(os.path.join("gemini", "tmp", "p", "chats", "f.json"), "{}"),
            }
            write(os.path.join("workspaceStorage", "h", "workspace.json"), json.dumps({"folder": proj_uri}))
            write(os.path.join("gemini", "tmp", "p", ".project_root"), proj)
            for name, src in cases.items():
                with self.subTest(name):
                    self.assertEqual(g["norm"](g["source_folder"]({"sourcePath": src})), g["norm"](proj))
            self.assertEqual(g["source_folder"]({"sourcePath": write("g.jsonl", line({"x": 1}))}), "")
            self.assertEqual(g["source_folder"]({"sourcePath": os.path.join(tmp, "gone.jsonl")}), "")

    def test_sync_uploads_only_this_folders_recently_active_sessions(self):
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as tmp:
            proj = os.path.join(tmp, "proj")
            env = {"HC_HUB": "http://x", "HC_TOKEN": "t", "HC_FOLDER": proj}
            with mock.patch.dict(os.environ, env):
                g: dict = {}
                exec(join_scripts.SYNC_PY.split('if FOLDER == norm("~")')[0], g)
            g["ROOT"] = tmp
            now = datetime.now(timezone.utc)
            iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
            recent, stale = iso(now - timedelta(minutes=10)), iso(now - timedelta(hours=2))
            g["session_folders"] = lambda: {"here": proj, "idle": proj, "other": os.path.join(tmp, "proj2")}
            rows = {
                "here": {"sessionId": "here", "key": "here", "calls": 5, "updatedAt": recent},
                "idle": {"sessionId": "idle", "key": "idle", "calls": 5, "updatedAt": stale},
                "other": {"sessionId": "other", "key": "other", "calls": 5, "updatedAt": recent},
                "nofolder": {"sessionId": "nofolder", "key": "nofolder", "calls": 5, "updatedAt": recent},
            }
            os.makedirs(os.path.join(tmp, "maps"))
            for k in rows:
                with open(os.path.join(tmp, "maps", k + ".json"), "w", encoding="utf-8") as f:
                    f.write("{}")
            with open(os.path.join(tmp, "index.json"), "w", encoding="utf-8") as f:
                json.dump({"sessions": rows}, f)
            posted = []
            g["post"] = lambda path, body, ctype: posted.append(path) or {"calls": 5}
            stats = g["sync"]()
            self.assertEqual(posted, ["/api/upload"])
            self.assertEqual(list(g["sent"]), ["here"])
            self.assertEqual((stats["found"], stats["eligible"], stats["skipped"]), (1, 1, {}))

    def test_sh_quote_round_trips(self):
        for p in self.PAYLOADS:
            self.assertEqual(shlex.split(join_scripts.sh_quote(p)), [p])

    def test_ps_quote_doubles_every_quote(self):
        for p in self.PAYLOADS:
            q = join_scripts.ps_quote(p)
            inner = q[1:-1]
            for ch in "'\u2018\u2019\u201a\u201b":
                self.assertNotIn(ch, inner.replace(ch * 2, ""), p)  # every quote inside is escaped by doubling


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        srv.Handler.registry = Registry(Path(cls.tmp.name), Config())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for b in srv.Handler.registry.boards.values():
            b.store.close()
        cls.tmp.cleanup()

    def req(self, path, body=None, headers=None, method=None):
        r = urllib.request.Request(self.base + path, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def register(self, name, team="T"):
        status, _, body = self.req("/api/register", json.dumps({"name": name, "team": team}).encode(),
                                   {"Content-Type": "application/json"})
        self.assertEqual(status, 200, body)
        return json.loads(body)["token"]

    def test_pages_have_csp(self):
        status, headers, _ = self.req("/")
        self.assertEqual(status, 200)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])

    def test_head_has_headers_without_body(self):
        status, headers, body = self.req("/healthz", method="HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(headers["Content-Length"], "3")

    def test_static_traversal_blocked(self):
        for p in ("/static/../server_7400.py", "/static/..%2fserver_7400.py", "/static/nope.js"):
            self.assertEqual(self.req(p)[0], 404, p)

    def test_upload_requires_token(self):
        doc = gzip.compress(json.dumps(make_map()).encode())
        self.assertEqual(self.req("/api/upload", doc)[0], 401)
        self.assertEqual(self.req("/api/upload", doc, {"X-Upload-Token": "bogus"})[0], 401)

    def test_upload_and_leaderboard(self):
        token = self.register("Http Ada", "Web")
        status, _, body = self.req("/api/upload", gzip.compress(json.dumps(make_map("h1", calls=33)).encode()),
                                   {"X-Upload-Token": token})
        self.assertEqual(status, 200, body)
        srv.Handler.registry.get().invalidate()  # skip the 3 s rebuild throttle another test may have started
        lb = json.loads(self.req("/api/leaderboard")[2])
        self.assertNotIn("_players", lb)
        self.assertIn("Http Ada", [p["name"] for p in lb["players"]])

    def test_heartbeat_requires_token(self):
        body = json.dumps({"found": 1}).encode()
        self.assertEqual(self.req("/api/heartbeat", body, {"X-Upload-Token": "bogus"})[0], 401)
        token = self.register("Beat")
        self.assertEqual(self.req("/api/heartbeat", body, {"X-Upload-Token": token})[0], 200)

    def test_gzip_bomb_rejected(self):
        token = self.register("Bomber")
        bomb = gzip.compress(b" " * (srv.MAX_MAP_BYTES + 10))
        status, _, body = self.req("/api/upload", bomb, {"X-Upload-Token": token})
        self.assertEqual(status, 400)
        self.assertIn(b"60 MB", body)

    def test_join_script_needs_known_token_and_valid_host(self):
        self.assertEqual(self.req("/join.sh?token=nope")[0], 404)
        token = self.register("Script $(whoami)")
        status, _, body = self.req("/join.sh?token=" + token)
        self.assertEqual(status, 200)
        self.assertIn(b"NAME='Script $(whoami)'", body)
        self.assertEqual(self.req("/join.sh?token=" + token, headers={"Host": 'x"; evil'})[0], 400)

    def test_register_validation(self):
        status, _, _ = self.req("/api/register", json.dumps({"name": "\n\t", "team": "T"}).encode())
        self.assertEqual(status, 400)
        self.register("Dup")
        status, _, _ = self.req("/api/register", json.dumps({"name": "DUP", "team": "T"}).encode())
        self.assertEqual(status, 409)

    def test_admin_list_and_remove_team(self):
        hdr = {"X-Admin-Token": "s3cret"}
        self.register("Rem One", "Doomed")
        self.register("Rem Two", "doomed")
        with mock.patch.dict(os.environ, {"HUB_ADMIN_TOKEN": "s3cret"}):
            self.assertEqual(self.req("/api/admin/participants", headers={"X-Admin-Token": "wrong"})[0], 403)
            status, _, body = self.req("/api/admin/participants", headers=hdr)
            self.assertEqual(status, 200)
            self.assertIn("Rem One", [p["name"] for p in json.loads(body)["participants"]])
            status, _, body = self.req("/api/admin/remove-team", json.dumps({"team": "DOOMED"}).encode(), hdr)
            self.assertEqual((status, sorted(json.loads(body)["removed"])), (200, ["Rem One", "Rem Two"]))
            self.assertEqual(self.req("/api/admin/remove-team", json.dumps({"team": "Doomed"}).encode(), hdr)[0], 404)
        self.assertEqual(self.req("/api/admin/participants")[0], 403)
        self.assertEqual(self.req("/admin")[0], 200)

    def test_end_event_snapshots_standings_and_badges(self):
        hdr = {"X-Admin-Token": "s3cret", "Content-Type": "application/json"}
        post = lambda path, body: self.req(path, json.dumps(body).encode(), hdr)  # noqa: E731
        with mock.patch.dict(os.environ, {"HUB_ADMIN_TOKEN": "s3cret"}):
            self.assertEqual(post("/api/admin/sessions", {"action": "create", "slug": "finale"})[0], 200)
            for name, team, calls in (("Fin Ada", "Alpha", 600), ("Fin Bo", "Beta", 40)):
                _, _, body = self.req("/api/register?s=finale", json.dumps({"name": name, "team": team}).encode(),
                                      {"Content-Type": "application/json"})
                token = json.loads(body)["token"]
                self.req("/api/upload", gzip.compress(json.dumps(make_map(name, calls=calls)).encode()),
                         {"X-Upload-Token": token})
            srv.Handler.registry.get("finale").invalidate()
            status, _, body = post("/api/admin/end-event?s=finale", {"topN": 3, "entity": "both"})
            self.assertEqual(status, 200, body)
        w = json.loads(self.req("/api/config?s=finale")[2])["winners"]
        self.assertEqual([p["name"] for p in w["standings"]["players"]], ["Fin Ada", "Fin Bo"])
        self.assertEqual([t["name"] for t in w["standings"]["teams"]], ["Alpha", "Beta"])
        ada = w["standings"]["players"][0]
        self.assertTrue(ada["badges"])
        for bid in ada["badges"]:
            entry = next(b for b in w["badgeWinners"]["personal"] if b["id"] == bid)
            self.assertIn("Fin Ada", [e["name"] for e in entry["earners"]])

    def test_sessions_routing_and_admin(self):
        hdr = {"X-Admin-Token": "s3cret", "Content-Type": "application/json"}
        post = lambda path, body: self.req(path, json.dumps(body).encode(), hdr)  # noqa: E731
        with mock.patch.dict(os.environ, {"HUB_ADMIN_TOKEN": "s3cret"}):
            status, _, body = post("/api/admin/sessions", {"action": "create", "slug": "round-2",
                                                            "settings": {"event_name": "Round 2", "registration_code": "go"}})
            self.assertEqual(status, 200, body)
            reg = lambda code: self.req("/api/register?s=round-2", json.dumps(  # noqa: E731
                {"name": "Rounder", "team": "R", "code": code}).encode(), {"Content-Type": "application/json"})
            self.assertEqual(reg("wrong")[0], 403)
            status, _, body = reg("go")
            self.assertEqual((status, json.loads(body)["slug"]), (200, "round-2"))
            token = json.loads(body)["token"]
            names = lambda q: [p["name"] for p in json.loads(self.req("/api/leaderboard" + q)[2])["players"]]  # noqa: E731
            self.assertIn("Rounder", names("?s=round-2"))
            self.assertNotIn("Rounder", names(""))
            self.assertEqual(json.loads(self.req("/api/me", headers={"X-Upload-Token": token})[2])["slug"], "round-2")
            self.assertEqual(self.req("/api/leaderboard?s=nope")[0], 404)
            post("/api/admin/sessions", {"action": "update", "slug": "round-2",
                                         "settings": {"registration_open": False, "announcement": "Demos at 5"}})
            self.assertEqual(reg("go")[0], 403)
            events = json.loads(self.req("/api/events?s=round-2")[2])
            self.assertEqual(events["announcement"], "Demos at 5")
            self.assertEqual(post("/api/admin/sessions", {"action": "update", "slug": "round-2",
                                                          "settings": {"rank_by": "nope"}})[0], 400)
            listed = json.loads(self.req("/api/admin/sessions", headers=hdr)[2])
            self.assertEqual({s["slug"] for s in listed["sessions"]} >= {"main", "round-2"}, True)
            status, headers, body = self.req("/api/admin/export?s=round-2", headers=hdr)
            self.assertEqual((status, body[:15]), (200, b"SQLite format 3"))
            self.assertIn("hackathon-round-2.db", headers["Content-Disposition"])
            self.assertEqual(post("/api/admin/update-participant?s=round-2", {"name": "Rounder", "hidden": True})[0], 200)
            self.assertNotIn("Rounder", names("?s=round-2"))
            self.assertEqual(post("/api/admin/sessions", {"action": "delete", "slug": "main"})[0], 400)  # active
            self.assertEqual(post("/api/admin/sessions", {"action": "delete", "slug": "round-2"})[0], 200)
            self.assertEqual(self.req("/api/me", headers={"X-Upload-Token": token})[0], 401)
        self.assertEqual(self.req("/api/admin/sessions")[0], 403)

    def test_csv_cells_cannot_start_formulas(self):
        self.assertEqual(srv.csv_text("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        for bad in ("+1", "-2", "@SUM(A1)", "\tx"):
            self.assertTrue(srv.csv_text(bad).startswith("'"), bad)
        self.assertEqual(srv.csv_text("Ada Lovelace"), "Ada Lovelace")
        self.assertEqual(srv.csv_text("claude-code, codex"), "claude-code, codex")

    def test_admin_disabled_without_token(self):
        status, _, _ = self.req("/api/admin/remove", json.dumps({"name": "Dup"}).encode(), {"X-Admin-Token": ""})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
