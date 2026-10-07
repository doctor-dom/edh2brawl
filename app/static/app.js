const state = {
  owned: {},
  replacements: {},
  roleOverrides: {},
  rolls: {},
  cheapOverrides: {},
  lastAnalysis: null,
  colorIdentity: "",
  formatKey: "brawl",
};

function imageSrc(card) {
  if (card?.image_url) return card.image_url;
  if (card?.name) {
    return `https://api.scryfall.com/cards/named?exact=${encodeURIComponent(card.name)}&format=image`;
  }
  return "";
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
  } else if (card.wildcard_cost) {
    const b = document.createElement("span");
    b.className = "badge badge-wc";
    b.textContent = `${card.wildcard_cost} WC`;
    div.appendChild(b);
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

function formatSourceAttributes(attrs, activeRole) {
  if (!attrs) return "";
  const parts = [];
  if (attrs.detected_roles?.length) parts.push(`roles: ${attrs.detected_roles.join(", ")}`);
  if (activeRole) parts.push(`matching as: ${activeRole}`);
  parts.push(`MV ${attrs.mana_value}`);
  if (attrs.type_line) parts.push(attrs.type_line);
  if (attrs.subtypes?.length) parts.push(`tribal: ${attrs.subtypes.join(", ")}`);
  if (attrs.keywords?.length) parts.push(`keywords: ${attrs.keywords.join(", ")}`);
  if (attrs.triggers?.length) parts.push(`triggers: ${attrs.triggers.join(", ")}`);
  return parts.join(" · ");
}

function formatColorIdentity(ci) {
  if (!ci) return "Colorless";
  const names = { W: "White", U: "Blue", B: "Black", R: "Red", G: "Green" };
  return [...ci].map((c) => names[c] || c).join(" · ");
}

function buildAnalyzeBody(commanderOverride, includeSuggestions) {
  state.formatKey = document.getElementById("formatKey").value;
  return {
    decklist: document.getElementById("decklist").value,
    format_key: state.formatKey,
    commander: commanderOverride || document.getElementById("commanderHint").value || null,
    replacements: state.replacements,
    role_overrides: state.roleOverrides,
    rolls: state.rolls,
    cheap_overrides: state.cheapOverrides,
    prefer_collection: document.getElementById("preferCollection").checked,
    minimize_wildcards: document.getElementById("minimizeWildcards").checked,
    owned: state.owned,
    include_suggestions: includeSuggestions,
  };
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

async function runAnalyze(commanderOverride) {
  setAnalyzeLoading(true, "Reading decklist…", 8, false);
  document.getElementById("loadingDeckInfo").classList.add("hidden");
  document.getElementById("commanderPick").classList.add("hidden");
  document.getElementById("replacements").classList.add("hidden");
  document.getElementById("final").classList.add("hidden");
  document.getElementById("sideboardReview").classList.add("hidden");

  let data;
  try {
    const preview = await postAnalyze(buildAnalyzeBody(commanderOverride, false));
    setAnalyzeLoading(true, "Finding legal replacements…", 38, false);
    showLoadingDeckPreview(preview);
    setAnalyzeLoading(true, "Finding legal replacements…", 38, true);
    data = await postAnalyze(buildAnalyzeBody(commanderOverride, true));
    setAnalyzeLoading(true, "Done", 100, false);
  } catch (err) {
    setAnalyzeLoading(false);
    alert(err.message || String(err));
    return;
  } finally {
    setAnalyzeLoading(false);
  }

  state.lastAnalysis = data;
  state.colorIdentity = data.color_identity || "";

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
        runAnalyze(name);
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
  if (row.slot === "commander") return "__commander__";
  if (typeof row.slot === "string" && row.slot.startsWith("sb:")) return row.slot;
  return row.illegal_name;
}

function buildRoleSelect(row, slotKey) {
  const wrap = document.createElement("div");
  wrap.className = "role-controls";
  const label = document.createElement("label");
  label.textContent = "Major role ";
  const select = document.createElement("select");
  const detected = new Set(row.source_attributes?.detected_roles || []);
  const ordered = [
    ...(row.source_attributes?.detected_roles || []),
    ...(row.available_roles || []).filter((r) => !detected.has(r)),
  ];
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "Auto-detect";
  select.appendChild(blank);
  for (const role of ordered) {
    const opt = document.createElement("option");
    opt.value = role;
    opt.textContent = detected.has(role) ? `${role} (detected)` : role;
    if (row.active_role === role || state.roleOverrides[slotKey] === role) {
      opt.selected = true;
    }
    select.appendChild(opt);
  }
  select.addEventListener("change", () => {
    if (select.value) state.roleOverrides[slotKey] = select.value;
    else delete state.roleOverrides[slotKey];
    state.rolls[slotKey] = 0;
    runAnalyze();
  });
  label.appendChild(select);
  wrap.appendChild(label);
  appendRerollAndCheapButtons(wrap, slotKey, row.prefer_cheap);
  return wrap;
}

function appendRerollAndCheapButtons(wrap, slotKey, preferCheapActive) {
  const reroll = document.createElement("button");
  reroll.type = "button";
  reroll.textContent = "Reroll suggestions";
  reroll.addEventListener("click", () => {
    state.rolls[slotKey] = (state.rolls[slotKey] || 0) + 1;
    runAnalyze();
  });
  wrap.appendChild(reroll);

  const cheap = document.createElement("button");
  cheap.type = "button";
  cheap.textContent = "Prefer common/uncommon";
  if (state.cheapOverrides[slotKey] || preferCheapActive) {
    cheap.classList.add("selected");
  }
  cheap.addEventListener("click", () => {
    if (state.cheapOverrides[slotKey]) delete state.cheapOverrides[slotKey];
    else state.cheapOverrides[slotKey] = true;
    state.rolls[slotKey] = 0;
    runAnalyze();
  });
  wrap.appendChild(cheap);
}

function buildSlotActions(row, slotKey) {
  const wrap = document.createElement("div");
  wrap.className = "role-controls";
  appendRerollAndCheapButtons(wrap, slotKey, row.prefer_cheap);
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
  block.className = "replacement-block";
  const h = document.createElement("h3");
  const zone = row.zone === "sideboard" ? "Sideboard · " : "";
  h.textContent = zone + row.illegal_name + (row.reason ? ` — ${row.reason}` : "");
  block.appendChild(h);

    const slotKey = slotKeyForRow(row);
    const selected = state.replacements[slotKey];

    if (row.source_attributes) {
      const attr = document.createElement("p");
      attr.className = "muted attrs-line";
      attr.textContent = formatSourceAttributes(row.source_attributes, row.active_role);
      block.appendChild(attr);
      if (row.available_roles?.length) {
        block.appendChild(buildRoleSelect(row, slotKey));
      } else {
        block.appendChild(buildSlotActions(row, slotKey));
      }
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
            state.replacements[slotKey] = card.name;
            runAnalyze();
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
    const wc = `common: ${ws.common}, uncommon: ${ws.uncommon}, rare: ${ws.rare}, mythic: ${ws.mythic}`;
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
  state.rolls = {};
  state.cheapOverrides = {};
  runAnalyze();
});

document.getElementById("preferCollection").addEventListener("change", () => {
  if (state.lastAnalysis) runAnalyze();
});
document.getElementById("minimizeWildcards").addEventListener("change", () => {
  if (state.lastAnalysis) runAnalyze();
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
