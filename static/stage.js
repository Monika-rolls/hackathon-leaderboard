"use strict";
(async () => {
  const { esc, fmt, fmtMetric, avatar, badge, METRICS } = HC;
  const SCENE_MS = 12000;
  const cfg = await HC.api("/api/config").catch(() => ({ name: "Catalyst" }));
  await HC.badges();
  document.title = `Stage · ${cfg.name}`;
  document.getElementById("eventName").textContent = cfg.name;
  HC.announce(cfg.announcement);

  // ?scene=N pins one scene (organisers can keep e.g. the podium up during announcements).
  const pinned = new URLSearchParams(location.search).get("scene");
  let data = null, idx = 0, paused = pinned !== null, timer = null, feed = [];

  const scenes = [
    { id: "live", render: sceneLive },
    { id: "builders", render: sceneBuilders },
    { id: "teams", render: sceneTeams },
    { id: "power", render: scenePower },
    { id: "crowns", render: sceneCrowns },
    { id: "feed", render: sceneFeed },
  ];

  function sceneLive() {
    const s = data.summary;
    return `<div class="s"><div class="s-eyebrow"><span class="live-dot"></span>Right now</div>
      <div class="s-title">The room, <span class="io-text">live.</span></div>
      <div class="mega">${[["Tool calls", s.calls, "#1a73e8"], ["Tokens", s.tokens, "#ea4335"], ["Commits", s.commits, "#1e8e3e"]].map(([l, v, c], i) =>
        `<div class="m"><div class="v" data-count="${v}" data-i="${i}">0</div><div class="l">${l}</div><div class="bar" style="background:${c}"></div></div>`).join("")}</div>
      <div class="sub-stats"><span><b>${fmt(s.participants)}</b> builders</span><span><b>${fmt(s.teams)}</b> teams</span>
        <span><b>${fmt(s.mcpCalls)}</b> MCP calls</span><span><b>${fmt(s.linesChanged)}</b> lines changed</span><span><b>${fmt(s.badgesEarned)}</b> badges</span></div></div>`;
  }

  function podium(list, label) {
    const top = list.slice(0, 3);
    if (top.length < 3) return `<div class="empty" style="font-size:2vw">Waiting for builders…</div>`;
    return `<div class="podium stage-podium">${[1, 0, 2].map((i) => {
      const x = top[i];
      return `<div class="pod p${i + 1}"><div class="place">${["1st", "2nd", "3rd"][i]}</div>
        <div style="margin-top:2vh">${avatar(x.name, Math.round(innerHeight * (i === 0 ? 0.11 : 0.085)), ["#c9971c", "#8d97a5", "#b0652a"][i])}</div>
        <div class="who">${esc(x.name)}</div><div class="team">${esc(label(x))}</div>
        <div class="big">${fmt(x.metrics.calls)}</div><div class="muted">tool calls</div></div>`;
    }).join("")}</div>`;
  }

  function sceneBuilders() {
    const rest = data.players.slice(3, 9);
    return `<div class="s"><div class="s-eyebrow">${HC.icon("trophy", 22, "#c9971c")} Top builders</div>
      ${podium(data.players, (p) => p.team)}
      <div class="runners">${rest.map((p) => `<div class="runner"><span class="r">#${p.rank}</span>${avatar(p.name, Math.round(innerHeight * 0.04))}
        <span class="n">${esc(p.name)} <span class="faint" style="font-weight:500">· ${esc(p.team)}</span></span><span class="v">${fmt(p.metrics.calls)}</span></div>`).join("")}</div></div>`;
  }

  function sceneTeams() {
    const teams = data.teams.slice(0, 6);
    return `<div class="s"><div class="s-eyebrow">${HC.icon("users", 22, "#1a73e8")} Team battle</div>
      <div class="s-title" style="margin-bottom:5vh">Every member counts.</div>
      <div class="race">${teams.map((t, i) => {
        const total = t.metrics.calls || 1;
        return `<div class="race-row"><span class="n">${i + 1}. ${esc(t.name)}</span>
          <div class="split">${t.members.map((m, j) => `<i style="flex-grow:${(m.metrics.calls || 0) / total};background:${HC.memberColor(j)}"></i>`).join("")}</div>
          <span class="v">${fmt(t.metrics.calls)}</span></div>`;
      }).join("") || '<div class="empty">No teams yet.</div>'}</div></div>`;
  }

  function scenePower() {
    const ph = data.powerHour;
    const max = Math.max(1, ...ph.top.map((t) => t.value));
    const mins = Math.max(0, Math.ceil((ph.endsAt - Date.now() / 1000) / 60));
    return `<div class="s"><div class="s-eyebrow">${HC.icon("zap", 22, "#ea4335")} Power hour · ${mins} min left</div>
      <div class="s-title">${esc(ph.title)}</div>
      <div class="race">${ph.top.slice(0, 5).map((t, i) => `<div class="race-row"><span class="n">${esc(t.name)}</span>
        <div class="track"><i style="width:${(t.value / max) * 100}%;background:${HC.COLORS[i % 4]};animation-delay:${i * 90}ms"></i></div>
        <span class="v">${fmtMetric(ph.metric, t.value)}</span></div>`).join("") || '<div class="empty" style="font-size:2vw">Nobody has scored this hour. First move wins.</div>'}</div></div>`;
  }

  function sceneCrowns() {
    const aw = [...data.awards.personal, ...data.awards.team].filter((a) => a.holder).slice(0, 12);
    const size = Math.round(innerHeight * 0.075);
    return `<div class="s"><div class="s-eyebrow">${HC.icon("crown", 22, "#f9ab00")} Crown holders</div>
      <div class="s-title" style="font-size:clamp(36px,4.4vw,96px)">Who wears what.</div>
      <div class="crown-grid">${aw.map((a) => `<div class="crown-cell">${badge(a, a.top[0] ? a.top[0].tier : 1, size, { crown: true })}
        <div style="min-width:0"><div class="bn">${esc(a.name)}</div><div class="hn">${esc(a.holder)}</div></div></div>`).join("") ||
        '<div class="empty" style="font-size:2vw">No crowns claimed yet.</div>'}</div></div>`;
  }

  function sceneFeed() {
    return `<div class="s"><div class="s-eyebrow"><span class="live-dot"></span>Just happened</div>
      <div class="big-feed">${feed.slice(-7).reverse().map(HC.feedItem).join("") || '<div class="empty" style="font-size:2vw">Quiet… for now.</div>'}</div></div>`;
  }

  function sceneWinners() {
    const w = data && data.event && data.event.winners;
    if (!w || !w.ended || (!w.winners?.length && !w.teamWinners?.length && !w.playerWinners?.length)) return sceneBuilders();
    const list = (w.entity === "both" ? w.teamWinners : w.winners) || [];
    const order = list.length >= 3 ? [1, 0, 2] : [0];
    const medals = { 1: "1st", 2: "2nd", 3: "3rd" };
    const rings = { 1: "#c9971c", 2: "#8d97a5", 3: "#b0652a" };
    return `<div class="s"><div class="s-eyebrow">${HC.icon("trophy", 22, "#c9971c")} Official Hackathon ${w.topN === 1 ? "Winner" : "Winners"}</div>
      <div class="s-title" style="margin-bottom:3vh">${w.topN === 1 ? "Champion." : "Top 3 Podium."}</div>
      <div class="podium stage-podium" style="justify-content:center">${order.filter((idx) => list[idx]).map((idx) => {
        const x = list[idx];
        return `<div class="pod p${x.place}"><div class="place">${medals[x.place]}</div>
          <div style="margin-top:2vh">${avatar(x.name, Math.round(innerHeight * (x.place === 1 ? 0.13 : 0.09)), rings[x.place] || "#c9971c")}</div>
          <div class="who">${esc(x.name)}</div><div class="team">${esc(x.subtitle || "")}</div>
          <div class="big">${fmt(x.calls)}</div><div class="muted">tool calls · ${fmt(x.commits)} commits</div></div>`;
      }).join("")}</div></div>`;
  }

  function show(i) {
    idx = (i + scenes.length) % scenes.length;
    if (!data) return;
    const el = document.getElementById("scene");
    const w = data.event && data.event.winners;
    el.innerHTML = (w && w.ended && idx === 0) ? sceneWinners() : scenes[idx].render();
    el.querySelectorAll("[data-count]").forEach((n) => HC.countUp(n, Number(n.dataset.count), fmt, 1600));
    document.getElementById("dots").innerHTML = scenes.map((_, j) => `<i class="${j === idx ? "on" : ""}" data-j="${j}"></i>`).join("");
    clearTimeout(timer);
    if (!paused) timer = setTimeout(() => show(idx + 1), SCENE_MS);
  }

  document.getElementById("dots").addEventListener("click", (e) => { const d = e.target.closest("[data-j]"); if (d) show(Number(d.dataset.j)); });
  addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") show(idx + 1);
    else if (e.key === "ArrowLeft") show(idx - 1);
    else if (e.key === " ") { paused = !paused; show(idx); e.preventDefault(); }
    else if (e.key.toLowerCase() === "f") (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen()).catch(() => {});
  });

  function chrome() {
    const s = data.summary;
    document.getElementById("liveCount").textContent = `${s.live} building now`;
    document.getElementById("liveDot").classList.toggle("off", !s.live);
  }

  function tick() {
    const now = new Date();
    document.getElementById("clock").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const cd = document.getElementById("countdown");
    if (cfg.end) {
      const left = Math.max(0, Date.parse(cfg.end) - now);
      const h = Math.floor(left / 3.6e6), m = Math.floor((left % 3.6e6) / 6e4), sec = Math.floor((left % 6e4) / 1e3);
      cd.textContent = left ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")} left` : "Time's up";
      cd.classList.remove("hidden");
    }
  }
  setInterval(tick, 1000);
  tick();

  async function load() {
    try {
      const first = !data;
      data = await HC.api("/api/leaderboard");
      chrome();
      if (first) show(Number(pinned) || 0);
    } catch (_) { /* projector keeps the last frame */ }
  }
  await load();
  setInterval(load, 8000);

  // ---- spotlight announcements ----
  const queue = [];
  let showing = false;
  function important(e) {
    return e.type === "crown" || e.type === "milestone" || (e.type === "badge" && e.tier === 3) || (e.type === "rank" && e.to <= 3);
  }
  function confetti() {
    const box = document.getElementById("confetti");
    box.innerHTML = Array.from({ length: 70 }, (_, i) => `<i style="left:${Math.random() * 100}vw;background:${HC.COLORS[i % 4]};animation-delay:${Math.random() * 0.8}s;animation-duration:${2.2 + Math.random() * 1.6}s"></i>`).join("");
    setTimeout(() => (box.innerHTML = ""), 4200);
  }
  function spotlight() {
    if (showing || !queue.length) return;
    showing = true;
    const e = queue.shift(), d = HC.describe(e);
    const kicker = { crown: "New crown holder", milestone: "Milestone", badge: "Gold badge unlocked", rank: "On the podium" }[e.type] || "";
    const lead = d.badge ? badge(d.badge, d.tier || 3, Math.round(innerHeight * 0.22), { pop: true, crown: e.type === "crown" })
      : `<span style="display:grid;place-items:center;width:18vh;height:18vh;border-radius:50%;background:${d.color}14">${HC.icon(d.icon, Math.round(innerHeight * 0.09), d.color)}</span>`;
    const headline = e.type === "milestone" ? `${fmt(e.value)} ${esc((METRICS[e.metric] || e.metric).toLowerCase())}` : esc(e.name);
    const sub = e.type === "milestone" ? "reached by the whole room together" : d.html;
    document.getElementById("spotInner").innerHTML = `${lead}<div class="kicker" style="color:${d.color}">${kicker}</div>
      <div class="headline">${headline}</div><div class="sub">${sub}</div>`;
    const sp = document.getElementById("spotlight");
    sp.classList.add("on");
    confetti();
    setTimeout(() => { sp.classList.remove("on"); setTimeout(() => { showing = false; spotlight(); }, 700); }, 5500);
  }

  function renderTicker() {
    const items = feed.slice(-14).reverse().map((e) => {
      const d = HC.describe(e);
      return `<span>${HC.icon(d.icon, 16, d.color)} ${d.html}</span>`;
    });
    document.getElementById("ticker").innerHTML = items.join("") || "<span>Join at /join and your agent activity lands here live.</span>";
  }

  HC.listen((fresh, all) => {
    if (!fresh.length) feed = all.slice(-30);
    else {
      feed = feed.concat(fresh).slice(-30);
      fresh.filter(important).slice(-4).forEach((e) => queue.push(e));
      spotlight();
      load();
    }
    renderTicker();
    if (scenes[idx].id === "feed" && data) document.getElementById("scene").innerHTML = sceneFeed();
  }, 4000);
})();
