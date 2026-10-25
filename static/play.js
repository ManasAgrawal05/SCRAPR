// In-game HUD: round/clock/counts, answer form, round + final overlay, and search/click tracking.
(() => {
  const hud = document.getElementById("hud");
  if (!hud) return;
  const id = hud.dataset.game;
  let me = null;
  try { me = JSON.parse(sessionStorage.getItem("scrapr:" + id)); } catch (_) {}
  const $ = i => document.getElementById(i);
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const fmt = s => Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");

  let game = null, offset = 0, feedback = "", overlayKey = "";
  const now = () => Date.now() / 1000 + offset;

  const api = (path, body) => {
    if (!me) return Promise.resolve(null);
    return fetch("/api/games/" + id + "/" + path, {
      method: "POST", keepalive: true,
      headers: { "Content-Type": "application/json", "X-Player-Token": me.token },
      body: JSON.stringify(body),
    }).then(r => r.json().then(data => ({ ok: r.ok, status: r.status, data }))).catch(() => null);
  };
  const post = (path, body) => api(path, body).then(r => { if (r && r.ok) apply(r.data); return r; });

  const apply = g => { offset = g.now - Date.now() / 1000; game = g; render(); };
  const poll = () => fetch("/api/games/" + id).then(r => r.ok ? r.json() : null).then(g => g && apply(g)).catch(() => {});

  const name = (pid) => {
    const p = game.players.find(x => x.id === pid);
    return (p ? p.name : pid) + (me && me.id === pid ? " (you)" : "");
  };

  // ---- shopping: select one listing, then submit it ----------------------------
  const SEL = "scrapr:sel:" + id;
  const loadSel = () => {
    try {
      const v = JSON.parse(sessionStorage.getItem(SEL));
      return v && game && game.state && v.round === game.state.round ? v : null;  // a pick from an earlier round is stale
    } catch (_) { return null; }
  };
  const saveSel = v => { try { v ? sessionStorage.setItem(SEL, JSON.stringify(v)) : sessionStorage.removeItem(SEL); } catch (_) {} };
  function renderSel() {
    const sel = loadSel();
    $("answerBtn").disabled = !sel;  // nothing to submit until a product box has been clicked
    $("answerBtn").title = sel ? "" : "Click a product to select it first";
    document.querySelectorAll(".shop-card").forEach(c => {
      const on = !!sel && c.dataset.id === sel.id;
      c.classList.toggle("selected", on);
      c.setAttribute("aria-pressed", on);
    });
  }
  function pick(card) {
    const g = game && game.state;
    const mine = g && me && g.players[me.id];
    if (!g || g.kind !== "shopping" || g.phase !== "round" || !mine || mine.status !== "searching") return;
    const ratingEl = card.querySelector(".shop-rating b");
    // only the id matters to the server; the rest is just for the summary line
    saveSel({ id: card.dataset.id, round: g.round, title: card.querySelector(".shop-title").textContent,
              price_text: card.querySelector(".shop-price").textContent, rating: ratingEl ? parseFloat(ratingEl.textContent) : null });
    feedback = "";
    render();
  }
  document.addEventListener("click", e => {
    if (e.target.closest("a")) return;  // the picture and title are links to the listing; anywhere else in the box selects it
    const c = e.target.closest && e.target.closest(".shop-card");
    if (c) pick(c);
  });
  document.addEventListener("keydown", e => {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList && e.target.classList.contains("shop-card")) { e.preventDefault(); pick(e.target); }
  });

  function render() {
    const s = game && game.state;
    if (!s) return;
    const mine = me && s.players[me.id];
    $("roundlabel").textContent = "Round " + s.round + "/" + s.of;
    $("qtext").textContent = s.question;
    $("counts").textContent = mine ? mine.searches + " searches, " + mine.clicks + " clicks" : "";
    const shop = s.kind === "shopping";  // shopping: pick a listing instead of typing an answer
    $("answerInput").hidden = shop;
    if (shop) renderSel(); else { $("answerBtn").disabled = false; $("answerBtn").title = ""; }
    $("answerForm").hidden = !(mine && s.phase === "round" && mine.status === "searching");
    $("answerMsg").textContent = mine && mine.status === "solved" && s.phase === "round"
      ? "Correct! +" + mine.points + " pts" : feedback;
    renderOverlay(s);
    tick();
  }

  function renderOverlay(s) {
    const box = $("overlay");
    const key = s.phase + ":" + s.round;
    if (s.phase === "round") { box.hidden = true; overlayKey = ""; return; }
    if (key === overlayKey) return;
    overlayKey = key;
    box.hidden = false;
    box.innerHTML = "";
    const card = el("div", "overlay-card");
    const last = s.history[s.history.length - 1];
    if (s.phase === "intermission") {
      card.append(el("h2", "", "Round " + last.round + " of " + s.of + " complete"));
      card.append(el("p", "ov-answer", last.reveal || "Answer: " + last.answer));
    } else {
      const w = s.winner;
      card.append(el("h2", "", "Game over"));
      card.append(el("p", "ov-answer", w === "draw" ? "It's a draw!" : me ? (w === me.id ? "You win!" : "You lose.") : name(w) + " wins"));
    }
    if (s.kind === "shopping") {  // what everyone picked in the round that just ended
      const picks = el("ul", "ov-picks");
      game.players.forEach(p => {
        const r = last.players[p.id], li = el("li");
        if (r.pick) {
          li.append(document.createTextNode(name(p.id) + ": " + r.pick.title + " — " + r.pick.price_text + (r.pick.rating ? " · ★" + r.pick.rating : "")));
          const b = r.breakdown;
          const parts = [];  // a scoring part the host switched off is null, so it isn't listed
          if (b.rating !== null) parts.push("rating +" + b.rating);
          if (b.cost !== null) parts.push("price +" + b.cost);
          parts.push("speed +" + b.speed);
          if (b.wrong) parts.push("wrong picks " + b.wrong);
          li.append(el("small", "", parts.join(" · ") + " = " + b.total));
        } else li.append(document.createTextNode(name(p.id) + ": no pick"));
        picks.append(li);
      });
      card.append(picks);
    }
    const table = el("table", "ov-table");
    const head = el("tr");
    head.append(el("th", "", ""));
    s.history.forEach(h => head.append(el("th", "", "R" + h.round)));
    head.append(el("th", "", "Total"));
    table.append(head);
    game.players.forEach(p => {
      const row = el("tr", me && me.id === p.id ? "me" : "");
      row.append(el("td", "", name(p.id)));
      s.history.forEach(h => {
        const r = h.players[p.id];
        const cell = el("td", "", r.status === "solved" ? "+" + r.points : "–");
        cell.title = r.status === "solved" ? (r.pick ? r.pick.title + " · " : "") + "Done in " + fmt(r.time) : "Did not finish";
        row.append(cell);
      });
      row.append(el("td", "ov-total", String(s.totals[p.id])));
      table.append(row);
    });
    card.append(table);
    if (s.phase === "intermission") {
      card.append(el("p", "ov-next", ""));
      card.lastChild.id = "ovNext";
    } else {
      const b = el("button", "btn", "Back to SCRAPR");
      b.addEventListener("click", () => { location.href = "/"; });
      card.append(b);
    }
    box.append(card);
  }

  function tick() {
    const s = game && game.state;
    if (!s) return;
    const mine = me && s.players[me.id];
    if (s.phase === "round") {
      $("clock").textContent = fmt(mine && mine.status === "solved" ? mine.time : Math.max(0, now() - s.round_started));
      if (s.deadline) {
        const left = fmt(Math.max(0, s.deadline - now()));
        $("grace").textContent = !mine || mine.status === "searching"
          ? "· someone finished, " + left + " left" : "· waiting for the others, " + left;
      } else $("grace").textContent = "";
    } else {
      $("grace").textContent = "";
      const n = $("ovNext");
      if (n) n.textContent = "Next round in " + Math.max(0, Math.ceil(s.next_at - now())) + "s";
    }
  }

  $("answerForm").addEventListener("submit", async e => {
    e.preventDefault();
    if (game && game.state && game.state.kind === "shopping") {
      const sel = loadSel();
      if (!sel) { feedback = "Select a listing first."; render(); return; }
      const r = await post("action", { action: { type: "select", item_id: sel.id } });
      if (!r) feedback = "Couldn't reach the server.";
      else if (!r.ok) { feedback = r.data.error || "Something went wrong."; if (r.status === 410) saveSel(null); }
      else if (!r.data.result.correct) feedback = r.data.result.message || "That listing doesn't match the task.";
      else { feedback = ""; saveSel(null); }
      render();
      return;
    }
    const input = $("answerInput");
    const text = input.value.trim();
    if (!text) return;
    const r = await post("action", { action: { type: "answer", text } });
    if (!r) { feedback = "Couldn't reach the server."; }
    else if (!r.ok) { feedback = r.data.error || "Something went wrong."; }
    else { feedback = r.data.result.correct ? "" : "Not quite — keep looking."; input.value = ""; }
    render();
  });

  // ---- tracking ----------------------------------------------------------
  const q = hud.dataset.q;
  const logClick = url => post("event", { type: "click", url, q });

  // Results page: open links inside SCRAPR's viewer instead of leaving the app.
  document.addEventListener("click", e => {
    const a = e.target.closest && e.target.closest("a[href^='http']");
    if (!a || !hud.dataset.page || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    const dest = "/view?game=" + encodeURIComponent(id) + "&q=" + encodeURIComponent(q) + "&url=" + encodeURIComponent(a.href);
    // Leave once the click is logged, or after 1.5s if the server is slow. It must happen exactly once, and the backup
    // timer must die with the first attempt: the browser keeps this page alive in its back/forward cache, so a timer
    // left running would fire when you press Back and throw you straight into the viewer again.
    let left = false, backup;
    const leave = () => {
      if (left) return;
      left = true;
      clearTimeout(backup);
      location.href = dest;
    };
    backup = setTimeout(leave, 1500);
    window.addEventListener("pagehide", () => clearTimeout(backup), { once: true });
    logClick(a.href).finally(leave);
  });
  // Viewer page: links clicked inside the framed page arrive as messages.
  window.addEventListener("message", e => {
    if (e.data && typeof e.data.scraprNav === "string") logClick(e.data.scraprNav);
  });

  poll().then(() => {
    // one search event per distinct results URL (a refresh doesn't count twice)
    const key = "scrapr:search:" + id;
    if (hud.dataset.page && sessionStorage.getItem(key) !== location.search) {
      sessionStorage.setItem(key, location.search);
      post("event", { type: "search", q, page: hud.dataset.page });
    }
  });
  setInterval(poll, 1500);
  setInterval(tick, 250);
})();
