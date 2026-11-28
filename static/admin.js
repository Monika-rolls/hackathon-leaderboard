"use strict";
/* Organisers' console: sessions, their settings, and the people on each board. */
(async () => {
  const { esc, fmt, avatar } = HC;
  const KEY = "hc-admin-token";
  const main = document.getElementById("main");
  const OFFSETS = [[-480, "UTC−8 (US Pacific)"], [-300, "UTC−5 (US Eastern)"], [0, "UTC"], [60, "UTC+1 (Central Europe)"],
    [120, "UTC+2"], [180, "UTC+3"], [240, "UTC+4 (Gulf)"], [330, "UTC+5:30 (India)"], [345, "UTC+5:45 (Nepal)"],
    [420, "UTC+7"], [480, "UTC+8 (Singapore)"], [540, "UTC+9 (Japan)"], [600, "UTC+10 (Sydney)"]];
  const STATUS = { live: "var(--green)", upcoming: "var(--blue)", ended: "var(--faint)", archived: "var(--faint)" };
  const state = { sessions: [], active: "", rankable: [], agents: [], sel: "", rows: [], q: "", onlyFlagged: false, issued: null,
    open: null, editing: null, creating: false };
  let token = "";
  try { token = sessionStorage.getItem(KEY) || ""; } catch (_) { /* storage blocked */ }

  await HC.nav("admin");

  const call = (path, body) => HC.api(path, {
    method: body ? "POST" : "GET",
    headers: { "X-Admin-Token": token, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const q = (path) => path + (path.includes("?") ? "&" : "?") + "s=" + encodeURIComponent(state.sel);
  const sel = () => state.sessions.find((s) => s.slug === state.sel);
  const pad = (n) => String(n).padStart(2, "0");
  const toInput = (iso, off) => (iso ? new Date(Date.parse(iso) + off * 60000).toISOString().slice(0, 16) : "");
  const fromInput = (v, off) => (v ? `${v}:00${off < 0 ? "-" : "+"}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)}` : "");
  const slugify = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);

  // ---------- login ----------
  function loginView(error) {
    main.innerHTML = `<div class="card" style="max-width:460px">
      <h3 class="h3">🔒 Admin Console Login</h3>
      <p class="muted" style="margin:6px 0 14px">Enter organiser credentials to manage participants, teams, sessions, flags, and reset leaderboard data.</p>
      <form id="login" style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label for="admUser">Username</label><input class="input" id="admUser" type="text" autocomplete="username" placeholder="Username" required></div>
        <div class="field"><label for="admPass">Password</label><input class="input" id="admPass" type="password" autocomplete="current-password" placeholder="Password" required></div>
        <div style="display:flex;justify-content:flex-end;margin-top:6px"><button class="btn" type="submit">Unlock Console</button></div>
      </form>
      ${error ? `<p style="color:var(--red);margin:10px 0 0">${esc(error)}</p>` : ""}</div>`;
    document.getElementById("login").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const user = document.getElementById("admUser").value.trim();
      const pass = document.getElementById("admPass").value.trim();
      if (user !== "admin") return loginView("Invalid admin credentials.");
      token = pass;
      start();
    });
  }

  async function start() {
    try {
      await loadSessions();
    } catch (e) {
      try { sessionStorage.removeItem(KEY); } catch (_) { /* */ }
      return loginView(token ? e.message : "");
    }
    try { sessionStorage.setItem(KEY, token); } catch (_) { /* */ }
    main.innerHTML = `
      <div style="display:flex;justify-content:flex-end;margin:0 0 16px"><button class="btn light" id="logout">Lock admin</button></div>
      <div id="sessions"></div><div id="endEvent"></div><div id="settings"></div><div id="people"></div>`;
    document.getElementById("logout").addEventListener("click", () => {
      token = ""; try { sessionStorage.removeItem(KEY); } catch (_) { /* */ }
      loginView();
    });
    main.addEventListener("click", onClick);
    HC.copyButtons(main);
    renderSessions();
    renderEndEvent();
    renderSettings();
    await loadPeople();
  }

  async function loadSessions() {
    const d = await call("/api/admin/sessions");
    Object.assign(state, { sessions: d.sessions, active: d.active, rankable: d.rankable, agents: d.agents });
    if (!sel()) state.sel = d.active;
  }

  // ---------- sessions ----------
  function renderSessions(msg) {
    const others = state.sessions;
    document.getElementById("sessions").innerHTML = `
      <div class="card table-card" style="margin-bottom:24px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:16px 18px;flex-wrap:wrap">
          <div><div class="h3">Sessions</div><div class="muted" style="font-size:13px">Each event or round has its own board.
            The <b>active</b> one is what <code>/</code>, <code>/join</code> and <code>/stage</code> show.</div></div>
          <button class="btn" data-a="new">${state.creating ? "Cancel" : "New session"}</button></div>
        ${state.creating ? newForm() : ""}
        ${msg ? `<p class="muted" style="margin:0 18px 12px">${esc(msg)}</p>` : ""}
        <table class="lb"><thead><tr><th>Session</th><th>Status</th><th class="r">Builders</th><th class="r hide-sm">Teams</th>
          <th class="r hide-sm">Tool calls</th><th class="r"></th></tr></thead><tbody>
        ${others.map((s) => `<tr class="${s.slug === state.sel ? "me" : ""}">
          <td><div class="n">${esc(s.settings.event_name)} ${s.active ? '<span class="tier-pill" style="background:var(--ink);color:#fff">active</span>' : ""}</div>
            <div class="t faint">${esc(s.slug)}</div></td>
          <td><span style="color:${STATUS[s.status]};font-weight:600">${esc(s.status)}</span>
            ${s.live ? `<div class="faint" style="font-size:12px">${fmt(s.live)} building now</div>` : ""}</td>
          <td class="r metric">${fmt(s.participants)}</td><td class="r hide-sm">${fmt(s.teams)}</td><td class="r hide-sm">${fmt(s.calls)}</td>
          <td class="r" style="white-space:nowrap">
            ${s.slug === state.sel ? '<span class="muted" style="margin-right:8px">managing</span>' : `<button class="btn light" data-a="pick" data-v="${esc(s.slug)}">Manage</button>`}
            ${s.active ? "" : `<button class="btn light" data-a="activate" data-v="${esc(s.slug)}">Make active</button>`}
            <a class="btn light" href="/?s=${encodeURIComponent(s.slug)}" target="_blank" rel="noopener">Board</a>
            <a class="btn light hide-sm" href="/stage?s=${encodeURIComponent(s.slug)}" target="_blank" rel="noopener">Stage</a>
          </td></tr>`).join("")}
        </tbody></table></div>`;
    if (state.creating) {
      const name = document.getElementById("nName"), slug = document.getElementById("nSlug");
      name.addEventListener("input", () => { if (!slug.dataset.touched) slug.value = slugify(name.value); });
      slug.addEventListener("input", () => { slug.dataset.touched = "1"; });
      document.getElementById("newForm").addEventListener("submit", createSession);
    }
  }

  function newForm() {
    const cur = sel();
    return `<form id="newForm" class="card soft" style="margin:0 18px 16px;display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">
      <div class="field"><label for="nName">Name</label><input class="input" id="nName" required maxlength="80" placeholder="Batch 2"></div>
      <div class="field"><label for="nSlug">Short id (in links)</label><input class="input" id="nSlug" required maxlength="40" pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" placeholder="batch-2"></div>
      <div class="field"><label for="nCopy">Copy rules from</label><select class="input" id="nCopy"><option value="">Defaults</option>
        ${state.sessions.map((s) => `<option value="${esc(s.slug)}" ${cur && s.slug === cur.slug ? "selected" : ""}>${esc(s.settings.event_name)}</option>`).join("")}</select></div>
      <div style="display:flex;align-items:end;gap:10px"><button class="btn" type="submit">Create</button>
        <label style="display:flex;gap:6px;align-items:center;font-size:14px"><input type="checkbox" id="nActive"> make it active</label></div>
      <div id="nErr" style="color:var(--red);grid-column:1/-1;font-size:14px"></div></form>`;
  }

  async function createSession(ev) {
    ev.preventDefault();
    const slug = document.getElementById("nSlug").value.trim();
    try {
      await call("/api/admin/sessions", { action: "create", slug, copyFrom: document.getElementById("nCopy").value,
        settings: { event_name: document.getElementById("nName").value } });
      if (document.getElementById("nActive").checked) await call("/api/admin/sessions", { action: "activate", slug });
    } catch (e) { document.getElementById("nErr").textContent = e.message; return; }
    state.creating = false;
    state.sel = slug;
    await refreshAll(`Created ${slug}. Set its times and rules below.`);
  }

  async function refreshAll(msg) {
    await loadSessions();
    renderSessions(msg);
    await renderEndEvent();
    renderSettings();
    await loadPeople();
  }

  // ---------- final results (snapshot taken when the event ended) ----------
  async function finalResultsHtml(w) {
    if (!w.standings && !w.badgeWinners) return "";
    let defs = {};
    try { defs = (await HC.badges()).by; } catch (_) {}
    const head = (t) => `<summary class="h3" style="cursor:pointer;margin:22px 0 10px">${t}</summary>`;

    const table = (rows, label) => !rows?.length ? "" : `
      <details open>${head(`${label} · overall (${rows.length})`)}
        <div style="overflow-x:auto"><table class="lb"><thead><tr><th>#</th><th>Name</th><th class="r">Tool calls</th>
          <th class="r hide-sm">Tokens</th><th class="r hide-sm">Commits</th><th>Badges</th></tr></thead><tbody>
          ${rows.map((r) => `<tr><td>${r.rank}</td>
            <td><b>${esc(r.name)}</b><div class="muted" style="font-size:12px">${esc(r.subtitle || "")}</div></td>
            <td class="r">${fmt(r.calls)}</td><td class="r hide-sm">${fmt(r.tokens)}</td><td class="r hide-sm">${fmt(r.commits)}</td>
            <td>${HC.badgeRow(r.badges, r.crowns, 10, 24)}</td></tr>`).join("")}
        </tbody></table></div>
      </details>`;

    const badgeList = (list, label) => {
      const earned = (list || []).filter((b) => b.earners.length);
      if (!earned.length) return "";
      return `<details open>${head(`${label} · who got what (${earned.length} awarded)`)}
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;align-items:start">
          ${earned.map((b) => {
            const def = defs[b.id];
            const top = Math.max(...b.earners.map((e) => e.tier));
            return `<div class="card soft" style="padding:14px">
              <div style="display:flex;gap:10px;align-items:center;margin-bottom:8px">
                ${def ? HC.badge(def, top, 40) : ""}
                <div><b>${esc(b.name)}</b><div class="muted" style="font-size:12px">${esc(b.rule || "")}</div></div>
              </div>
              ${b.earners.map((e) => `<div style="display:flex;justify-content:space-between;gap:8px;font-size:13px;padding:3px 0">
                <span>${e.crown ? "👑 " : ""}${esc(e.name)}<span class="muted"> · ${esc(e.subtitle || "")}</span></span>
                <span class="muted">${esc(HC.TIER[e.tier] || "")}</span></div>`).join("")}
            </div>`;
          }).join("")}
        </div>
      </details>`;
    };

    const st = w.standings || {}, bw = w.badgeWinners || {};
    return `<div style="margin-top:8px">
      ${table(st.players, "Builders")}${table(st.teams, "Teams")}
      ${badgeList(bw.personal, "Builder badges")}${badgeList(bw.team, "Team badges")}
    </div>`;
  }

  // ---------- end event & announce winners ----------
  async function renderEndEvent() {
    const box = document.getElementById("endEvent");
    if (!box) return;
    let cfg = null;
    try { cfg = await HC.api(q("/api/config")); } catch (_) { return; }
    const w = cfg && cfg.winners;
    if (w && w.ended && (w.winners?.length || w.teamWinners?.length || w.playerWinners?.length)) {
      const medals = { 1: "🏆 1st Place", 2: "🥈 2nd Place", 3: "🥉 3rd Place" };
      const podHtml = (list, label) => {
        if (!list || !list.length) return "";
        const order = list.length >= 3 ? [1, 0, 2] : [0];
        return `<div style="margin-top:12px">
          ${label ? `<div class="muted" style="font-size:12px;font-weight:700;text-transform:uppercase;margin-bottom:8px;text-align:center">${esc(label)}</div>` : ""}
          <div class="podium" style="margin-bottom:0;justify-content:center">
            ${order.filter((idx) => list[idx]).map((idx) => {
              const x = list[idx];
              return `<div class="pod p${x.place}" style="min-width:170px">
                <div class="place">${medals[x.place] || `#${x.place}`}</div>
                <div style="margin-top:10px">${avatar(x.name, x.place === 1 ? 56 : 44)}</div>
                <div class="who" style="font-size:16px">${esc(x.name)}</div>
                <div class="team">${esc(x.subtitle || "")}</div>
                <div class="big" style="font-size:22px">${fmt(x.calls)}</div>
                <div class="muted" style="font-size:12px">calls · ${fmt(x.commits)} commits</div>
              </div>`;
            }).join("")}
          </div>
        </div>`;
      };
      const entLabel = w.entity === "both" ? "Teams + Individuals" : w.entity;
      box.innerHTML = `
        <div class="card io-border" style="margin-bottom:24px">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px">
            <div>
              <div class="h3">🏁 Event Ended · ${w.topN === 1 ? "Top 1 Winner" : "Top 3 Podium"} (${esc(entLabel)})</div>
              <div class="muted" style="font-size:13px">Uploads are frozen and the Official Winners Podium is displayed as a popup on every screen.</div>
            </div>
            <button class="btn light" type="button" data-a="revert-event">↩️ Revert &amp; Reopen Event</button>
          </div>
          ${w.entity === "both"
            ? podHtml(w.teamWinners, "Top Teams") + podHtml(w.playerWinners, "Top Individual Builders")
            : podHtml(w.winners, "")}
          ${await finalResultsHtml(w)}
        </div>`;
      return;
    }

    box.innerHTML = `
      <div class="card" style="margin-bottom:24px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px">
          <div>
            <div class="h3">🏁 End Event &amp; Announce Winners</div>
            <div class="muted" style="font-size:13px">Freezes the leaderboard, locks in the official winners, and pops up the Winners Podium on everyone's screen. (Can be reverted anytime.)</div>
          </div>
        </div>
        <div style="display:flex;gap:14px;align-items:end;flex-wrap:wrap">
          <div class="field" style="min-width:200px">
            <label for="winTopN">Podium Format</label>
            <select class="input" id="winTopN">
              <option value="3">🏆 Top 3 Podium (1st, 2nd, 3rd)</option>
              <option value="1">🥇 Top 1 Winner (1st Only)</option>
            </select>
          </div>
          <div class="field" style="min-width:220px">
            <label for="winEntity">Award To</label>
            <select class="input" id="winEntity">
              <option value="teams">Teams</option>
              <option value="players">Builders (Individuals)</option>
              <option value="both">Individual + Team (Both)</option>
            </select>
          </div>
          <button class="btn" type="button" data-a="end-event" style="background:#b45309;border-color:#b45309">🏁 End Event &amp; Announce Winners</button>
        </div>
      </div>`;
  }

  // ---------- settings of the selected session ----------
  function renderSettings(msg, isError) {
    const s = sel();
    if (!s) return;
    const c = s.settings, off = c.utc_offset_min;
    const offsets = OFFSETS.some(([o]) => o === off) ? OFFSETS : [...OFFSETS, [off, `UTC${off < 0 ? "−" : "+"}${Math.abs(off) / 60}`]];
    const agents = [...new Set([...state.agents, ...c.agents])];
    document.getElementById("settings").innerHTML = `
      <form id="setForm" class="card" style="margin-bottom:24px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px">
          <div class="h3">Settings · ${esc(c.event_name)}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn light" type="button" data-a="copy-join">📋 Copy Join Link</button>
            <a class="btn light" href="/api/admin/export-csv?s=${encodeURIComponent(s.slug)}" data-a="export-csv">📊 Export CSV</a>
            <a class="btn light" href="/api/admin/export?s=${encodeURIComponent(s.slug)}" data-a="export">Export DB</a>
            <button class="btn light danger" type="button" data-a="clear-all" title="Wipe all participants, teams, sessions, and badges for a fresh start">💥 Clean all data</button>
            <button class="btn light danger" type="button" data-a="clear-feed">Clear live feed</button>
            <button class="btn light${c.archived ? "" : " danger"}" type="button" data-a="${c.archived ? "reopen" : "archive"}">${c.archived ? "Reopen" : "Archive"}</button>
            ${s.active ? "" : `<button class="btn light danger" type="button" data-a="delete">Delete session</button>`}</div></div>
        ${c.archived ? `<p class="card soft" style="margin:0 0 14px">Archived: the board stays viewable, but nobody can register or upload. Reopen to change that.</p>` : ""}
        <div style="display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))">
          <div class="field"><label for="sName">Name</label><input class="input" id="sName" maxlength="80" required value="${esc(c.event_name)}"></div>
          <div class="field"><label for="sTz">Venue time zone</label><select class="input" id="sTz">
            ${offsets.map(([o, l]) => `<option value="${o}" ${o === off ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>
          <div class="field"><label for="sStart">Starts (venue time)</label><input class="input" id="sStart" type="datetime-local" value="${toInput(c.event_start, off)}">
            <span class="faint" style="font-size:12px">Agent sessions started earlier don't count.</span></div>
          <div class="field"><label for="sEnd">Ends (venue time)</label><input class="input" id="sEnd" type="datetime-local" value="${toInput(c.event_end, off)}">
            <span class="faint" style="font-size:12px">Drives the countdowns.</span></div>
          <div class="field"><label for="sCode">Registration code</label><input class="input" id="sCode" maxlength="64" autocomplete="off" value="${esc(c.registration_code)}" placeholder="none: anyone can join"></div>
          <div class="field"><label for="sRank">Rank builders and teams by</label><select class="input" id="sRank">
            ${state.rankable.map((k) => `<option value="${k}" ${k === c.rank_by ? "selected" : ""}>${esc(HC.METRICS[k] || k)}</option>`).join("")}</select></div>
        </div>
        <div style="display:flex;gap:24px;flex-wrap:wrap;margin:18px 0">
          <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="sReg" ${c.registration_open ? "checked" : ""}> Registration open</label>
          <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="sUp" ${c.uploads_open ? "checked" : ""}> Uploads open <span class="faint" style="font-size:12px">(untick to freeze the board)</span></label></div>
        <div class="field" style="margin-bottom:18px"><label>Agents that count <span class="faint" style="font-weight:400">(none ticked = all)</span></label>
          <div style="display:flex;gap:8px 18px;flex-wrap:wrap;margin-top:6px">${agents.map((a) =>
            `<label style="display:flex;gap:6px;align-items:center;font-size:14px"><input type="checkbox" name="agent" value="${esc(a)}" ${c.agents.includes(a) ? "checked" : ""}> ${esc(a)}</label>`).join("")}</div></div>
        <div class="field"><label for="sAnn">Announcement <span class="faint" style="font-weight:400">(shown on every page and the stage; empty = none)</span></label>
          <textarea class="input" id="sAnn" maxlength="280" rows="2" style="height:auto;padding-top:10px">${esc(c.announcement)}</textarea></div>
        <div style="display:flex;gap:12px;align-items:center;margin-top:16px;flex-wrap:wrap">
          <button class="btn" type="submit">Save settings</button>
          <span style="font-size:14px;color:${isError ? "var(--red)" : "var(--muted)"}">${esc(msg || "")}</span></div>
      </form>`;
    document.getElementById("setForm").addEventListener("submit", saveSettings);
  }

  async function saveSettings(ev) {
    ev.preventDefault();
    const off = Number(document.getElementById("sTz").value);
    const settings = {
      event_name: document.getElementById("sName").value,
      utc_offset_min: off,
      event_start: fromInput(document.getElementById("sStart").value, off),
      event_end: fromInput(document.getElementById("sEnd").value, off),
      registration_code: document.getElementById("sCode").value,
      rank_by: document.getElementById("sRank").value,
      registration_open: document.getElementById("sReg").checked,
      uploads_open: document.getElementById("sUp").checked,
      agents: [...document.querySelectorAll('input[name="agent"]:checked')].map((x) => x.value),
      announcement: document.getElementById("sAnn").value,
    };
    try {
      await call("/api/admin/sessions", { action: "update", slug: state.sel, settings });
    } catch (e) { return renderSettings(e.message, true); }
    await loadSessions();
    renderSessions();
    renderSettings("Saved. Pages pick it up within a few seconds.");
  }

  // ---------- people of the selected session ----------
  async function loadPeople() {
    try { state.rows = (await call(q("/api/admin/participants"))).participants; } catch (e) { state.rows = []; }
    if (state.open) {
      try { state.open.sessions = (await call(q("/api/admin/participant?name=" + encodeURIComponent(state.open.name)))).sessions; }
      catch (_) { state.open = null; }
    }
    renderPeople();
  }

  function syncCell(p) {
    const s = p.sync;
    if (!s) return `<span class="faint">never connected</span>`;
    const skipped = Object.entries(s.skipped || {}).map(([why, n]) => `${n} ${why}`).join(", ");
    return `<div>${HC.ago(p.sync_ts)} · ${fmt(s.eligible)}/${fmt(s.found)} count</div>
      ${s.error ? `<div style="color:var(--red);font-size:12px">${esc(s.error)}</div>` : ""}
      ${skipped ? `<div class="faint" style="font-size:12px">${esc(skipped)}</div>` : ""}`;
  }

  function personRow(p) {
    if (state.editing === p.name) {
      return `<tr><td colspan="5"><form class="editForm" data-v="${esc(p.name)}" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <input class="input" name="newName" maxlength="40" value="${esc(p.name)}" style="flex:1;min-width:160px" aria-label="Name">
        <input class="input" name="newTeam" maxlength="40" value="${esc(p.team)}" list="teamList" style="flex:1;min-width:160px" aria-label="Team">
        <button class="btn" type="submit">Save</button><button class="btn light" type="button" data-a="cancel-edit">Cancel</button>
        <span class="err" style="color:var(--red);font-size:14px"></span></form></td></tr>`;
    }
    const open = state.open && state.open.name === p.name;
    return `<tr style="${p.hidden ? "opacity:.55" : ""}">
      <td><div class="who">${avatar(p.name, 32)}<div style="min-width:0"><div class="n">${esc(p.name)} ${p.hidden ? '<span class="tier-pill">hidden</span>' : ""} ${p.flagged ? '<span class="tier-pill" style="background:#fee2e2;color:#991b1b;font-weight:700">🚩 Flagged</span>' : ""}</div>
        <div class="t">📅 Started: ${new Date(p.created_at * 1000).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} (${HC.ago(p.created_at)})</div></div></div></td>
      <td class="r metric"><button class="btn light" data-a="sessions" data-v="${esc(p.name)}" title="Show uploaded sessions">${fmt(p.sessions)} ${open ? "▴" : "▾"}</button></td>
      <td class="hide-sm faint">${p.last_upload ? HC.ago(p.last_upload) : "–"}</td>
      <td class="hide-sm" style="font-size:13px">${syncCell(p)}</td>
      <td class="r" style="white-space:nowrap">
        <button class="btn light" data-a="edit" data-v="${esc(p.name)}">Edit</button>
        <button class="btn light" data-a="${p.flagged ? "unflag" : "flag"}" data-v="${esc(p.name)}">${p.flagged ? "Unflag" : "🚩 Flag"}</button>
        <button class="btn light" data-a="${p.hidden ? "unhide" : "hide"}" data-v="${esc(p.name)}" title="${p.hidden ? "Show on the board again" : "Keep their data but take them off the board"}">${p.hidden ? "Unhide" : "Hide"}</button>
        <button class="btn light" data-a="reissue" data-v="${esc(p.name)}">New token</button>
        <button class="btn light danger" data-a="remove" data-v="${esc(p.name)}">Remove</button></td></tr>
    ${open ? `<tr><td colspan="5" style="background:var(--bg-soft)">${state.open.sessions.length ? `<table style="width:100%;font-size:13px">
      ${state.open.sessions.map((s) => `<tr><td>${esc(s.agentTool)}</td><td class="faint">started ${s.startedAt ? esc(new Date(s.startedAt).toLocaleString()) : "–"}</td>
        <td class="r">${fmt(s.calls)} calls</td><td class="r">${fmt(s.tokens)} tokens</td>
        <td class="r"><button class="btn light danger" data-a="remove-session" data-v="${esc(p.name)}" data-sid="${esc(s.id)}">Remove session</button></td></tr>`).join("")}
      </table>` : '<span class="faint">No sessions uploaded.</span>'}</td></tr>` : ""}`;
  }

  function renderPeople(msg) {
    const s = sel();
    const needle = state.q.toLowerCase();
    const teams = new Map();
    for (const p of state.rows) {
      if (state.onlyFlagged && !p.flagged) continue;
      if (needle && !p.name.toLowerCase().includes(needle) && !p.team.toLowerCase().includes(needle)) continue;
      const k = p.team.toLowerCase();  // the server matches teams case-insensitively
      if (!teams.has(k)) teams.set(k, []);
      teams.get(k).push(p);
    }
    const flaggedCount = state.rows.filter((p) => p.flagged).length;
    const total = new Set(state.rows.map((p) => p.team.toLowerCase())).size;
    const el = document.getElementById("people");
    const hadFocus = document.activeElement && document.activeElement.id === "q";
    el.innerHTML = `
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:18px">
        <div class="h3" style="margin-right:auto">People · ${esc(s ? s.settings.event_name : "")}</div>
        <input class="input" id="q" placeholder="Search name or team" value="${esc(state.q)}" style="flex:1;max-width:320px;min-width:200px">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer">
          <input type="checkbox" id="fFlagged" ${state.onlyFlagged ? "checked" : ""}> 🚩 Flagged only (${flaggedCount})
        </label>
        <span class="muted">${fmt(state.rows.length)} builders · ${fmt(total)} teams</span>
        <button class="btn light" data-a="refresh">Refresh</button></div>
      <datalist id="teamList">${[...new Set(state.rows.map((p) => p.team))].map((t) => `<option value="${esc(t)}">`).join("")}</datalist>
      ${msg || state.msg ? `<p class="muted" style="margin:0 0 14px">${esc(msg || state.msg)}</p>` : ""}
      ${state.issued ? issuedBox(state.issued) : ""}
      ${teams.size ? [...teams.values()].map((members) => { const team = members[0].team; return `
        <div class="card table-card" style="margin-bottom:16px">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:14px 18px;flex-wrap:wrap">
            <div class="h3">${esc(team)} <span class="muted" style="font-weight:500">· ${members.length} member${members.length === 1 ? "" : "s"}</span></div>
            <button class="btn light danger" data-a="team" data-v="${esc(team)}">Remove team</button></div>
          <table class="lb"><thead><tr><th>Builder</th><th class="r">Sessions</th><th class="hide-sm">Last upload</th>
            <th class="hide-sm">Sync window</th><th class="r"></th></tr></thead><tbody>
          ${members.map(personRow).join("")}</tbody></table></div>`; }).join("")
        : `<div class="card empty">${state.rows.length ? "No matches." : "Nobody has registered for this session yet."}</div>`}`;
    state.msg = "";
    const qEl = document.getElementById("q");
    qEl.addEventListener("input", () => { state.q = qEl.value; renderPeople(); });
    document.getElementById("fFlagged").addEventListener("change", (ev) => { state.onlyFlagged = ev.target.checked; renderPeople(); });
    if (hadFocus) { qEl.focus(); qEl.setSelectionRange(qEl.value.length, qEl.value.length); }
    el.querySelectorAll(".editForm").forEach((f) => f.addEventListener("submit", saveEdit));
    HC.copyButtons(el);
  }

  function issuedBox({ name, token: t }) {
    const o = location.origin, e = encodeURIComponent(t);
    const ps = `irm "${o}/join.ps1?token=${e}" | iex`, sh = `curl -fsSL "${o}/join.sh?token=${e}" | bash`;
    return `<div class="card soft" style="margin-bottom:16px">
      <h3 class="h3">New token for ${esc(name)}</h3>
      <p class="muted" style="margin:4px 0 10px">Their old token stops working now. Send them one of these; it's shown only once.
        They run it inside their project folder.</p>
      <div class="cmd" id="isPs" data-text="${esc(ps)}">${esc(ps)}<button class="copy" data-copy="isPs">Copy</button></div>
      <div class="cmd" id="isSh" data-text="${esc(sh)}" style="margin-top:8px">${esc(sh)}<button class="copy" data-copy="isSh">Copy</button></div></div>`;
  }

  async function saveEdit(ev) {
    ev.preventDefault();
    const f = ev.target;
    try {
      const r = await call(q("/api/admin/update-participant"), { name: f.dataset.v, newName: f.newName.value, newTeam: f.newTeam.value });
      state.editing = null;
      state.msg = `Saved ${r.name} (${r.team}).`;
    } catch (e) { f.querySelector(".err").textContent = e.message; return; }
    await refreshAll();
  }

  // ---------- buttons ----------
  // Destructive ones need a second click within 4 s (no browser confirm dialogs).
  const DANGER = { remove: "Click to confirm", team: null, "remove-session": "Click to confirm", "clear-feed": "Clear it?",
    "clear-all": "Wipe ALL data?", archive: "Archive it?", delete: "Delete everything?" };
  function armed(btn) {
    if (btn.dataset.armed) return true;
    const original = btn.textContent;
    btn.dataset.armed = "1";
    if (btn.dataset.a === "team") {
      const n = state.rows.filter((p) => p.team.toLowerCase() === btn.dataset.v.toLowerCase()).length;
      btn.textContent = `Remove ${n} builder${n === 1 ? "" : "s"}?`;
    } else btn.textContent = DANGER[btn.dataset.a];
    setTimeout(() => { if (btn.isConnected) { delete btn.dataset.armed; btn.textContent = original; } }, 4000);
    return false;
  }

  async function onClick(ev) {
    const btn = ev.target.closest("[data-a]");
    if (!btn) return;
    const a = btn.dataset.a, v = btn.dataset.v;
    if (a === "export") return exportFile(ev, "/api/admin/export", `hackathon-${state.sel}.db`);
    if (a === "export-csv") return exportFile(ev, "/api/admin/export-csv", `hackathon-${state.sel}-scores.csv`);
    if (a === "copy-join") {
      const s = sel();
      const joinUrl = `${location.origin}/join?s=${encodeURIComponent(state.sel)}`;
      const code = s && s.settings.registration_code ? ` (Code: ${s.settings.registration_code})` : "";
      try {
        await navigator.clipboard.writeText(joinUrl + code);
        btn.textContent = "Copied!";
        setTimeout(() => { if (btn.isConnected) btn.textContent = "📋 Copy Join Link"; }, 1600);
      } catch (_) { renderSettings(`Join link: ${joinUrl}${code}`); }
      return;
    }
    if (a in DANGER && !armed(btn)) return;
    if (btn.tagName === "BUTTON") btn.disabled = true;
    state.issued = a === "reissue" ? state.issued : null;
    try {
      switch (a) {
        case "new": state.creating = !state.creating; return renderSessions();
        case "pick": state.sel = v; state.open = null; state.editing = null; state.q = ""; return refreshAll();
        case "activate": await call("/api/admin/sessions", { action: "activate", slug: v }); return refreshAll(`${v} is now the active session.`);
        case "archive": case "reopen":
          await call("/api/admin/sessions", { action: a, slug: state.sel });
          return refreshAll(a === "archive" ? "Archived." : "Reopened.");
        case "delete":
          await call("/api/admin/sessions", { action: "delete", slug: state.sel });
          state.sel = ""; return refreshAll("Session deleted.");
        case "clear-feed": await call(q("/api/admin/clear-feed"), {}); return renderSettings("Live feed cleared.");
        case "clear-all":
          await call(q("/api/admin/clear-all"), {});
          return refreshAll("All leaderboard data has been reset.");
        case "end-event": {
          const topN = Number(document.getElementById("winTopN")?.value || 3);
          const entity = document.getElementById("winEntity")?.value || "teams";
          await call(q("/api/admin/end-event"), { topN, entity });
          return refreshAll(`Event ended! Top ${topN} ${entity} announced on the Podium.`);
        }
        case "revert-event":
          await call(q("/api/admin/revert-event"), {});
          return refreshAll("Event reverted and reopened! Uploads are live again.");
        case "flag":
          await call(q("/api/admin/flag"), { name: v, flagged: true });
          return refreshAll(`🚩 Flagged ${v} for review.`);
        case "unflag":
          await call(q("/api/admin/flag"), { name: v, flagged: false });
          return refreshAll(`Unflagged ${v}.`);
        case "refresh": return refreshAll();
        case "edit": state.editing = v; return renderPeople();
        case "cancel-edit": state.editing = null; return renderPeople();
        case "sessions":
          state.open = state.open && state.open.name === v ? null : { name: v, sessions: [] };
          return loadPeople();
        case "hide": case "unhide":
          await call(q("/api/admin/update-participant"), { name: v, hidden: a === "hide" });
          state.msg = a === "hide" ? `${v} is hidden from the board (data kept).` : `${v} is back on the board.`;
          return refreshAll();
        case "reissue":
          state.issued = await call(q("/api/admin/reissue"), { name: v });
          return renderPeople();
        case "remove":
          await call(q("/api/admin/remove"), { name: v });
          if (state.open && state.open.name === v) state.open = null;
          state.msg = `Removed ${v}.`; return refreshAll();
        case "team": {
          const r = await call(q("/api/admin/remove-team"), { team: v });
          state.msg = `Removed team ${v}: ${r.removed.join(", ")}.`; return refreshAll();
        }
        case "remove-session":
          await call(q("/api/admin/remove-session"), { name: v, sessionId: btn.dataset.sid });
          state.msg = `Removed one of ${v}'s sessions.`; return refreshAll();
      }
    } catch (e) {
      state.msg = e.message;
      renderPeople();
    } finally {
      if (btn.isConnected) btn.disabled = false;
    }
  }

  async function exportFile(ev, endpoint, filename) {
    ev.preventDefault();  // the token goes in a header, so fetch it rather than follow the link
    try {
      const res = await fetch(q(endpoint), { headers: { "X-Admin-Token": token } });
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const url = URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: filename });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) { renderSettings(e.message, true); }
  }

  start();
})();
