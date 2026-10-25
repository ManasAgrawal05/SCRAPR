// Profile (username + optional image, kept in this tab's sessionStorage) and the in-game leaderboard panel.
(() => {
  const roots = [...document.querySelectorAll(".tb-widgets")];
  if (!roots.length) return;
  const game = roots[0].dataset.game;  // "" on the home screen
  const PROFILE = "scrapr:profile", LB_CLOSED = "scrapr:lb-closed";
  const store = {
    get: k => { try { return sessionStorage.getItem(k); } catch (_) { return null; } },
    set: (k, v) => { try { sessionStorage.setItem(k, v); } catch (_) {} },
  };
  let profile = { name: "", avatar: "" };
  try { profile = { ...profile, ...JSON.parse(store.get(PROFILE) || "{}") }; } catch (_) {}
  let me = null;
  try { me = JSON.parse(store.get("scrapr:" + game)); } catch (_) {}
  let lbOpen = !!game && store.get(LB_CLOSED) !== "1";  // open by default in a game
  let snapshot = null;

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const PERSON = '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8m0 2c-3 0-8 1.5-8 4.5V20h16v-1.5c0-3-5-4.5-8-4.5"/></svg>';

  // fill a circle with an image, or an initial, or the generic person icon
  function paintAvatar(node, src, name) {
    node.innerHTML = "";
    node.classList.toggle("has-img", !!src);
    if (src) { const im = el("img"); im.src = src; im.alt = ""; node.append(im); }
    else if (name) node.append(document.createTextNode(name.trim()[0].toUpperCase()));
    else node.innerHTML = PERSON;
  }

  // downsize to a 96px square JPEG so it's tiny to store and send
  const toAvatar = file => new Promise((resolve, reject) => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const size = 96, c = document.createElement("canvas"), m = Math.min(img.width, img.height);
      c.width = c.height = size;
      c.getContext("2d").drawImage(img, (img.width - m) / 2, (img.height - m) / 2, m, m, 0, 0, size, size);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => reject(new Error("That file isn't an image."));
    img.src = url;
  });

  function renderProfile() {
    roots.forEach(r => {
      paintAvatar(r.querySelector(".tb-profile .av"), profile.avatar, profile.name);
      r.querySelector(".pname").textContent = profile.name;
    });
  }

  function renderLeaderboard() {
    roots.forEach(r => {
      const panel = r.querySelector(".tb-lb");
      panel.hidden = !lbOpen;
      if (!lbOpen) return;
      const list = r.querySelector(".lb-list");
      list.innerHTML = "";
      if (!snapshot) { list.append(el("li", "lb-empty", "Loading…")); return; }
      const st = snapshot.state, totals = (st && st.totals) || {}, round = st && st.phase === "round" ? st.players : null;
      // totals only include finished rounds; add what each player has already earned this round
      const rows = snapshot.players.map((p, i) => ({ p, i, pts: (totals[p.id] || 0) + (round && round[p.id] ? round[p.id].points : 0) }))
        .sort((a, b) => b.pts - a.pts || a.i - b.i);
      rows.forEach((row, rank) => {
        const li = el("li", me && me.id === row.p.id ? "me" : "");
        li.append(el("span", "lb-rank", String(rank + 1)));
        const av = el("span", "av");
        paintAvatar(av, row.p.avatar, row.p.name);
        li.append(av);
        li.append(el("span", "lb-name", row.p.name + (me && me.id === row.p.id ? " (you)" : "")));
        if (round && round[row.p.id]) {
          const s = round[row.p.id].status;
          li.append(el("span", "lb-chip " + s, s === "solved" ? "solved" : s === "timeout" ? "out" : "searching"));
        }
        li.append(el("span", "lb-pts", String(row.pts)));
        list.append(li);
      });
    });
  }

  function setLeaderboard(open) {
    lbOpen = open;
    if (game) store.set(LB_CLOSED, open ? "0" : "1");
    renderLeaderboard();
  }

  // ---- profile popover (one open at a time) ----------------------------
  let draftAvatar = "";
  roots.forEach(r => {
    const pop = r.querySelector(".tb-pop"), name = r.querySelector(".pop-name"), err = r.querySelector(".pop-error");
    const preview = r.querySelector(".pop-preview"), remove = r.querySelector(".pop-remove");
    const refreshPreview = () => {
      paintAvatar(preview, draftAvatar, name.value);
      remove.hidden = !draftAvatar;
    };
    const close = () => { pop.hidden = true; };
    const open = () => {
      draftAvatar = profile.avatar;
      name.value = profile.name;
      err.textContent = "";
      refreshPreview();
      pop.hidden = false;
      name.focus();
    };
    r.querySelector(".tb-profile").addEventListener("click", e => { e.stopPropagation(); pop.hidden ? open() : close(); });
    pop.addEventListener("click", e => e.stopPropagation());
    name.addEventListener("input", refreshPreview);
    r.querySelector(".pop-file input").addEventListener("change", async e => {
      const file = e.target.files[0];
      e.target.value = "";
      if (!file) return;
      try { draftAvatar = await toAvatar(file); err.textContent = ""; refreshPreview(); }
      catch (ex) { err.textContent = ex.message; }
    });
    remove.addEventListener("click", () => { draftAvatar = ""; refreshPreview(); });
    r.querySelector(".pop-cancel").addEventListener("click", close);
    const save = async () => {
      profile = { name: name.value.trim().slice(0, 24), avatar: draftAvatar };
      store.set(PROFILE, JSON.stringify(profile));
      renderProfile();
      close();
      if (game && me) {  // let the other players see the change
        try {
          const res = await fetch("/api/games/" + game + "/profile", {
            method: "POST", headers: { "Content-Type": "application/json", "X-Player-Token": me.token },
            body: JSON.stringify({ name: profile.name, avatar: profile.avatar }),
          });
          if (res.ok) { snapshot = await res.json(); renderLeaderboard(); }
        } catch (_) {}
      }
    };
    r.querySelector(".pop-save").addEventListener("click", save);
    name.addEventListener("keydown", e => { if (e.key === "Enter") save(); if (e.key === "Escape") close(); });

    const apps = r.querySelector(".tb-apps");
    if (game) {
      apps.addEventListener("click", e => { e.stopPropagation(); setLeaderboard(!lbOpen); });
      r.querySelector(".tb-lb .lb-close").addEventListener("click", () => setLeaderboard(false));
    }
    const rules = r.querySelector(".tb-rules");  // home screen: the grid shows the rules instead
    if (rules && !game) {
      apps.addEventListener("click", e => { e.stopPropagation(); pop.hidden = true; rules.hidden = !rules.hidden; });
      rules.addEventListener("click", e => e.stopPropagation());
      rules.querySelector(".rules-close").addEventListener("click", () => { rules.hidden = true; });
      r.querySelector(".tb-profile").addEventListener("click", () => { rules.hidden = true; });
    }
  });
  const closeFloating = () => roots.forEach(r => {
    r.querySelector(".tb-pop").hidden = true;
    const rules = r.querySelector(".tb-rules");
    if (rules) rules.hidden = true;
  });
  document.addEventListener("click", closeFloating);
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeFloating(); });

  renderProfile();
  renderLeaderboard();
  if (game) {
    const poll = () => fetch("/api/games/" + game).then(r => r.ok ? r.json() : null)
      .then(g => { if (g) { snapshot = g; renderLeaderboard(); } }).catch(() => {});
    poll();
    setInterval(poll, 2000);
  }
})();
