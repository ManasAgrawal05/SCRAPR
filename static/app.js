(() => {
  // Home page = game lobby: no web search / autocomplete here.
  function lobby(form, input) {
    const err = document.createElement("div");
    err.className = "lobby-error";
    document.getElementById("homeButtons").after(err);
    const profile = () => { try { return JSON.parse(sessionStorage.getItem("scrapr:profile")) || {}; } catch (_) { return {}; } };
    const go = async (path, body) => {
      err.textContent = "";
      body = { ...body, name: profile().name || "", avatar: profile().avatar || "" };
      try {
        const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || "Request failed");
        sessionStorage.setItem("scrapr:" + data.game.id, JSON.stringify(data.player));
        location.href = "/game/" + data.game.id;
      } catch (e) { err.textContent = e.message; }
    };
    const join = () => {
      const id = input.value.trim().toUpperCase();
      if (!id) { err.textContent = "Enter a lobby code to join."; return input.focus(); }
      go("/api/games/" + encodeURIComponent(id) + "/join", {});
    };
    form.addEventListener("submit", e => { e.preventDefault(); join(); });
    document.getElementById("btnLucky").addEventListener("click", () => go("/api/games", {}));
  }

  const form = document.getElementById("searchForm");
  if (!form) return;
  const input = form.querySelector("input[name=q]");
  if (document.body.classList.contains("home")) return lobby(form, input);
  const list = form.querySelector(".suggestions");
  const clear = form.querySelector(".clear");
  let items = [], idx = -1, timer, seq = 0;

  const hide = () => { list.hidden = true; form.classList.remove("open"); idx = -1; };
  const render = () => {
    list.innerHTML = "";
    items.forEach((s, i) => {
      const li = document.createElement("li");
      li.textContent = s;
      li.addEventListener("mousedown", e => { e.preventDefault(); input.value = s; form.submit(); });
      list.appendChild(li);
    });
    list.hidden = !items.length;
    form.classList.toggle("open", items.length > 0);
  };
  const highlight = () => [...list.children].forEach((li, i) => li.classList.toggle("sel", i === idx));

  // In a game, words from the question can't be searched (play.js knows the list).
  const note = form.parentElement.querySelector(".banned-note");
  const offending = () => (window.scraprCheck ? window.scraprCheck(input.value) : []);
  const warn = () => {
    if (!note) return;
    const bad = offending();
    note.hidden = !bad.length;
    note.textContent = bad.length ? "Can't use words from the question: " + bad.join(", ") : "";
  };
  form.addEventListener("submit", e => { if (offending().length) { e.preventDefault(); warn(); } });

  input.addEventListener("input", () => {
    warn();
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { items = []; return hide(); }
    timer = setTimeout(async () => {
      const my = ++seq;
      try {
        const gm = form.querySelector("input[name=game]");
        const r = await fetch("/suggest?q=" + encodeURIComponent(q) + (gm ? "&game=" + encodeURIComponent(gm.value) : ""));
        const data = await r.json();
        if (my === seq) { items = data; idx = -1; render(); }
      } catch (_) {}
    }, 300);
  });
  input.addEventListener("keydown", e => {
    if (!items.length || list.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      idx = (idx + (e.key === "ArrowDown" ? 1 : -1) + items.length + 1) % (items.length + 1);
      if (idx === items.length) idx = -1;
      if (idx >= 0) input.value = items[idx];
      highlight();
    } else if (e.key === "Escape") hide();
  });
  input.addEventListener("blur", hide);
  if (clear) clear.addEventListener("click", () => { input.value = ""; input.focus(); });

  document.addEventListener("keydown", e => {
    if (e.key === "/" && document.activeElement !== input) { e.preventDefault(); input.focus(); }
  });

  const lucky = document.getElementById("btnLucky");
  if (lucky) lucky.addEventListener("click", () => {
    const q = input.value.trim();
    if (q) location.href = "/lucky?q=" + encodeURIComponent(q);
  });
})();
