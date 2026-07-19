(() => {
  "use strict";

  const params = new URLSearchParams(window.location.search);
  const player = cleanPlayerInput(params.get("player") || safeStorageGet("betaEarthPlayer") || "Traveler");
  const displayName = cleanDisplayName(params.get("name") || player);
  safeStorageSet("betaEarthPlayer", player);

  const ui = {
    worldTitle: document.querySelector("#world-title"), zoneName: document.querySelector("#zone-name"),
    roomName: document.querySelector("#room-name"), roomDescription: document.querySelector("#room-description"),
    roomAmbient: document.querySelector("#room-ambient"), dangerChip: document.querySelector("#danger-chip"),
    message: document.querySelector("#message"), playerStage: document.querySelector("#player-stage"),
    playerIdentity: document.querySelector("#player-identity"), playerRevision: document.querySelector("#player-revision"),
    credBalance: document.querySelector("#cred-balance"), stats: document.querySelector("#stats"),
    exits: document.querySelector("#exits"), missionJournal: document.querySelector("#mission-journal"),
    missionCount: document.querySelector("#mission-count"), inventoryList: document.querySelector("#inventory-list"),
    inventoryCount: document.querySelector("#inventory-count"), barterOffers: document.querySelector("#barter-offers"),
    didReadiness: document.querySelector("#did-readiness"), didTier: document.querySelector("#did-tier"),
    actionGrid: document.querySelector("#action-grid"), actionCount: document.querySelector("#action-count"),
    commandList: document.querySelector("#command-list"), commandForm: document.querySelector("#command-form"),
    commandInput: document.querySelector("#command-input"), resetButton: document.querySelector("#reset-button"),
    connection: document.querySelector(".connection"), connectionText: document.querySelector("#connection-text")
  };

  let snapshot = null;
  let busy = false;
  let loadGeneration = 0;
  let activeLoadController = null;
  let pendingNetworkRefresh = false;

  function safeStorageGet(key) {
    try { return window.localStorage.getItem(key); } catch { return null; }
  }

  function safeStorageSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch { /* storage can be disabled */ }
  }

  function cleanPlayerInput(value) {
    const cleaned = String(value).replace(/[\u0000-\u001F\u007F]/g, "").trim().slice(0, 256);
    return cleaned || "Traveler";
  }

  function cleanDisplayName(value) {
    const cleaned = String(value).replace(/[\u0000-\u001F\u007F]/g, " ").trim().replace(/\s+/g, " ");
    return cleaned.slice(0, 40) || "Traveler";
  }

  function setConnection(state, text) {
    ui.connection.classList.remove("online", "offline");
    if (state) ui.connection.classList.add(state);
    ui.connectionText.textContent = text;
  }

  function retryable(error) {
    if (error?.name === "AbortError") return false;
    if (typeof error?.status !== "number") return true;
    return [429, 502, 503, 504].includes(error.status);
  }

  async function requestJson(url, options = {}, retryGet = true, externalSignal = null) {
    const isGet = !options.method || options.method === "GET";
    const attempts = retryGet && isGet ? 3 : 1;
    let lastError;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const controller = new AbortController();
      const onAbort = () => controller.abort();
      externalSignal?.addEventListener("abort", onAbort, { once: true });
      const timeout = window.setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch(url, { ...options, signal: controller.signal, cache: "no-store" });
        const text = await response.text();
        let payload;
        try { payload = text ? JSON.parse(text) : {}; }
        catch { throw new Error(`The local server returned an unreadable response (HTTP ${response.status}).`); }
        if (!response.ok) {
          const error = new Error(payload.message || payload.error || `HTTP ${response.status}`);
          error.status = response.status;
          error.payload = payload;
          throw error;
        }
        return payload;
      } catch (error) {
        lastError = error;
        if (attempt + 1 >= attempts || !retryable(error) || externalSignal?.aborted) break;
        await new Promise(resolve => window.setTimeout(resolve, 250 * (2 ** attempt)));
      } finally {
        window.clearTimeout(timeout);
        externalSignal?.removeEventListener("abort", onAbort);
      }
    }
    throw lastError;
  }

  async function loadState(recoveryMessage = null) {
    const generation = ++loadGeneration;
    activeLoadController?.abort();
    const controller = new AbortController();
    activeLoadController = controller;
    setConnection("", "Connecting");
    try {
      const loaded = await requestJson(`/api/state?player=${encodeURIComponent(player)}&name=${encodeURIComponent(displayName)}`, {}, true, controller.signal);
      if (generation !== loadGeneration) return;
      snapshot = loaded;
      render(snapshot);
      if (recoveryMessage) ui.message.textContent = recoveryMessage;
      setConnection("online", "Local server online");
    } catch (error) {
      if (controller.signal.aborted || generation !== loadGeneration) return;
      snapshot = null;
      setConnection("offline", "Local server unavailable");
      renderConnectionFailure(error?.message || "The HUD could not load current options.");
    } finally {
      if (activeLoadController === controller) activeLoadController = null;
    }
  }

  function render(data) {
    const actions = Array.isArray(data.current_options) ? data.current_options : [];
    ui.worldTitle.textContent = data.world || "Beta Earth";
    ui.zoneName.textContent = (data.room?.zone || "Unknown zone").toUpperCase();
    ui.roomName.textContent = data.room?.name || "Unknown location";
    ui.roomDescription.textContent = data.room?.description || "No description available.";
    ui.roomAmbient.textContent = data.room?.ambient || "";
    ui.dangerChip.textContent = String(data.room?.danger || "unknown").toUpperCase();
    ui.message.textContent = data.message || "Choose a current option.";
    ui.playerStage.textContent = data.player?.stage || "—";
    ui.playerIdentity.textContent = data.player?.identity || "not set";
    ui.playerRevision.textContent = String(data.player?.revision ?? "—");
    ui.credBalance.textContent = String(data.economy?.cred ?? 0);
    renderStats(data.player?.stats || {});
    renderExits(data.room?.exits || []);
    renderQuestJournal(data.quest_journal || {});
    renderEconomy(data.economy || {});
    renderDIDReadiness(data.did_readiness || {});
    renderActions(actions);
    renderCommandText(actions);
  }

  function renderStats(stats) {
    ui.stats.replaceChildren();
    Object.entries(stats).forEach(([name, value]) => {
      const item = document.createElement("div"); item.className = "stat";
      const label = document.createElement("span"); label.textContent = name;
      const strong = document.createElement("strong"); strong.textContent = String(value);
      item.append(label, strong); ui.stats.append(item);
    });
  }

  function renderExits(exits) {
    ui.exits.replaceChildren();
    if (!Array.isArray(exits) || !exits.length) {
      const none = document.createElement("span"); none.className = "muted";
      none.textContent = "No visible exits in this state."; ui.exits.append(none); return;
    }
    exits.forEach(direction => {
      const chip = document.createElement("span"); chip.className = "exit-chip";
      chip.textContent = direction; ui.exits.append(chip);
    });
  }

  function renderQuestJournal(journal) {
    const active = Array.isArray(journal.active) ? journal.active : [];
    const completed = Array.isArray(journal.completed) ? journal.completed : [];
    ui.missionCount.textContent = `${active.length} ACTIVE`;
    ui.missionJournal.replaceChildren();

    const missionCard = (quest, isCompleted = false) => {
      const card = document.createElement("article"); card.className = `mission-card${isCompleted ? " completed" : ""}`;
      const titleRow = document.createElement("div"); titleRow.className = "mission-title-row";
      const titleWrap = document.createElement("div");
      const title = document.createElement("h3"); title.textContent = quest.title || "Untitled mission";
      const giver = document.createElement("p"); giver.className = "mission-giver"; giver.textContent = `Given by ${quest.giver || "Unknown"}`;
      titleWrap.append(title, giver);
      const status = document.createElement("span"); status.className = "mission-status";
      status.textContent = String(quest.status || (isCompleted ? "completed" : "active")).replaceAll("_", " ");
      titleRow.append(titleWrap, status); card.append(titleRow);

      if (!isCompleted && quest.tracer) {
        const tracer = document.createElement("div"); tracer.className = "tracer-box";
        const tracerLabel = document.createElement("strong"); tracerLabel.textContent = "Next trace";
        const instruction = document.createElement("p"); instruction.textContent = quest.tracer.instruction || "Continue the mission.";
        tracer.append(tracerLabel, instruction);
        if (quest.tracer.recommended_command) {
          const code = document.createElement("code"); code.className = "tracer-command"; code.tabIndex = 0;
          code.textContent = quest.tracer.recommended_command; code.title = "Selectable recommended command"; tracer.append(code);
        }
        const route = Array.isArray(quest.tracer.route) ? quest.tracer.route : [];
        if (route.length) {
          const wrap = document.createElement("div"); wrap.className = "route-trace";
          route.forEach(step => {
            const routeStep = document.createElement("span"); routeStep.className = "route-step";
            routeStep.textContent = `${step.direction} → ${step.room_name}`; wrap.append(routeStep);
          });
          tracer.append(wrap);
        }
        card.append(tracer);
      }

      const objectives = Array.isArray(quest.objectives) ? quest.objectives : [];
      const list = document.createElement("ul"); list.className = "objective-list";
      objectives.forEach(objective => {
        const item = document.createElement("li"); item.className = `objective${objective.complete ? " complete" : ""}`;
        if (objective.current && !objective.complete) item.setAttribute("aria-current", "step");
        const mark = document.createElement("span"); mark.className = "objective-mark"; mark.textContent = objective.complete ? "✓" : "•";
        const copy = document.createElement("span"); copy.textContent = objective.label || objective.description || "Objective";
        item.append(mark, copy); list.append(item);
      });
      if (objectives.length) card.append(list);
      if (quest.reward_summary) {
        const reward = document.createElement("p"); reward.className = "mission-reward";
        reward.textContent = `Reward: ${quest.reward_summary}`; card.append(reward);
      }
      return card;
    };

    active.forEach(quest => ui.missionJournal.append(missionCard(quest)));
    if (completed.length) {
      const details = document.createElement("details"); details.className = "completed-missions";
      const summary = document.createElement("summary"); summary.textContent = `${completed.length} completed mission${completed.length === 1 ? "" : "s"}`;
      details.append(summary); completed.forEach(quest => details.append(missionCard(quest, true))); ui.missionJournal.append(details);
    }
    if (!active.length && !completed.length) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.textContent = "No active mission. Current options remain available below."; ui.missionJournal.append(empty);
    }
  }

  function renderEconomy(economy) {
    const inventory = Array.isArray(economy.inventory) ? economy.inventory : [];
    const offers = Array.isArray(economy.room_offers) ? economy.room_offers : [];
    const total = inventory.reduce((sum, item) => sum + (Number(item.quantity) || 0), 0);
    ui.inventoryCount.textContent = `${total} ITEM${total === 1 ? "" : "S"}`;
    ui.inventoryList.replaceChildren();
    if (!inventory.length) {
      const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "Nothing carried yet.";
      ui.inventoryList.append(empty);
    } else {
      inventory.forEach(item => {
        const row = document.createElement("article"); row.className = "inventory-item";
        const heading = document.createElement("div"); heading.className = "inventory-heading";
        const name = document.createElement("strong"); name.textContent = item.name || item.item_id;
        const quantity = document.createElement("span"); quantity.className = "inventory-quantity"; quantity.textContent = `×${item.quantity}`;
        heading.append(name, quantity);
        const description = document.createElement("p"); description.textContent = item.description || "";
        row.append(heading, description); ui.inventoryList.append(row);
      });
    }
    ui.barterOffers.replaceChildren();
    offers.forEach(offer => {
      const row = document.createElement("div"); row.className = `barter-offer${offer.completed ? " completed" : ""}`;
      const title = document.createElement("strong"); title.textContent = offer.label || "Barter offer";
      const detail = document.createElement("p");
      detail.textContent = offer.completed ? `Completed — ${offer.grant_summary}` : `${offer.cost_cred} cred → ${offer.grant_summary}`;
      row.append(title, detail); ui.barterOffers.append(row);
    });
  }

  function renderDIDReadiness(readiness) {
    ui.didReadiness.replaceChildren();
    const tier = String(readiness.tier || "locked");
    ui.didTier.textContent = tier.replaceAll("_", " ").toUpperCase();
    const card = document.createElement("article"); card.className = "did-card";
    const title = document.createElement("strong"); title.textContent = readiness.label || "Locked";
    const summary = document.createElement("p"); summary.textContent = readiness.summary || "The DID readiness layer is not calibrated yet.";
    card.append(title, summary);

    const effects = document.createElement("p"); effects.className = "readiness-safety";
    effects.textContent = readiness.combat_modifiers_enabled ? "Combat modifiers active" : "Preview only — no combat modifiers enabled";
    card.append(effects);

    const reasons = Array.isArray(readiness.reasons) ? readiness.reasons : [];
    if (reasons.length) {
      const list = document.createElement("ul"); list.className = "readiness-reasons";
      reasons.forEach(reason => { const item = document.createElement("li"); item.textContent = reason; list.append(item); });
      card.append(list);
    }

    const slots = Array.isArray(readiness.slots) ? readiness.slots : [];
    if (slots.length) {
      const slotList = document.createElement("div"); slotList.className = "readiness-slots";
      slots.forEach(slot => {
        const row = document.createElement("div"); row.className = "readiness-slot";
        const heading = document.createElement("strong"); heading.textContent = slot.label || slot.slot_id || "Slot";
        const value = document.createElement("span"); value.textContent = slot.equipped_item_name || "Empty";
        const description = document.createElement("p"); description.textContent = slot.description || "Read-only preview slot.";
        row.append(heading, value, description); slotList.append(row);
      });
      card.append(slotList);
    }
    if (readiness.next_hint) {
      const hint = document.createElement("p"); hint.className = "did-hint"; hint.textContent = readiness.next_hint; card.append(hint);
    }
    ui.didReadiness.append(card);
  }

  function renderActions(actions) {
    ui.actionGrid.replaceChildren(); ui.actionCount.textContent = `${actions.length} OPTION${actions.length === 1 ? "" : "S"}`;
    if (!actions.length) { renderConnectionFailure("No current options were returned by the application service.", false); return; }
    actions.forEach(action => {
      const button = document.createElement("button"); button.type = "button";
      button.className = `action-button ${action.kind || "utility"}`; button.dataset.command = action.command;
      button.dataset.mission = action.mission_relevant ? "true" : "false"; button.disabled = busy || action.enabled === false;
      button.setAttribute("aria-label", `${action.label}. Command: ${action.command}`);
      const shortcut = document.createElement("span"); shortcut.className = "shortcut"; shortcut.textContent = action.shortcut ? String(action.shortcut) : "•";
      const copy = document.createElement("span"); copy.className = "action-copy";
      const label = document.createElement("strong"); label.textContent = action.label;
      const description = document.createElement("span"); description.textContent = action.description || action.command;
      copy.append(label, description); button.append(shortcut, copy);
      if (action.mission_relevant) { const tag = document.createElement("span"); tag.className = "mission-tag"; tag.textContent = "MISSION"; button.append(tag); }
      button.addEventListener("click", () => executeCommand(action.command)); ui.actionGrid.append(button);
    });
  }

  function renderCommandText(actions) {
    ui.commandList.replaceChildren();
    actions.forEach(action => {
      const row = document.createElement("div"); row.className = "command-item";
      const execute = document.createElement("button"); execute.type = "button"; execute.className = "command-execute";
      execute.textContent = "Execute"; execute.disabled = busy || action.enabled === false;
      execute.addEventListener("click", () => executeCommand(action.command));
      const code = document.createElement("code"); code.tabIndex = 0; code.textContent = action.command;
      code.title = "Selectable command text"; row.append(execute, code); ui.commandList.append(row);
    });
  }

  function renderConnectionFailure(message, showRetry = true) {
    ui.actionGrid.replaceChildren(); ui.commandList.replaceChildren(); ui.actionCount.textContent = "0 OPTIONS";
    const card = document.createElement("div"); card.className = "loading-card"; card.textContent = message;
    if (showRetry) {
      const retry = document.createElement("button"); retry.type = "button"; retry.className = "retry-button";
      retry.textContent = "Retry local connection"; retry.addEventListener("click", () => loadState()); card.append(retry);
    }
    ui.actionGrid.append(card);
  }

  async function executeCommand(command) {
    if (busy || !snapshot) return;
    busy = true; renderActions(snapshot.current_options || []); renderCommandText(snapshot.current_options || []);
    ui.message.textContent = `Executing: ${command}`;
    try {
      const next = await requestJson("/api/command", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ player, command, expected_revision: snapshot.player.revision })
      }, false);
      snapshot = next; render(snapshot); setConnection("online", "Local server online");
    } catch (error) {
      if (error.status === 409 && error.payload?.state) {
        snapshot = error.payload.state; render(snapshot);
        ui.message.textContent = "State changed in another request. The HUD refreshed without replaying your command.";
      } else {
        pendingNetworkRefresh = false;
        await loadState(`${error.message || "Command failed"} Current state refreshed; the command was not replayed automatically.`);
      }
    } finally {
      busy = false;
      if (snapshot) { renderActions(snapshot.current_options || []); renderCommandText(snapshot.current_options || []); }
      flushPendingNetworkRefresh();
    }
  }

  ui.commandForm.addEventListener("submit", event => {
    event.preventDefault(); const command = ui.commandInput.value.trim(); if (!command) return;
    ui.commandInput.value = ""; executeCommand(command);
  });

  ui.resetButton.addEventListener("click", async () => {
    if (busy || !snapshot || !window.confirm("Reset only this local player profile and restart character creation?")) return;
    busy = true; ui.resetButton.disabled = true;
    try {
      snapshot = await requestJson("/api/reset", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ player, name: displayName, expected_revision: snapshot.player.revision })
      }, false);
      render(snapshot); setConnection("online", "Local server online");
    } catch (error) {
      if (error.status === 409 && error.payload?.state) {
        snapshot = error.payload.state; render(snapshot);
        ui.message.textContent = "State changed in another request. Reset was not applied; the HUD refreshed safely.";
      } else {
        pendingNetworkRefresh = false;
        await loadState(`${error.message || "Reset response was uncertain."} Current state refreshed without replaying reset.`);
      }
    } finally {
      busy = false; ui.resetButton.disabled = false;
      if (snapshot) { renderActions(snapshot.current_options || []); renderCommandText(snapshot.current_options || []); }
      flushPendingNetworkRefresh();
    }
  });

  window.addEventListener("keydown", event => {
    const target = event.target;
    const typing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target?.isContentEditable;
    if (typing || event.ctrlKey || event.altKey || event.metaKey || busy || !snapshot) return;
    const index = Number(event.key) - 1; const actions = snapshot.current_options || [];
    if (index >= 0 && index < Math.min(actions.length, 9)) { event.preventDefault(); executeCommand(actions[index].command); }
  });

  function flushPendingNetworkRefresh() {
    if (!pendingNetworkRefresh || busy) return;
    pendingNetworkRefresh = false;
    void loadState("Network connection returned. Current state refreshed after the active command finished.");
  }

  window.addEventListener("online", () => {
    if (busy) {
      pendingNetworkRefresh = true;
      setConnection("", "Network returned; state refresh queued");
      return;
    }
    void loadState("Network connection returned. Current state refreshed.");
  });
  window.addEventListener("offline", () => setConnection("offline", "Browser reports offline"));
  loadState();
})();
