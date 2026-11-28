/* Catalyst — shared helpers: formatting, nav, avatars, SVG badges, live events. */
"use strict";

const HC = (() => {
  const COLORS = ["#1a73e8", "#ea4335", "#f9ab00", "#1e8e3e", "#a142f4", "#12b5cb", "#e8710a", "#d01884"];
  const TIER = ["Locked", "Bronze", "Silver", "Gold"];
  const METRICS = {
    calls: "Tool calls", tokens: "Tokens", outputTokens: "Output tokens", commits: "Commits", prs: "PRs",
    mcpCalls: "MCP calls", errors: "Errors", verifyRuns: "Test runs", linesChanged: "Lines changed",
    webCalls: "Web lookups", subagents: "Subagents", costUsd: "Spend", activeMs: "Active time", sessions: "Sessions",
    badges: "Badges",
  };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function fmt(n) {
    n = Number(n) || 0;
    const a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 0 : 1).replace(/\.0$/, "") + "B";
    if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
    if (a >= 1e4) return (n / 1e3).toFixed(0) + "K";
    return Math.round(n).toLocaleString("en-US");
  }
  const fmtFull = (n) => Math.round(Number(n) || 0).toLocaleString("en-US");
  const fmtUsd = (n) => "$" + (Number(n) || 0).toFixed((Number(n) || 0) >= 100 ? 0 : 2);
  const fmtHours = (ms) => { const h = (Number(ms) || 0) / 3.6e6; return h >= 10 ? Math.round(h) + "h" : h.toFixed(1) + "h"; };
  const fmtPct = (r) => Math.round((Number(r) || 0) * 100) + "%";

  function fmtMetric(key, v) {
    if (key === "costUsd") return fmtUsd(v);
    if (key === "activeMs") return fmtHours(v);
    return fmt(v);
  }

  function fmtBadgeValue(def, v) {
    if (v === null || v === undefined) return "—";
    if (def.fmt === "pct") return fmtPct(v);
    if (def.fmt === "ratio") return (Math.round(v * 10) / 10).toString();
    if (def.fmt === "hours") return (Math.round(v * 10) / 10) + "h";
    return fmt(v);
  }

  function ago(ts) {
    if (!ts) return "never";
    const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (s < 45) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }

  // ---------- sessions: ?s=<slug> picks one; without it the server uses the active session ----------
  const S = new URLSearchParams(location.search).get("s") || "";
  function withS(href) {
    if (!S || !href.startsWith("/") || /[?&]s=/.test(href)) return href;
    return href + (href.includes("?") ? "&" : "?") + "s=" + encodeURIComponent(S);
  }
  // Every internal link keeps the session, including ones the pages build themselves.
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest('a[href^="/"]');
    if (a && S) a.setAttribute("href", withS(a.getAttribute("href")));
  }, true);

  async function api(path, opts) {
    const res = await fetch(path.startsWith("/api/") && !path.startsWith("/api/admin/") ? withS(path) : path, opts);
    let body = null;
    try { body = await res.json(); } catch (_) { /* non-JSON */ }
    if (!res.ok) throw new Error((body && body.error) || `Request failed (${res.status})`);
    return body;
  }

  function hash(s) { let h = 0; for (const c of String(s)) h = (h * 31 + c.codePointAt(0)) >>> 0; return h; }
  const colorFor = (s) => COLORS[hash(String(s).toLowerCase()) % COLORS.length];
  const memberColor = (i) => COLORS[i % COLORS.length]; // distinct within one team's split bars

  function initials(name) {
    const parts = String(name).trim().split(/\s+/).filter(Boolean);
    const chars = parts.length > 1 ? [parts[0], parts[parts.length - 1]].map((p) => [...p][0]) : [...(parts[0] || "?")].slice(0, 2);
    return chars.join("").toUpperCase();
  }

  function avatar(name, size = 36, ring) {
    const style = `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.38)}px;background:${colorFor(name)};${ring ? `--ring:${ring}` : ""}`;
    return `<span class="avatar${ring ? " ring" : ""}" style="${style}" aria-hidden="true">${esc(initials(name))}</span>`;
  }

  const link = {
    player: (n) => withS("/player?name=" + encodeURIComponent(n)),
    team: (n) => withS("/team?name=" + encodeURIComponent(n)),
  };

  // ---------- SVG badges ----------
  const ICONS = {
    coin: '<circle cx="12" cy="12" r="9"/><path d="M14.8 9.4c-.5-.9-1.5-1.4-2.7-1.4-1.5 0-2.6.8-2.6 2s1.1 1.6 2.6 2 2.7.8 2.7 2.1-1.2 1.9-2.7 1.9c-1.2 0-2.3-.5-2.8-1.4M12 6.5V8M12 16v1.5"/>',
    wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z"/>',
    rocket: '<path d="M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2.1-.1-2.9a2.2 2.2 0 0 0-2.9-.1z"/><path d="M12 15l-3-3a22 22 0 0 1 2-4A12.9 12.9 0 0 1 22 2c0 2.7-.8 7.5-6 11a22.4 22.4 0 0 1-4 2z"/><path d="M9 12H4s.6-3 2-4c1.6-1.1 5 0 5 0M12 15v5s3-.6 4-2c1.1-1.6 0-5 0-5"/>',
    flask: '<path d="M9 3h6M10 3v6l-5.5 9.5A1.5 1.5 0 0 0 5.8 21h12.4a1.5 1.5 0 0 0 1.3-2.5L14 9V3M7.5 15h9"/>',
    sparkles: '<path d="M12 3l1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7-4.7-1.8 4.7-1.8zM19 15l.8 2.2 2.2.8-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5"/>',
    plug: '<path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/>',
    bot: '<path d="M12 8V4M9 4h6"/><rect x="3" y="8" width="18" height="12" rx="3"/><path d="M9 13v2M15 13v2"/>',
    database: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    leaf: '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.5 19 2c1 2 2 4.2 2 8 0 5.5-4.8 10-10 10z"/><path d="M2 21c0-3 1.9-5.4 5.1-6"/>',
    layers: '<path d="M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    pen: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    shuffle: '<path d="M16 3h5v5M4 20 21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/>',
    users: '<circle cx="9" cy="7" r="4"/><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    scale: '<path d="M12 3v18M7 21h10M3 7h18M6 7l-3 7a3 3 0 0 0 6 0zM18 7l-3 7a3 3 0 0 0 6 0z"/>',
    crown: '<path d="M2 18h20M3.5 18 2 7l5.5 4L12 4l4.5 7L22 7l-1.5 11z"/>',
    hive: '<path d="M12 2l4 2.3v4.6L12 11 8 8.9V4.3zM7 11l4 2.3v4.6L7 20l-4-2.1v-4.6zM17 11l4 2.3v4.6L17 20l-4-2.1v-4.6z"/>',
    trophy: '<path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0zM17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>',
    flag: '<path d="M4 22V4M4 4h13l-2 4 2 4H4"/>',
    up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>',
  };
  const RIM = [["#f1f2f5", "#d9dce2"], ["#f3c49a", "#a85a22"], ["#eef1f5", "#8c96a4"], ["#fae39a", "#c08c12"]];
  let uid = 0;

  function icon(name, size = 18, color = "currentColor", width = 2) {
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.trophy}</svg>`;
  }

  function badge(def, tier = 0, size = 64, opts = {}) {
    const id = "hcb" + (++uid);
    const [a, b] = RIM[tier] || RIM[0];
    const col = tier ? def.color : "#a6abb3";
    const title = `${def.name}${tier ? " · " + TIER[tier] : " · locked"}`;
    const crown = opts.crown
      ? `<g transform="translate(42 0)"><circle cx="11" cy="11" r="11" fill="#fff"/><circle cx="11" cy="11" r="9.5" fill="#fbbc04"/><g transform="translate(4.5 4.6) scale(.54)" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">${ICONS.crown}</g></g>`
      : "";
    return `<svg class="svg-badge${opts.pop ? " pop" : ""}" width="${size}" height="${size}" viewBox="0 0 64 64" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title>
      <defs><linearGradient id="${id}r" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${a}"/><stop offset="1" stop-color="${b}"/></linearGradient>
      <radialGradient id="${id}f" cx=".35" cy=".3" r=".9"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="${tier ? col : "#ffffff"}" stop-opacity="${tier ? 0.16 : 0}"/></radialGradient></defs>
      <circle cx="32" cy="32" r="30" fill="url(#${id}r)"/>
      <circle cx="32" cy="32" r="24.5" fill="#fff"/><circle cx="32" cy="32" r="24.5" fill="url(#${id}f)"/>
      ${tier === 3 ? `<circle cx="32" cy="32" r="27.3" fill="none" stroke="#fff" stroke-opacity=".55" stroke-width="1" stroke-dasharray="2 3"/>` : ""}
      <g transform="translate(20 20)" fill="none" stroke="${col}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[def.icon] || ICONS.trophy}</g>
      ${crown}</svg>`;
  }

  let badgeDefs = null;
  async function badges() {
    if (!badgeDefs) {
      const d = await api("/api/badges");
      badgeDefs = { list: d.personal, team: d.team, by: {} };
      for (const b of [...d.personal, ...d.team]) badgeDefs.by[b.id] = b;
    }
    return badgeDefs;
  }

  function badgeRow(earned, crowns = [], max = 6, size = 26) {
    const defs = badgeDefs ? badgeDefs.by : {};
    const ids = Object.keys(earned || {}).filter((id) => defs[id])
      .sort((x, y) => (crowns.includes(y) - crowns.includes(x)) || earned[y] - earned[x]);
    if (!ids.length) return '<span class="faint">—</span>';
    const shown = ids.slice(0, max).map((id) => badge(defs[id], earned[id], size, { crown: crowns.includes(id) })).join("");
    return `<span class="badge-row">${shown}${ids.length > max ? `<span class="more">+${ids.length - max}</span>` : ""}</span>`;
  }

  // ---------- page chrome ----------
  let config = null;
  function renderWinnersPopup(w) {
    let modal = document.getElementById("hcWinnersModal");
    if (!w || !w.ended || (!w.winners?.length && !w.teamWinners?.length && !w.playerWinners?.length)) {
      if (modal) modal.remove();
      return;
    }
    const ackKey = "hc_win_ack";
    const stamp = String(w.endedAt || 1);
    try { if (sessionStorage.getItem(ackKey) === stamp) return; } catch (_) {}
    if (modal && modal.dataset.stamp === stamp) return;
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "hcWinnersModal";
      modal.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(15,23,42,0.72);backdrop-filter:blur(4px);display:flex;align-items:center;justify-content:center;padding:20px;";
      document.body.appendChild(modal);
    }
    modal.dataset.stamp = stamp;
    const medals = { 1: "🏆 1st Place", 2: "🥈 2nd Place", 3: "🥉 3rd Place" };
    const rings = { 1: "#c9971c", 2: "#8d97a5", 3: "#b0652a" };
    const podGroup = (list, title, kind) => {
      if (!list || !list.length) return "";
      const order = list.length >= 3 ? [1, 0, 2] : [0];
      return `<div style="margin-top:16px">
        ${title ? `<div style="font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#64748b;margin-bottom:10px">${esc(title)}</div>` : ""}
        <div class="podium" style="margin-bottom:0;justify-content:center;gap:12px">
          ${order.filter((idx) => list[idx]).map((idx) => {
            const x = list[idx];
            const href = kind === "players" ? link.player(x.name) : link.team(x.name);
            return `<div class="pod p${x.place}" onclick="location.href='${href}'" style="cursor:pointer;min-width:165px;padding:16px">
              <div class="place">${medals[x.place] || `#${x.place}`}</div>
              <div style="margin-top:10px">${avatar(x.name, x.place === 1 ? 64 : 48, rings[x.place] || "#c9971c")}</div>
              <div class="who" style="font-size:${x.place === 1 ? "19px" : "16px"}">${esc(x.name)}</div>
              <div class="team">${esc(x.subtitle || "")}</div>
              <div class="big" style="font-size:22px">${fmt(x.calls)}</div>
              <div class="muted" style="font-size:12px">tool calls · ${fmt(x.commits)} commits</div>
            </div>`;
          }).join("")}
        </div>
      </div>`;
    };
    const bodyHtml = w.entity === "both"
      ? podGroup(w.teamWinners, "Top Teams", "teams") + podGroup(w.playerWinners, "Top Individual Builders", "players")
      : podGroup(w.winners, w.entity === "players" ? "Top Individual Builders" : "Top Teams", w.entity);
    modal.innerHTML = `<div class="card io-border" style="max-width:860px;width:100%;max-height:90vh;overflow-y:auto;padding:28px;text-align:center;position:relative;background:#fff">
      <button id="hcWinClose" class="btn light" style="position:absolute;top:16px;right:16px">✕ Close</button>
      <span class="eyebrow" style="color:#b45309">🏁 Event Ended · Official Results</span>
      <h2 class="h2" style="margin:8px 0 4px">${w.topN === 1 ? "Official Hackathon Winner" : "Official Top 3 Winners"}</h2>
      ${bodyHtml}
    </div>`;
    document.getElementById("hcWinClose").addEventListener("click", () => {
      try { sessionStorage.setItem(ackKey, stamp); } catch (_) {}
      modal.remove();
    });
  }

  async function nav(active) {
    try { config = await api("/api/config"); } catch (_) { config = { name: "Catalyst" }; }
    document.title = document.title.replace("{event}", config.name);
    announce(config.announcement);
    if (active !== "admin" && config.winners) renderWinnersPopup(config.winners);
    if (active !== "admin") {
      setInterval(async () => {
        try {
          const c = await api("/api/config");
          if ("announcement" in c) announce(c.announcement);
          renderWinnersPopup(c.winners);
        } catch (_) {}
      }, 6000);
    }
    const links = [["/", "Leaderboard", "home"], ["/awards", "Awards", "awards"], ["/stage", "Stage", "stage"], ["/admin", "🔒 Admin", "admin"]];
    const el = document.getElementById("nav");
    if (el) {
      el.className = "nav";
      el.innerHTML = `<div class="wrap">
        <a class="brand" href="${withS("/")}"><img class="brand-mark" src="/static/favicon.svg" alt="" width="24" height="24"><span>${esc(config.name)}</span></a>
        <nav class="nav-links">${links.map(([h, l, k]) => `<a href="${withS(h)}" class="${k === active ? "active" : ""}">${l}</a>`).join("")}
        <a href="${withS("/join")}" class="cta">${me() ? "My sync" : "Join"}</a></nav></div>`;
    }
    const foot = document.getElementById("footer");
    if (foot) {
      foot.className = "footer";
      foot.innerHTML = `<div class="wrap"><span>${esc(config.name)} · live agent telemetry</span><span><a href="${withS("/admin")}" style="color:inherit;text-decoration:none;opacity:0.8;font-weight:600;">🔒 Admin Console</a> &middot; Only counts, tool names and timings leave a laptop.</span></div>`;
    }
    return config;
  }

  // The saved identity belongs to one session; on another session's pages there is none.
  function me() {
    let m = null;
    try { m = JSON.parse(localStorage.getItem("hc_me") || "null"); } catch (_) { return null; }
    const here = S || (config && config.slug);
    return m && here && (m.slug || "main") !== here ? null : m;
  }

  // Organisers' announcement, set in /admin: a strip under the nav on every page.
  function announce(text) {
    const slot = document.getElementById("announceSlot");  // the stage has its own place for it
    if (slot) { slot.textContent = text || ""; slot.classList.toggle("hidden", !text); return; }
    let el = document.getElementById("announce");
    if (!text) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement("div");
      el.id = "announce";
      el.className = "announce";
      const nav = document.getElementById("nav");
      if (nav) nav.after(el); else document.body.prepend(el);
    }
    el.innerHTML = `<div class="wrap">${icon("zap", 16)}<span>${esc(text)}</span></div>`;
  }

  function countUp(el, to, format = fmt, ms = 900) {
    const from = Number(el.dataset.v || 0);
    el.dataset.v = to;
    if (from === to || matchMedia("(prefers-reduced-motion: reduce)").matches) { el.textContent = format(to); return; }
    const t0 = performance.now();
    const step = (t) => {
      const p = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - p, 3);
      el.textContent = format(from + (to - from) * e);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function shapes() {
    return `<div class="shapes" aria-hidden="true">
      <svg class="float-a" style="right:6%;top:40px" width="180" height="180" viewBox="0 0 180 180"><circle cx="90" cy="90" r="90" fill="#1a73e8"/></svg>
      <svg class="float-b" style="right:22%;top:170px" width="120" height="60" viewBox="0 0 120 60"><path d="M0 60a60 60 0 0 1 120 0z" fill="#f9ab00"/></svg>
      <svg class="float-a" style="right:1.5%;top:196px" width="72" height="72" viewBox="0 0 72 72"><rect width="72" height="72" rx="20" fill="#ea4335"/></svg>
      <svg class="float-c" style="right:18%;top:24px" width="76" height="76" viewBox="0 0 76 76"><circle cx="38" cy="38" r="30" fill="none" stroke="#1e8e3e" stroke-width="14"/></svg>
      <svg class="float-b" style="right:31%;top:60px" width="54" height="54" viewBox="0 0 54 54"><path d="M27 0l27 27-27 27L0 27z" fill="#1e8e3e" opacity=".9"/></svg>
    </div>`;
  }

  // ---------- events ----------
  function describe(e) {
    const defs = badgeDefs ? badgeDefs.by : {};
    const b = defs[e.badge];
    const who = `<b>${esc(e.name)}</b>`;
    switch (e.type) {
      case "joined": return { icon: "user", color: "#1a73e8", html: `${who} joined <b>${esc(e.team)}</b>` };
      case "badge": return { icon: b ? b.icon : "trophy", color: b ? b.color : "#f9ab00", badge: b, tier: e.tier,
        html: `${who} earned <b>${TIER[e.tier]} ${esc(b ? b.name : e.badge)}</b>` };
      case "crown": return { icon: "crown", color: "#f9ab00", badge: b, tier: 3,
        html: `${who} took the <b>${esc(b ? b.name : e.badge)}</b> crown${e.prev ? ` from ${esc(e.prev)}` : ""}` };
      case "rank": return { icon: "up", color: "#1e8e3e", html: `${who} climbed to <b>#${e.to}</b> <span class="faint">from #${e.from}</span>` };
      case "milestone": return { icon: "flag", color: "#ea4335",
        html: `The room passed <b>${fmt(e.value)} ${esc((METRICS[e.metric] || e.metric).toLowerCase())}</b>` };
      default: return { icon: "trophy", color: "#6b7079", html: esc(e.type) };
    }
  }

  function feedItem(e) {
    const d = describe(e);
    const lead = d.badge ? badge(d.badge, d.tier || 1, 34) :
      `<span class="feed-icon" style="background:${d.color}14;color:${d.color}">${icon(d.icon, 18, d.color)}</span>`;
    return `<div class="feed-item">${lead}<div class="txt">${d.html}</div><div class="when">${ago(e.ts)}</div></div>`;
  }

  function toast(e) {
    let box = document.querySelector(".toasts");
    if (!box) { box = document.createElement("div"); box.className = "toasts"; document.body.appendChild(box); }
    const d = describe(e);
    const el = document.createElement("div");
    el.className = "toast";
    el.innerHTML = (d.badge ? badge(d.badge, d.tier || 1, 40, { pop: true }) :
      `<span class="feed-icon" style="background:${d.color}14">${icon(d.icon, 20, d.color)}</span>`) + `<div class="txt">${d.html}</div>`;
    box.appendChild(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 450); }, 5200);
    while (box.children.length > 3) box.firstChild.remove();
  }

  async function listen(onEvents, interval = 5000) {
    let since = null;
    const poll = async () => {
      try {
        const d = await api("/api/events?since=" + (since ?? 0));
        if ("announcement" in d) announce(d.announcement);
        if ("winners" in d && !location.pathname.startsWith("/admin")) renderWinnersPopup(d.winners);
        if (since === null) { since = d.events.length ? d.events[d.events.length - 1].id : 0; onEvents([], d.events); }
        else if (d.events.length) { since = d.events[d.events.length - 1].id; onEvents(d.events, d.events); }
      } catch (_) { /* retry next tick */ }
    };
    await poll();
    setInterval(poll, interval);
  }

  function hourlyBars(hourly, color = "#1a73e8") {
    const max = Math.max(1, ...hourly);
    return `<div class="bars">${hourly.map((v, h) => `<i title="${h}:00 · ${fmtFull(v)} calls" style="height:${Math.max(2, (v / max) * 100)}%;background:${color};opacity:${v ? 1 : .25}"></i>`).join("")}</div>
      <div class="bars-x"><span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div>`;
  }

  function copyButtons(root = document) {
    root.querySelectorAll("[data-copy]").forEach((btn) => btn.addEventListener("click", async () => {
      const text = document.getElementById(btn.dataset.copy).dataset.text;
      try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; } catch (_) { btn.textContent = "Select & copy"; }
      setTimeout(() => (btn.textContent = "Copy"), 1600);
    }));
  }

  return { esc, fmt, fmtFull, fmtUsd, fmtHours, fmtPct, fmtMetric, fmtBadgeValue, ago, api, avatar, colorFor, memberColor, link,
    icon, badge, badges, badgeRow, nav, me, countUp, shapes, describe, feedItem, toast, listen, hourlyBars,
    copyButtons, withS, announce, METRICS, TIER, COLORS };
})();
