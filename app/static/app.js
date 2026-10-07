const state = {
  owned: {},
  replacements: {},
  roleOverrides: {},
  attributeOverrides: {},
  queryOverrides: {},
  rolls: {},
  cheapOverrides: {},
  edhrecOverrides: {},
  poolNamesBySlot: {},
  edhrecStatus: "unavailable",
  lastAnalysis: null,
  analysisSnapshot: null,
  colorIdentity: "",
  formatKey: "brawl",
};

const COMMANDER_SLOT_KEY = "__commander__";

function imageSrc(card) {
  if (card?.image_url) return card.image_url;
  if (card?.name) {
    return `https://api.scryfall.com/cards/named?exact=${encodeURIComponent(card.name)}&format=image`;
  }
  return "";
}

function rarityLetter(rarity) {
  const r = (rarity || "").toLowerCase();
  if (r === "mythic" || r === "mythic rare" || r === "mythic-rare") return "M";
  if (r === "rare") return "R";
  if (r === "uncommon") return "U";
  if (r === "common") return "C";
  return "";
}

function rarityBadgeClass(rarity) {
  const r = (rarity || "").toLowerCase();
  if (r === "mythic" || r === "mythic rare" || r === "mythic-rare") return "badge-rarity-m";
  if (r === "rare") return "badge-rarity-r";
  if (r === "uncommon") return "badge-rarity-u";
  return "badge-rarity-c";
}

async function fetchStatus() {
  const el = document.getElementById("status");
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    if (data.cards_indexed === 0) {
      el.textContent = "Card index empty — building from Scryfall (first run may take several minutes)…";
      const refresh = await fetch("/api/index/refresh?force=true", { method: "POST" });
      if (!refresh.ok) {
        el.textContent = `Index build failed: ${await refresh.text()}`;
        return;
      }
      const again = await fetch("/api/status");
      const d2 = await again.json();
      el.textContent = `Indexed ${d2.cards_indexed} cards (Scryfall bulk ${d2.bulk_updated_at || "unknown"})`;
    } else {
      el.textContent = `Indexed ${data.cards_indexed} cards`;
    }
  } catch (e) {
    el.textContent = "Could not load card index.";
  }
}

document.getElementById("collectionFile").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  const status = document.getElementById("collectionStatus");
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/collection/parse", { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    state.owned = data.owned || {};
    status.textContent = `Loaded ${data.unique_cards} unique cards (${data.format} format, ${data.row_count} rows).`;
  } catch (err) {
    status.textContent = `Import failed: ${err.message}`;
  }
});

function cardTile(card, onSelect, selectedName, selectable = true) {
  const div = document.createElement("div");
  div.className = "card-tile";
  if (selectedName === card.name) div.classList.add("card-tile-selected");
  const img = document.createElement("img");
  img.src = imageSrc(card);
  img.alt = card.name;
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";
  div.appendChild(img);
  const title = document.createElement("div");
  title.textContent = card.name;
  div.appendChild(title);
  if (card.mana_value !== undefined) {
    const mv = document.createElement("div");
    mv.className = "muted";
    mv.textContent = `MV ${card.mana_value} · ${card.type_line || ""}`;
    div.appendChild(mv);
  }
  if (card.owned) {
    const b = document.createElement("span");
    b.className = "badge badge-owned";
    b.textContent = "In collection";
    div.appendChild(b);
  } else {
    const letter = rarityLetter(card.rarity);
    if (letter) {
      const b = document.createElement("span");
      b.className = `badge ${rarityBadgeClass(card.rarity)}`;
      b.textContent = letter;
      div.appendChild(b);
    }
  }
  if (card.reasons?.length) {
    const r = document.createElement("div");
    r.className = "reasons";
    r.textContent = card.reasons.join("; ");
    div.appendChild(r);
  }
  if (selectable) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = selectedName === card.name ? "Selected" : "Select";
    if (selectedName === card.name) btn.classList.add("selected");
    btn.addEventListener("click", () => onSelect(card));
    div.appendChild(btn);
  }
  return div;
}

function defaultAttributeMap(noticed) {
  const map = {};
  for (const attr of noticed || []) {
    map[attr.id] = attr.group === "score";
  }
  return map;
}

function effectiveAttributeState(slotKey, attr, row) {
  const overrides = state.attributeOverrides[slotKey];
  if (overrides && attr.id in overrides) return overrides[attr.id];
  return attr.group === "score";
}

function buildAttributePanel(row, slotKey) {
  const wrap = document.createElement("div");
  wrap.className = "attribute-panel";

  const scoreLabel = document.createElement("div");
  scoreLabel.className = "attr-group-label";
  scoreLabel.textContent = "In the score (toggle off to ignore)";
  wrap.appendChild(scoreLabel);

  const scoreRow = document.createElement("div");
  scoreRow.className = "attr-chips";
  const optionalRow = document.createElement("div");
  optionalRow.className = "attr-chips";
  let hasOptional = false;

  for (const attr of row.noticed_attributes || []) {
    const on = effectiveAttributeState(slotKey, attr, row);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `attr-chip ${on ? "on" : "off"} group-${attr.group}`;
    const weightPct = Math.round((attr.weight || 0) * 100);
    btn.title = attr.used
      ? `Used in ranking (~${weightPct}% weight)`
      : `Noticed only (~${weightPct}% when on)`;
    btn.textContent = attr.label;
    btn.addEventListener("click", () => {
      const map =
        state.attributeOverrides[slotKey] ||
        defaultAttributeMap(row.noticed_attributes);
      const current = effectiveAttributeState(slotKey, attr, row);
      map[attr.id] = !current;
      state.attributeOverrides[slotKey] = map;
      state.rolls[slotKey] = 0;
      refreshSlots([slotKey]);
    });
    if (attr.group === "score") scoreRow.appendChild(btn);
    else {
      hasOptional = true;
      optionalRow.appendChild(btn);
    }
  }
  wrap.appendChild(scoreRow);
  if (hasOptional) {
    const optionalLabel = document.createElement("div");
    optionalLabel.className = "attr-group-label";
    optionalLabel.textContent = "Noticed (toggle on to require or emphasize)";
    wrap.appendChild(optionalLabel);
    wrap.appendChild(optionalRow);
  }

  wrap.appendChild(buildScryfallFilter(row, slotKey));
  appendRerollAndCheapButtons(wrap, slotKey, row);
  return wrap;
}

function buildScryfallFilter(row, slotKey) {
  const wrap = document.createElement("div");
  wrap.className = "scryfall-filter-wrap";
  const queryActive = !!(state.queryOverrides[slotKey] || "").trim();

  const status = document.createElement("p");
  status.className = "filter-status";
  if (row.query_error) {
    status.classList.add("query-error");
    status.textContent = row.query_error;
  } else if (row.query_matched === 0 && queryActive) {
    status.classList.add("query-error");
    status.textContent = "No cards matched this filter.";
  } else if (queryActive && row.query_matched != null) {
    status.textContent =
      row.query_matched === 1 ? "1 match" : `${row.query_matched} matches`;
  } else {
    status.classList.add("muted");
    status.textContent = "";
  }
  if (status.textContent) wrap.appendChild(status);

  const details = document.createElement("details");
  details.className = "scryfall-filter";
  if (queryActive) details.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "Scryfall filter";
  details.appendChild(summary);
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = 'e.g. t:creature mv<=3 pow>=4 o:trample -o:"enters the battlefield"';
  input.value = state.queryOverrides[slotKey] || "";
  input.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    const q = input.value.trim();
    if (q) state.queryOverrides[slotKey] = q;
    else delete state.queryOverrides[slotKey];
    state.rolls[slotKey] = 0;
    refreshSlots([slotKey]);
  });
  details.appendChild(input);
  wrap.appendChild(details);
  return wrap;
}

function formatColorIdentity(ci) {
  if (!ci) return "Colorless";
  const names = { W: "White", U: "Blue", B: "Black", R: "Red", G: "Green" };
  return [...ci].map((c) => names[c] || c).join(" · ");
}

function buildAnalyzeBody(commanderOverride) {
  state.formatKey = document.getElementById("formatKey").value;
  return {
    decklist: document.getElementById("decklist").value,
    format_key: state.formatKey,
    commander: commanderOverride || document.getElementById("commanderHint").value || null,
    replacements: state.replacements,
    role_overrides: state.roleOverrides,
    attribute_overrides: state.attributeOverrides,
    query_overrides: state.queryOverrides,
    rolls: state.rolls,
    cheap_overrides: state.cheapOverrides,
    edhrec_overrides: state.edhrecOverrides,
    pool_names_by_slot: {},
    prefer_collection: document.getElementById("preferCollection").checked,
    minimize_wildcards: document.getElementById("minimizeWildcards").checked,
    owned: state.owned,
    include_suggestions: true,
  };
}

function buildSlotRefreshBody(slots, mode = "full") {
  const pool_names_by_slot = {};
  if (mode === "light") {
    for (const sk of slots) {
      const names = state.poolNamesBySlot[sk];
      if (names?.length) pool_names_by_slot[sk] = names;
    }
  }
  return {
    snapshot: state.analysisSnapshot,
    slots,
    format_key: state.formatKey,
    replacements: state.replacements,
    role_overrides: state.roleOverrides,
    attribute_overrides: state.attributeOverrides,
    query_overrides: state.queryOverrides,
    rolls: state.rolls,
    cheap_overrides: state.cheapOverrides,
    edhrec_overrides: state.edhrecOverrides,
    pool_names_by_slot,
    prefer_collection: document.getElementById("preferCollection").checked,
    minimize_wildcards: document.getElementById("minimizeWildcards").checked,
    owned: state.owned,
  };
}

function ingestSlotMetaFromRows(rows, resetPools = false) {
  if (resetPools) state.poolNamesBySlot = {};
  for (const row of rows || []) {
    const slot = row.slot;
    if (!slot) continue;
    if (row.suggestion_pool_names?.length) {
      state.poolNamesBySlot[slot] = row.suggestion_pool_names;
    }
    if (state.edhrecStatus === "ok" && state.edhrecOverrides[slot] === undefined) {
      state.edhrecOverrides[slot] = row.prefer_edhrec !== false;
    }
  }
}

function allReplacementRows() {
  if (!state.lastAnalysis) return [];
  return [
    ...(state.lastAnalysis.replacement_rows || []),
    ...(state.lastAnalysis.sideboard_replacement_rows || []),
  ];
}

function findRowBySlot(slotKey) {
  for (const r of allReplacementRows()) {
    if (slotKeyForRow(r) === slotKey) return r;
  }
  return null;
}

function applyLocalPick(slotKey, card) {
  for (const key of ["replacement_rows", "sideboard_replacement_rows"]) {
    const list = state.lastAnalysis?.[key];
    if (!list) continue;
    for (const row of list) {
      if (slotKeyForRow(row) !== slotKey) continue;
      row.selected_replacement = { ...card };
    }
  }
}

function replaceReplacementBlock(slotKey) {
  const row = findRowBySlot(slotKey);
  if (!row) return;
  const el = document.querySelector(`.replacement-block[data-slot="${CSS.escape(slotKey)}"]`);
  if (!el) return;
  el.replaceWith(renderReplacementBlock(row));
}

function updateReplacementSummary() {
  const data = state.lastAnalysis;
  if (!data) return;
  const rows = data.replacement_rows || [];
  const summary = document.getElementById("summary");
  if (!summary) return;
  if (!rows.length) {
    summary.textContent = "";
    return;
  }
  const pending = rows.filter((r) => !state.replacements[slotKeyForRow(r)]).length;
  summary.textContent = `${rows.length} illegal slot(s), ${pending} still need a pick · deck size ${data.deck_size}/${data.expected_size}`;
}

function slotsToRefreshOnPick(slotKey, cardName, row) {
  const refresh = new Set();
  const linkGroup = row.link_group;
  for (const r of allReplacementRows()) {
    const sk = slotKeyForRow(r);
    if (sk === slotKey || state.replacements[sk]) continue;
    if (linkGroup && r.link_group === linkGroup) {
      refresh.add(sk);
      continue;
    }
    if ((r.suggestions || []).some((s) => s.name === cardName)) refresh.add(sk);
  }
  return [...refresh];
}

function pendingSlotKeys() {
  return allReplacementRows()
    .map((r) => slotKeyForRow(r))
    .filter((sk) => !state.replacements[sk]);
}

function allSlotsFilled() {
  const rows = allReplacementRows();
  return rows.length > 0 && rows.every((r) => state.replacements[slotKeyForRow(r)]);
}

function mergeSlotRows(updatedRows) {
  if (!state.lastAnalysis) return;
  const bySlot = new Map(updatedRows.map((r) => [r.slot, r]));
  for (const key of ["replacement_rows", "sideboard_replacement_rows"]) {
    const list = state.lastAnalysis[key];
    if (!list) continue;
    for (let i = 0; i < list.length; i++) {
      if (bySlot.has(list[i].slot)) list[i] = bySlot.get(list[i].slot);
    }
  }
}

function setSlotsLoading(slots, loading) {
  for (const slot of slots) {
    const el = document.querySelector(`.replacement-block[data-slot="${CSS.escape(slot)}"]`);
    if (el) el.classList.toggle("slot-loading", loading);
  }
}

async function refreshSlots(slots, { partial = false, mode = "full" } = {}) {
  if (!state.analysisSnapshot || !slots.length) return;
  if (mode === "full") {
    for (const sk of slots) delete state.poolNamesBySlot[sk];
  }
  setSlotsLoading(slots, true);
  try {
    const res = await fetch("/api/suggest/slots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildSlotRefreshBody(slots, mode)),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (data.edhrec) state.edhrecStatus = data.edhrec;
    const updated = data.rows || [];
    mergeSlotRows(updated);
    ingestSlotMetaFromRows(updated);
    if (partial) {
      for (const row of updated) replaceReplacementBlock(row.slot);
      updateReplacementSummary();
    } else {
      renderReplacements(state.lastAnalysis);
      renderSideboard(state.lastAnalysis);
    }
  } catch (err) {
    alert(err.message || String(err));
  } finally {
    setSlotsLoading(slots, false);
  }
}

async function finalizeDeck() {
  if (!state.analysisSnapshot) return;
  const res = await fetch("/api/finalize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      snapshot: state.analysisSnapshot,
      format_key: state.formatKey,
      replacements: state.replacements,
      owned: state.owned,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  if (data.pending) {
    state.lastAnalysis.final_deck = null;
    document.getElementById("final").classList.add("hidden");
    return;
  }
  state.lastAnalysis.final_deck = data.final_deck;
  state.lastAnalysis.wildcard_summary = data.wildcard_summary;
  state.lastAnalysis.arena_export = data.arena_export;
  renderFinal(state.lastAnalysis);
}

async function onPickReplacement(slotKey, card, row) {
  state.replacements[slotKey] = card.name;
  applyLocalPick(slotKey, card);
  replaceReplacementBlock(slotKey);
  updateReplacementSummary();
  const toRefresh = slotsToRefreshOnPick(slotKey, card.name, row);
  if (toRefresh.length) await refreshSlots(toRefresh, { partial: true, mode: "full" });
  if (allSlotsFilled()) {
    try {
      await finalizeDeck();
    } catch (err) {
      alert(err.message || String(err));
    }
  }
}

function setAnalyzeLoading(active, message, progressPct, indeterminate) {
  const panel = document.getElementById("analyzeLoading");
  const msg = document.getElementById("loadingMessage");
  const bar = document.getElementById("loadingProgressBar");
  const fill = document.getElementById("loadingProgressFill");
  const btn = document.getElementById("analyzeBtn");
  if (!active) {
    panel.classList.add("hidden");
    bar.classList.remove("indeterminate");
    fill.style.width = "0%";
    btn.disabled = false;
    return;
  }
  panel.classList.remove("hidden");
  msg.textContent = message;
  btn.disabled = true;
  bar.classList.toggle("indeterminate", !!indeterminate);
  if (!indeterminate) {
    fill.style.width = `${Math.min(100, Math.max(0, progressPct))}%`;
    bar.setAttribute("aria-valuenow", String(progressPct));
  }
}

function showLoadingDeckPreview(preview) {
  const info = document.getElementById("loadingDeckInfo");
  const cmdEl = document.getElementById("loadingCommander");
  const ciEl = document.getElementById("loadingColorIdentity");
  const hint = document.getElementById("loadingIllegalHint");
  info.classList.remove("hidden");
  if (preview.commander_candidates?.length && !preview.commander) {
    cmdEl.textContent = "Choose commander (multiple detected)";
    hint.textContent = `${preview.commander_candidates.length} commander candidate(s) — finish loading to pick.`;
  } else {
    cmdEl.textContent = preview.commander || "Unknown / unresolved";
    const n = preview.illegal_count || 0;
    hint.textContent = n
      ? `Finding replacements for ${n} card(s)…`
      : "Checking legality and building suggestions…";
  }
  ciEl.textContent = formatColorIdentity(preview.color_identity);
}

async function postAnalyze(body) {
  const res = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
}

async function runFullAnalyze(commanderOverride) {
  setAnalyzeLoading(true, "Analyzing deck…", 40, true);
  document.getElementById("loadingDeckInfo").classList.add("hidden");
  document.getElementById("commanderPick").classList.add("hidden");
  document.getElementById("replacements").classList.add("hidden");
  document.getElementById("final").classList.add("hidden");
  document.getElementById("sideboardReview").classList.add("hidden");

  let data;
  try {
    data = await postAnalyze(buildAnalyzeBody(commanderOverride));
    setAnalyzeLoading(true, "Done", 100, false);
  } catch (err) {
    setAnalyzeLoading(false);
    alert(err.message || String(err));
    return;
  } finally {
    setAnalyzeLoading(false);
  }

  state.lastAnalysis = data;
  state.analysisSnapshot = data.analysis_snapshot;
  state.colorIdentity = data.color_identity || "";
  state.edhrecStatus = data.edhrec || "unavailable";
  state.poolNamesBySlot = {};
  state.edhrecOverrides = {};
  ingestSlotMetaFromRows([
    ...(data.replacement_rows || []),
    ...(data.sideboard_replacement_rows || []),
  ]);

  const cmdSection = document.getElementById("commanderPick");
  const cmdDiv = document.getElementById("commanderCandidates");
  if (data.commander_candidates?.length && !data.commander) {
    cmdSection.classList.remove("hidden");
    cmdDiv.innerHTML = "";
    for (const name of data.commander_candidates) {
      const btn = document.createElement("button");
      btn.textContent = name;
      btn.addEventListener("click", () => {
        document.getElementById("commanderHint").value = name;
        runFullAnalyze(name);
      });
      cmdDiv.appendChild(btn);
    }
  } else {
    cmdSection.classList.add("hidden");
  }

  renderReplacements(data);
  renderSideboard(data);
  showDeckIdentity(data);
  renderFinal(data);
}

const runAnalyze = runFullAnalyze;

function showDeckIdentity(data) {
  const commander = data.commander || "Unknown";
  const colors = formatColorIdentity(data.color_identity);
  for (const el of document.querySelectorAll(".identityCommander")) el.textContent = commander;
  for (const el of document.querySelectorAll(".identityColors")) el.textContent = colors;
}

function renderSideboard(data) {
  const section = document.getElementById("sideboardReview");
  const row = document.getElementById("sideboardCards");
  const summary = document.getElementById("sideboardSummary");
  const illegalRows = document.getElementById("sideboardReplacementRows");
  const cards = data.sideboard_review || [];
  const replacements = data.sideboard_replacement_rows || [];
  if (!cards.length && !replacements.length) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  const legal = cards.filter((c) => c.status === "legal").length;
  summary.textContent = `${cards.length} sideboard card(s) — ${legal} legal, ${replacements.length} need an Arena replacement. Your picks are exported with the deck as Sideboard.`;
  row.innerHTML = "";
  for (const entry of cards) {
    if (entry.status !== "legal") continue;
    const tile = cardTile(entry.card, () => {}, null, false);
    const badge = document.createElement("span");
    badge.className = "badge badge-legal";
    badge.textContent = "Legal";
    tile.appendChild(badge);
    const r = document.createElement("div");
    r.className = "reasons";
    r.textContent = entry.reason || "";
    tile.appendChild(r);
    row.appendChild(tile);
  }
  illegalRows.innerHTML = "";
  for (const repl of replacements) {
    illegalRows.appendChild(renderReplacementBlock(repl));
  }
}

function slotKeyForRow(row) {
  if (row.slot === "commander") return COMMANDER_SLOT_KEY;
  return row.slot || row.illegal_name;
}

function appendRerollAndCheapButtons(wrap, slotKey, row) {
  const reroll = document.createElement("button");
  reroll.type = "button";
  reroll.textContent = "Show next 4";
  reroll.title = "Cycle through the top 12 ranked replacements (4 at a time)";
  reroll.addEventListener("click", () => {
    state.rolls[slotKey] = (state.rolls[slotKey] || 0) + 1;
    refreshSlots([slotKey], { partial: true, mode: "light" });
  });
  wrap.appendChild(reroll);

  const cheap = document.createElement("button");
  cheap.type = "button";
  cheap.textContent = "Prefer common/uncommon";
  if (state.cheapOverrides[slotKey] || row.prefer_cheap) {
    cheap.classList.add("selected");
  }
  cheap.addEventListener("click", () => {
    if (state.cheapOverrides[slotKey]) delete state.cheapOverrides[slotKey];
    else state.cheapOverrides[slotKey] = true;
    state.rolls[slotKey] = 0;
    refreshSlots([slotKey], { partial: true, mode: "light" });
  });
  wrap.appendChild(cheap);

  const edhrecWrap = document.createElement("label");
  edhrecWrap.className = "edhrec-pref";
  const edhrecCb = document.createElement("input");
  edhrecCb.type = "checkbox";
  const edhrecOn =
    slotKey in state.edhrecOverrides
      ? state.edhrecOverrides[slotKey]
      : row.prefer_edhrec !== false;
  edhrecCb.checked = edhrecOn;
  edhrecCb.disabled = !(row.edhrec_available || state.edhrecStatus === "ok");
  edhrecCb.addEventListener("change", () => {
    state.edhrecOverrides[slotKey] = edhrecCb.checked;
    state.rolls[slotKey] = 0;
    refreshSlots([slotKey], { partial: true, mode: "light" });
  });
  edhrecWrap.appendChild(edhrecCb);
  edhrecWrap.append(" Prefer EDHREC");
  wrap.appendChild(edhrecWrap);
}

function buildSlotActions(row, slotKey) {
  const wrap = document.createElement("div");
  wrap.className = "role-controls";
  appendRerollAndCheapButtons(wrap, slotKey, row);
  return wrap;
}

function renderReplacements(data) {
  const section = document.getElementById("replacements");
  const rowsEl = document.getElementById("replacementRows");
  const summary = document.getElementById("summary");
  const rows = data.replacement_rows || [];

  if (!rows.length) {
    section.classList.add("hidden");
    summary.textContent = "";
    return;
  }
  section.classList.remove("hidden");
  const pending = rows.filter((r) => {
    const sk = slotKeyForRow(r);
    return !state.replacements[sk];
  }).length;
  summary.textContent = `${rows.length} illegal slot(s), ${pending} still need a pick · deck size ${data.deck_size}/${data.expected_size}`;
  rowsEl.innerHTML = "";

  for (const row of rows) {
    rowsEl.appendChild(renderReplacementBlock(row));
  }
}

function renderReplacementBlock(row) {
  const block = document.createElement("div");
  const slotKey = slotKeyForRow(row);
  block.className = "replacement-block";
  block.dataset.slot = slotKey;
  const h = document.createElement("h3");
  const zone = row.zone === "sideboard" ? "Sideboard · " : "";
  let title = zone + row.illegal_name + (row.reason ? ` — ${row.reason}` : "");
  if ((row.linked_count || 1) > 1) {
    title += ` · copy ${(row.occurrence_index || 0) + 1} of ${row.linked_count}`;
  }
  h.textContent = title;
  block.appendChild(h);
  if ((row.linked_count || 1) > 1) {
    const linkBadge = document.createElement("p");
    linkBadge.className = "linked-badge";
    linkBadge.textContent = "Linked — each copy needs its own legal pick (same card cannot be reused).";
    block.appendChild(linkBadge);
  }

    const selected = state.replacements[slotKey];

    if (row.noticed_attributes?.length) {
      block.appendChild(buildAttributePanel(row, slotKey));
    } else if ((row.suggestions || []).length) {
      block.appendChild(buildSlotActions(row, slotKey));
    }

    const illegalRow = document.createElement("div");
    illegalRow.className = "card-row";
    const illegalLabel = document.createElement("div");
    illegalLabel.className = "row-label";
    illegalLabel.textContent = "Illegal / unavailable";
    block.appendChild(illegalLabel);
    illegalRow.appendChild(cardTile(row.illegal, () => {}, null, false));
    block.appendChild(illegalRow);

    if (row.selected_replacement || selected) {
      const picked = row.selected_replacement;
      const pickedRow = document.createElement("div");
      pickedRow.className = "card-row";
      const pickedLabel = document.createElement("div");
      pickedLabel.className = "row-label";
      pickedLabel.textContent = "Your pick";
      block.appendChild(pickedLabel);
      if (picked) {
        pickedRow.appendChild(
          cardTile(
            { ...picked, owned: (state.owned[picked.name] || 0) >= 1 },
            () => {},
            selected,
            false
          )
        );
      }
      block.appendChild(pickedRow);
    }

    const sugLabel = document.createElement("div");
    sugLabel.className = "row-label";
    sugLabel.textContent = "Legal replacements (pick one)";
    block.appendChild(sugLabel);

    const sugRow = document.createElement("div");
    sugRow.className = "card-row";
    for (const sug of row.suggestions || []) {
      sugRow.appendChild(
        cardTile(
          { ...sug, owned: sug.owned || (state.owned[sug.name] || 0) >= 1 },
          (card) => {
            onPickReplacement(slotKey, card, row);
          },
          selected
        )
      );
    }
    block.appendChild(sugRow);
  return block;
}

function renderFinal(data) {
  const section = document.getElementById("final");
  if (!data.final_deck) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  const gal = document.getElementById("finalGallery");
  gal.innerHTML = "";
  const deck = data.final_deck;
  if (deck.commander) gal.appendChild(cardTile(deck.commander, () => {}, null, false));
  for (const c of deck.main) gal.appendChild(cardTile(c, () => {}, null, false));

  const sbHeading = document.getElementById("finalSideboardHeading");
  const sbGal = document.getElementById("finalSideboard");
  sbGal.innerHTML = "";
  const sideboard = deck.sideboard || [];
  if (sideboard.length) {
    sbHeading.classList.remove("hidden");
    for (const c of sideboard) sbGal.appendChild(cardTile(c, () => {}, null, false));
  } else {
    sbHeading.classList.add("hidden");
  }

  document.getElementById("arenaExport").value = data.arena_export || "";
  const ws = data.wildcard_summary;
  const el = document.getElementById("wildcardSummary");
  if (ws) {
    const parts = [];
    if (ws.common) parts.push(`${ws.common}C`);
    if (ws.uncommon) parts.push(`${ws.uncommon}U`);
    if (ws.rare) parts.push(`${ws.rare}R`);
    if (ws.mythic) parts.push(`${ws.mythic}M`);
    const wc = parts.length ? parts.join(", ") : "0";
    if (data.has_collection) {
      el.textContent = `Wildcards to complete deck — ${wc}; already in your collection: ${ws.owned}`;
    } else {
      el.textContent = `Wildcards needed for full list — ${wc}; cards treated as owned: ${ws.owned}`;
    }
  } else {
    el.textContent = "";
  }
}

document.getElementById("analyzeBtn").addEventListener("click", () => {
  state.replacements = {};
  state.roleOverrides = {};
  state.attributeOverrides = {};
  state.queryOverrides = {};
  state.rolls = {};
  state.cheapOverrides = {};
  state.edhrecOverrides = {};
  state.poolNamesBySlot = {};
  runFullAnalyze();
});

document.getElementById("preferCollection").addEventListener("change", () => {
  if (state.analysisSnapshot) refreshSlots(pendingSlotKeys());
});
document.getElementById("minimizeWildcards").addEventListener("change", () => {
  if (state.analysisSnapshot) refreshSlots(pendingSlotKeys());
});

document.getElementById("copyExport").addEventListener("click", async () => {
  const t = document.getElementById("arenaExport").value;
  await navigator.clipboard.writeText(t);
});

document.getElementById("minimizeSideboard").addEventListener("click", () => {
  const body = document.getElementById("sideboardBody");
  const btn = document.getElementById("minimizeSideboard");
  const collapsed = body.classList.toggle("hidden");
  btn.textContent = collapsed ? "Show sideboard" : "Minimize";
  if (collapsed) {
    document.getElementById("replacements").scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

fetchStatus();
