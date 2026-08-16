/*
Asset-ID: BE-NEXT-HUD-APPLICATION
Version: 0.51.1
Copyright © 2026 Gateway Information Group LLC. All rights reserved.
*/

"use strict";

const token = document.querySelector('meta[name="beta-earth-token"]').content;
const app = {
  state: null,
  commands: [],
  history: [],
  historyIndex: 0,
  requestPending: false,
  autoscroll: true,
  recoveryReceivedAt: 0,
  recoveryBase: 0,
  recoveryInitial: 1,
  transcriptCount: 0,
  onboardingStep: 1,
  onboardingVisible: false,
  onboardingMessage: "",
  transcriptFilter: "all",
  commandEcho: true,
  rightTab: "tactical",
  combatProjection: "tactical",
  commandTrayExpanded: false,
  lastTacticalAnnouncement: "",
  paletteItems: [],
  paletteIndex: 0,
  hudPreset: "guided",
  introMusicWanted: true,
  introMusicUserPaused: false,
  introMusicFadeToken: 0,
  sfxMuted: false,
  sfxVolume: 0.24,
  sfxInteractions: true,
  sfxFeedback: true,
  sfxPools: Object.create(null),
  sfxPoolCursor: Object.create(null),
  sfxLastPlayed: Object.create(null),
  sfxPreviewToken: 0,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
const SFX_LIBRARY = Object.freeze({
  tick: "/media/sfx/signal-tick.wav",
  select: "/media/sfx/signal-select.wav",
  confirm: "/media/sfx/signal-confirm.wav",
  warning: "/media/sfx/signal-warning.wav",
  error: "/media/sfx/signal-error.wav",
  impact: "/media/sfx/signal-impact.wav",
  recovery: "/media/sfx/signal-recovery.wav",
});
const SFX_MIN_GAP_MS = Object.freeze({
  tick: 42,
  select: 75,
  confirm: 180,
  warning: 180,
  error: 180,
  impact: 105,
  recovery: 180,
});
const titleCase = (value) => String(value || "")
  .replaceAll("_", " ")
  .replace(/\b\w/g, (character) => character.toUpperCase());
const quoteArg = (value) => `"${String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;


const INTENT_LABELS = Object.freeze({
  attack: "attack",
  rush: "rush",
  heavy_attack: "heavy strike",
  aimed_attack: "aimed strike",
  reposition: "reposition",
  evade: "evasive move",
  brace: "brace",
  protect_ally: "protect an ally",
  covering_fire: "covering fire",
  heal_ally: "repair an ally",
  support_heal: "repair an ally",
  suppress: "suppressive action",
  interrupt: "interruption",
  charged_attack: "charged attack",
  control: "control action",
  rally: "rally allies",
  commander_order: "formation order",
  balanced_setup: "Akari-line setup",
  guard_intercept: "guard intercept",
  assault_strike: "assault strike",
  recover: "recover",
});

function intentLabel(value) {
  const key = String(value || "").trim().toLowerCase();
  return INTENT_LABELS[key] || titleCase(key || "ready");
}

function battlefieldActors(state) {
  return Array.isArray(state?.battlefield?.actors) ? state.battlefield.actors : [];
}

function actorTiming(actor, projection = app.combatProjection) {
  const seconds = Math.max(0, Number(actor?.ready_in_field_seconds || 0));
  const text = String(actor?.timing_text || (seconds <= 0 ? "ready" : "recovering"));
  if (projection === "audit") return `${seconds.toFixed(1)} field sec`;
  if (projection === "tactical") return seconds <= 0 ? "ready" : `~${Math.max(1, Math.round(seconds))}s`;
  return text === "imminent" ? "now" : text;
}

function actorEffects(actor) {
  return (Array.isArray(actor?.effects) ? actor.effects : []).map((effect) => {
    const name = titleCase(effect?.name || "effect");
    if (app.combatProjection !== "audit") return name;
    const details = [];
    const magnitude = Number(effect?.magnitude || 0);
    const uses = Number(effect?.uses_remaining || 0);
    const seconds = Number(effect?.remaining_field_seconds || 0);
    if (magnitude) details.push(String(magnitude));
    if (uses) details.push(`${uses} use${uses === 1 ? "" : "s"}`);
    if (seconds) details.push(`${seconds.toFixed(1)}s`);
    return details.length ? `${name} · ${details.join(" · ")}` : name;
  });
}

function actorForCreature(state, creature) {
  const instanceId = String(creature?.instance_id || "");
  return battlefieldActors(state).find((actor) =>
    actor.actor_id === `creature:${instanceId}`
      || actor.actor_id === instanceId
      || (actor.kind === "creature" && actor.name === creature?.name)
  ) || creature?.battlefield || null;
}

function companionBattlefieldActor(state) {
  return battlefieldActors(state).find((actor) => actor.kind === "companion") || null;
}

function factionById(foundation, factionId) {
  return (foundation?.factions || []).find((faction) => faction.id === factionId) || null;
}

function standingText(value, label = "") {
  const numeric = Number(value || 0);
  return `${numeric >= 0 ? "+" : ""}${numeric}${label ? ` · ${label}` : ""}`;
}

function setTerritoryMetric(name, value) {
  const numeric = clamp(Number(value || 0), 0, 100);
  const valueNode = $(`#sprawl-${name}`) || $(`#territory-${name}`);
  if (valueNode) valueNode.textContent = String(Math.round(numeric));
  const metric = $(`[data-sprawl-metric="${name}"]`) || $(`[data-territory-metric="${name}"]`);
  if (!metric) return;
  const meter = $(".meter", metric);
  const fill = meter ? $("span", meter) : null;
  if (fill) fill.style.width = `${numeric}%`;
  meter?.setAttribute("aria-valuenow", String(Math.round(numeric)));
  meter?.setAttribute("aria-label", `${titleCase(name)} ${numeric} of 100`);
  metric.dataset.pressure = name === "tension"
    ? numeric >= 80 ? "critical" : numeric >= 55 ? "strained" : "stable"
    : numeric <= 20 ? "low" : numeric >= 70 ? "strong" : "steady";
}

function setText(selector, value, root = document) {
  const element = $(selector, root);
  if (element) element.textContent = String(value);
}

function setMeter(selector, percentage, valueText = "") {
  const meter = $(selector);
  if (!meter) return;
  const value = Math.round(clamp(Number(percentage) || 0, 0, 100));
  const fill = $("span", meter);
  if (fill) fill.style.width = `${value}%`;
  meter.setAttribute("aria-valuenow", String(value));
  if (valueText) meter.setAttribute("aria-valuetext", valueText);
  else meter.removeAttribute("aria-valuetext");
}

function clearNode(node) {
  while (node?.firstChild) node.removeChild(node.firstChild);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function button(text, command, className = "") {
  const node = element("button", className, text);
  node.type = "button";
  if (command) node.dataset.command = command;
  return node;
}

function emptyNote(text) {
  return element("span", "empty-note", text);
}

function currentTime() {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

function appendTranscript(text, kind = "world", stream = null) {
  if (!text) return;
  const transcript = $("#transcript");
  const entry = element("div", `transcript-entry ${kind}`);
  entry.dataset.stream = stream || (kind === "error" || kind === "system" ? "system" : "world");
  const time = element("time", "", currentTime());
  time.dateTime = new Date().toISOString();
  const pre = element("pre", "", text);
  entry.append(time, pre);
  transcript.append(entry);
  app.transcriptCount += 1;
  while (app.transcriptCount > 200 && transcript.firstElementChild) {
    transcript.firstElementChild.remove();
    app.transcriptCount -= 1;
  }
  applyTranscriptFilter();
  if (app.autoscroll && !entry.hidden) transcript.scrollTop = transcript.scrollHeight;
}

function classifyCommand(raw) {
  const command = String(raw || "").trim().toLowerCase();
  const verb = command.split(/\s+/)[0] || "";
  if ([
    "attack", "assess", "target", "ability", "technique", "stance", "defense",
    "withdraw", "stand", "stabilize", "rest", "health", "injury", "recover",
  ].includes(verb) || command.startsWith("companion order")) return "combat";
  if (["save", "quit", "playtest", "help", "commands", "diagnostics"].includes(verb)) return "system";
  return "world";
}

function applyTranscriptFilter() {
  const filter = app.transcriptFilter || "all";
  $$("#transcript .transcript-entry").forEach((entry) => {
    entry.hidden = filter !== "all" && entry.dataset.stream !== filter;
  });
  $$('[data-transcript-filter]').forEach((node) => {
    const selected = node.dataset.transcriptFilter === filter;
    node.setAttribute("aria-selected", String(selected));
    node.classList.toggle("active", selected);
    node.tabIndex = selected ? 0 : -1;
  });
}

function toast(message, kind = "") {
  const stack = $("#toast-stack");
  const node = element("div", `toast ${kind}`.trim(), message);
  stack.append(node);
  window.setTimeout(() => node.remove(), 4200);
}

function setSync(mode, label = "") {
  const status = $("#sync-status");
  status.classList.toggle("busy", mode === "busy");
  status.classList.toggle("error", mode === "error");
  setText("#sync-label", label || (mode === "busy" ? "SAVING" : mode === "error" ? "CHECK SAVE" : "SAVED"));
  setText("#save-label", mode === "busy" ? "WORKING…" : mode === "error" ? "SAVE NEEDS ATTENTION" : "AUTOSAVE CURRENT");
}

function setRequestPending(pending) {
  app.requestPending = pending;
  document.body.classList.toggle("request-pending", pending);
  $("#hud-shell").setAttribute("aria-busy", String(pending));
  $("#onboarding-screen").setAttribute("aria-busy", String(pending));
  $("#onboarding-screen").inert = pending;
  if (app.state) renderOnboarding(app.state);
}

function setupIsRequired() {
  return app.state?.character?.build?.status === "pending";
}

function focusSetupStep() {
  if (!app.onboardingVisible || app.requestPending) return;
  const selector = app.onboardingStep === 1
    ? "#class-select"
    : app.onboardingStep === 2
      ? "#use-recommended-build"
      : "#choose-guided-start";
  window.requestAnimationFrame(() => $(selector)?.focus());
}

function focusCurrentSurface() {
  if (setupIsRequired()) focusSetupStep();
  else $("#command-input")?.focus();
}

async function api(path, payload = null, method = "POST") {
  const options = {
    method,
    headers: {
      "X-Beta-Earth-Token": token,
    },
  };
  if (payload !== null) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(path, options);
  let document;
  try {
    document = await response.json();
  } catch {
    throw new Error(`Local HUD returned an unreadable response (${response.status}).`);
  }
  if (!response.ok || !document.ok) {
    throw new Error(document.error || `Local HUD request failed (${response.status}).`);
  }
  return document;
}


function introAudioElement() {
  return $("#intro-music");
}

function updateIntroMusicControls(status = "") {
  const audio = introAudioElement();
  if (!audio) return;
  const playing = !audio.paused && !audio.ended;
  const toggle = $("#intro-music-toggle");
  toggle?.setAttribute("aria-pressed", String(playing));
  toggle?.setAttribute("aria-label", playing ? "Pause intro music" : "Play intro music");
  setText("#intro-music-toggle-icon", playing ? "❚❚" : "▶");
  setText("#intro-music-toggle-label", playing ? "Pause" : "Play");

  const muted = Boolean(audio.muted);
  const mute = $("#intro-music-mute");
  mute?.setAttribute("aria-pressed", String(muted));
  mute?.setAttribute("aria-label", muted ? "Unmute intro music" : "Mute intro music");
  setText("#intro-music-mute-icon", muted ? "×" : "◖");
  setText("#intro-music-mute-label", muted ? "Unmute" : "Mute");

  const percent = Math.round(clamp(Number(audio.volume) * 100, 0, 100));
  const volume = $("#intro-music-volume");
  if (volume && Number(volume.value) !== percent) volume.value = String(percent);
  volume?.setAttribute("aria-valuenow", String(percent));
  volume?.setAttribute("aria-valuetext", `${percent} percent`);
  setText("#intro-music-volume-value", `${percent}%`);
  if (status) setText("#intro-music-status", status);
}

function setIntroMusicVolume(value, store = true) {
  const audio = introAudioElement();
  if (!audio) return;
  const percent = Math.round(clamp(Number(value) || 0, 0, 100));
  audio.volume = percent / 100;
  if (store) storePreference("intro-music-volume", percent);
  updateIntroMusicControls();
}

function setIntroMusicMuted(muted, store = true) {
  const audio = introAudioElement();
  if (!audio) return;
  audio.muted = Boolean(muted);
  if (store) storePreference("intro-music-muted", audio.muted);
  updateIntroMusicControls(audio.muted ? "Muted · intro continues silently" : audio.paused ? "Ready — press Play" : "Playing · loops during setup");
}

async function playIntroMusic({ store = true, userInitiated = false } = {}) {
  const audio = introAudioElement();
  const dock = $("#intro-audio-dock");
  if (!audio || !dock || dock.hidden) return false;
  app.introMusicWanted = true;
  app.introMusicUserPaused = false;
  if (store) storePreference("intro-music-enabled", true);
  try {
    await audio.play();
    updateIntroMusicControls(audio.muted ? "Playing muted · use Unmute when ready" : "Playing · loops during setup");
    return true;
  } catch {
    updateIntroMusicControls(
      userInitiated
        ? "Playback was blocked · press Play to start"
        : "Ready — begins after your first interaction",
    );
    return false;
  }
}

function pauseIntroMusic({ store = true, status = "Paused" } = {}) {
  const audio = introAudioElement();
  if (!audio) return;
  audio.pause();
  app.introMusicWanted = false;
  app.introMusicUserPaused = true;
  if (store) storePreference("intro-music-enabled", false);
  updateIntroMusicControls(status);
}

function showIntroMusicDock() {
  const dock = $("#intro-audio-dock");
  if (!dock) return;
  app.introMusicFadeToken += 1;
  document.body.classList.remove("intro-audio-fading");
  dock.hidden = false;
}

function fadeOutIntroMusic() {
  const audio = introAudioElement();
  const dock = $("#intro-audio-dock");
  if (!audio || !dock || dock.hidden || document.body.classList.contains("intro-audio-fading")) return;
  const token = ++app.introMusicFadeToken;
  const storedVolumeValue = Number(readStoredPreference("intro-music-volume", "35"));
  const storedVolume = clamp(Number.isFinite(storedVolumeValue) ? storedVolumeValue : 35, 0, 100) / 100;
  const reducedMotion = document.body.classList.contains("reduced-motion");
  document.body.classList.add("intro-audio-fading");

  const finish = () => {
    if (token !== app.introMusicFadeToken) return;
    audio.pause();
    audio.currentTime = 0;
    audio.volume = storedVolume;
    dock.hidden = true;
    document.body.classList.remove("intro-audio-fading");
    updateIntroMusicControls("Intro complete");
  };

  if (audio.paused || reducedMotion || storedVolume === 0) {
    window.setTimeout(finish, reducedMotion ? 0 : 250);
    return;
  }
  const startVolume = audio.volume;
  const started = performance.now();
  const duration = 900;
  const step = (now) => {
    if (token !== app.introMusicFadeToken) return;
    const progress = clamp((now - started) / duration, 0, 1);
    audio.volume = startVolume * (1 - progress);
    if (progress < 1) window.requestAnimationFrame(step);
    else finish();
  };
  window.requestAnimationFrame(step);
}

function initializeIntroMusic() {
  const audio = introAudioElement();
  const dock = $("#intro-audio-dock");
  if (!audio || !dock) return;
  showIntroMusicDock();
  const storedVolume = Number(readStoredPreference("intro-music-volume", "35"));
  const volume = clamp(Number.isFinite(storedVolume) ? storedVolume : 35, 0, 100);
  const muted = readStoredPreference("intro-music-muted", "false") === "true";
  const enabled = readStoredPreference("intro-music-enabled", "true") !== "false";
  setIntroMusicVolume(volume, false);
  setIntroMusicMuted(muted, false);
  app.introMusicWanted = enabled;
  app.introMusicUserPaused = !enabled;
  updateIntroMusicControls(enabled ? "Ready — begins after your first interaction" : "Paused · preference saved");

  if (enabled) playIntroMusic({ store: false, userInitiated: false });

  const unlock = async (event) => {
    if (!app.introMusicWanted || app.introMusicUserPaused || dock.hidden || !audio.paused) return;
    if (event.target instanceof Element && event.target.closest("#intro-audio-dock")) return;
    const started = await playIntroMusic({ store: false, userInitiated: true });
    if (started) {
      document.removeEventListener("pointerdown", unlock, true);
      document.removeEventListener("keydown", unlock, true);
    }
  };
  document.addEventListener("pointerdown", unlock, true);
  document.addEventListener("keydown", unlock, true);

  audio.addEventListener("play", () => updateIntroMusicControls(audio.muted ? "Playing muted · use Unmute when ready" : "Playing · loops during setup"));
  audio.addEventListener("pause", () => {
    if (!dock.hidden && !document.body.classList.contains("intro-audio-fading")) {
      updateIntroMusicControls(app.introMusicUserPaused ? "Paused · preference saved" : "Ready — press Play");
    }
  });
  audio.addEventListener("error", () => updateIntroMusicControls("Intro music could not be loaded"));
}

function sfxCategoryEnabled(category) {
  if (category === "interaction") return app.sfxInteractions;
  return app.sfxFeedback;
}

function sfxStatusText() {
  const percent = Math.round(app.sfxVolume * 100);
  if (app.sfxMuted) return "Muted · preferences remain saved locally";
  if (percent === 0) return "Effects volume 0% · increase volume to hear cues";
  if (!app.sfxInteractions && !app.sfxFeedback) return "Interaction and feedback cues disabled";
  const categories = [
    app.sfxInteractions ? "interactions" : null,
    app.sfxFeedback ? "gameplay feedback" : null,
  ].filter(Boolean).join(" + ");
  return `${titleCase(categories)} · ${percent}%`;
}

function updateSfxControls(status = "") {
  const percent = Math.round(clamp(app.sfxVolume * 100, 0, 100));
  for (const selector of ["#intro-sfx-volume", "#settings-sfx-volume"]) {
    const control = $(selector);
    if (!control) continue;
    if (Number(control.value) !== percent) control.value = String(percent);
    control.setAttribute("aria-valuenow", String(percent));
    control.setAttribute("aria-valuetext", `${percent} percent`);
  }
  setText("#intro-sfx-volume-value", `${percent}%`);
  setText("#settings-sfx-volume-value", `${percent}%`);

  const introMute = $("#intro-sfx-mute");
  introMute?.setAttribute("aria-pressed", String(app.sfxMuted));
  introMute?.setAttribute("aria-label", app.sfxMuted ? "Unmute interface effects" : "Mute interface effects");
  setText("#intro-sfx-mute-icon", app.sfxMuted ? "×" : "◖");
  setText("#intro-sfx-mute-label", app.sfxMuted ? "Unmute" : "Mute");

  const settingsMute = $("#settings-sfx-mute");
  settingsMute?.setAttribute("aria-pressed", String(app.sfxMuted));
  setText("#settings-sfx-mute-label", app.sfxMuted ? "Unmute effects" : "Mute effects");

  const interaction = $("#sfx-interaction-toggle");
  if (interaction) interaction.checked = app.sfxInteractions;
  const feedback = $("#sfx-feedback-toggle");
  if (feedback) feedback.checked = app.sfxFeedback;

  const message = status || sfxStatusText();
  setText("#intro-sfx-status", message);
  setText("#settings-sfx-status", message);
}

function forEachSfxVoice(callback) {
  for (const pool of Object.values(app.sfxPools)) {
    for (const voice of pool) callback(voice);
  }
}

function stopSfx() {
  app.sfxPreviewToken += 1;
  forEachSfxVoice((voice) => {
    voice.pause();
    try {
      voice.currentTime = 0;
    } catch {
      // A browser may reject seeking before metadata is available. Pausing is enough.
    }
  });
}

function setSfxVolume(value, store = true) {
  const percent = Math.round(clamp(Number(value) || 0, 0, 100));
  app.sfxVolume = percent / 100;
  forEachSfxVoice((voice) => {
    const gain = Number(voice.dataset.sfxGain || 1);
    voice.volume = clamp(app.sfxVolume * gain, 0, 1);
  });
  if (percent === 0) stopSfx();
  if (store) storePreference("sfx-volume", percent);
  updateSfxControls();
}

function setSfxMuted(muted, store = true) {
  app.sfxMuted = Boolean(muted);
  if (app.sfxMuted) stopSfx();
  if (store) storePreference("sfx-muted", app.sfxMuted);
  updateSfxControls();
}

function setSfxCategory(category, enabled, store = true) {
  const active = Boolean(enabled);
  if (category === "interaction") app.sfxInteractions = active;
  else app.sfxFeedback = active;
  if (store) storePreference(`sfx-${category}-enabled`, active);
  updateSfxControls();
}

function sfxPool(cue) {
  if (!SFX_LIBRARY[cue]) return [];
  if (!app.sfxPools[cue]) {
    app.sfxPools[cue] = Array.from({ length: cue === "tick" || cue === "impact" ? 4 : 2 }, () => {
      const voice = new Audio(SFX_LIBRARY[cue]);
      voice.preload = "auto";
      voice.playsInline = true;
      return voice;
    });
    app.sfxPoolCursor[cue] = 0;
  }
  return app.sfxPools[cue];
}

function playSfx(cue, { category = "feedback", gain = 1, playbackRate = 1, bypassThrottle = false } = {}) {
  if (!SFX_LIBRARY[cue] || app.sfxMuted || app.sfxVolume <= 0 || !sfxCategoryEnabled(category)) return false;
  const now = performance.now();
  const minimumGap = Number(SFX_MIN_GAP_MS[cue] || 80);
  if (!bypassThrottle && now - Number(app.sfxLastPlayed[cue] || 0) < minimumGap) return false;
  app.sfxLastPlayed[cue] = now;

  const pool = sfxPool(cue);
  if (!pool.length) return false;
  let voice = pool.find((candidate) => candidate.paused || candidate.ended);
  if (!voice) {
    const cursor = Number(app.sfxPoolCursor[cue] || 0) % pool.length;
    voice = pool[cursor];
    app.sfxPoolCursor[cue] = (cursor + 1) % pool.length;
    voice.pause();
  }
  try {
    voice.currentTime = 0;
    voice.playbackRate = clamp(Number(playbackRate) || 1, 0.65, 1.5);
    voice.dataset.sfxGain = String(clamp(Number(gain || 1), 0, 2));
    voice.volume = clamp(app.sfxVolume * Number(voice.dataset.sfxGain), 0, 1);
    const attempt = voice.play();
    if (attempt?.catch) attempt.catch(() => updateSfxControls("Effects ready · browser needs one normal interaction"));
    return true;
  } catch {
    updateSfxControls("Effects unavailable in this browser session");
    return false;
  }
}

function previewSfx() {
  if (app.sfxMuted) {
    updateSfxControls("Muted · unmute to preview effects");
    return;
  }
  if (!app.sfxInteractions && !app.sfxFeedback) {
    updateSfxControls("Enable an effect category to preview it");
    return;
  }
  const token = ++app.sfxPreviewToken;
  updateSfxControls("Previewing the current effect mix");
  if (app.sfxInteractions) playSfx("tick", { category: "interaction", gain: 0.78, bypassThrottle: true });
  window.setTimeout(() => {
    if (token !== app.sfxPreviewToken) return;
    if (app.sfxInteractions) playSfx("select", { category: "interaction", gain: 0.82, bypassThrottle: true });
  }, 125);
  window.setTimeout(() => {
    if (token !== app.sfxPreviewToken) return;
    if (app.sfxFeedback) playSfx("confirm", { category: "feedback", gain: 0.88, bypassThrottle: true });
    window.setTimeout(() => {
      if (token === app.sfxPreviewToken) updateSfxControls();
    }, 340);
  }, 300);
}

function initializeSfx() {
  const storedVolume = Number(readStoredPreference("sfx-volume", "24"));
  app.sfxVolume = clamp(Number.isFinite(storedVolume) ? storedVolume : 24, 0, 100) / 100;
  app.sfxMuted = readStoredPreference("sfx-muted", "false") === "true";
  app.sfxInteractions = readStoredPreference("sfx-interaction-enabled", "true") !== "false";
  app.sfxFeedback = readStoredPreference("sfx-feedback-enabled", "true") !== "false";
  Object.keys(SFX_LIBRARY).forEach((cue) => sfxPool(cue));
  updateSfxControls();
}

function storyStateKey(state) {
  const story = state?.story || {};
  return `${story.quest_id || ""}:${story.stage_id || ""}:${(story.completed_quests || []).length}`;
}

function completedCompetencyCount(state) {
  const foundation = state?.beginner_experience?.foundation || {};
  return Number(foundation.completed_competencies || 0);
}

function creatureHealthMap(state) {
  return new Map((state?.room?.creatures || []).map((creature) => [
    creature.instance_id || creature.id || creature.name,
    Number(creature.health || 0),
  ]));
}

function commandOutputLooksRejected(output) {
  const normalized = String(output || "").trim().toLowerCase();
  if (!normalized) return false;
  return [
    "unknown command",
    "no one here matches",
    "nothing here matches",
    "you cannot",
    "you can't",
    "you need ",
    "not enough ",
    "that action is unavailable",
    "that route is unavailable",
    "there is no exit",
    "you are still recovering",
    "you must ",
  ].some((phrase) => normalized.includes(phrase));
}

function playStateFeedback(previousState, nextState, command, output = "") {
  if (!previousState || !nextState || !app.sfxFeedback) return;
  if (commandOutputLooksRejected(output)) {
    playSfx("error", { category: "feedback", gain: 0.76 });
    return;
  }
  const previousCharacter = previousState.character || {};
  const nextCharacter = nextState.character || {};
  const previousLevel = Number(previousCharacter.level || 0);
  const nextLevel = Number(nextCharacter.level || 0);
  const previousCompleted = Number((previousState.story?.completed_quests || []).length);
  const nextCompleted = Number((nextState.story?.completed_quests || []).length);
  const previousCompetencies = completedCompetencyCount(previousState);
  const nextCompetencies = completedCompetencyCount(nextState);

  if (nextLevel > previousLevel || nextCompleted > previousCompleted || nextCompetencies > previousCompetencies) {
    playSfx("confirm", { category: "feedback", gain: 0.92 });
    return;
  }

  const previousHealth = Number(previousCharacter.health || 0);
  const nextHealth = Number(nextCharacter.health || 0);
  const maxHealth = Math.max(1, Number(nextCharacter.max_health || previousCharacter.max_health || 1));
  const previousInjury = Boolean(previousState.difficulty_curve?.injury?.active);
  const nextInjury = Boolean(nextState.difficulty_curve?.injury?.active);
  if ((!previousInjury && nextInjury) || nextHealth < previousHealth - Math.max(2, maxHealth * 0.06)) {
    playSfx("warning", { category: "feedback", gain: 0.92 });
    return;
  }

  const previousCreatures = creatureHealthMap(previousState);
  const nextCreatures = creatureHealthMap(nextState);
  if (nextCreatures.size < previousCreatures.size) {
    playSfx("confirm", { category: "feedback", gain: 0.72, playbackRate: 0.92 });
    return;
  }
  for (const [id, health] of nextCreatures) {
    if (previousCreatures.has(id) && health < previousCreatures.get(id)) {
      playSfx("impact", { category: "feedback", gain: 0.74 });
      return;
    }
  }

  if (nextHealth > previousHealth + 1) {
    playSfx("recovery", { category: "feedback", gain: 0.78 });
    return;
  }
  if (storyStateKey(previousState) !== storyStateKey(nextState)) {
    playSfx("select", { category: "feedback", gain: 0.72, playbackRate: 1.05 });
    return;
  }
  const previousRoom = previousState.room?.id || previousState.room?.room_id || previousState.room?.title;
  const nextRoom = nextState.room?.id || nextState.room?.room_id || nextState.room?.title;
  if (previousRoom && nextRoom && previousRoom !== nextRoom) {
    playSfx("select", { category: "feedback", gain: 0.62 });
    return;
  }
  if (String(command || "").trim().toLowerCase() === "save") {
    playSfx("confirm", { category: "feedback", gain: 0.62 });
  }
}

async function openSession(player) {
  const submit = $("#session-form button");
  submit.disabled = true;
  setText("#startup-message", "");
  try {
    const document = await api("/api/session", { player });
    app.commands = document.commands || [];
    $("#startup-screen").hidden = true;
    $("#hud-shell").hidden = false;
    appendTranscript(document.output, "system");
    applyState(document.state);
    if (document.created) {
      toast("New local field record created. Character Foundation is ready.");
    }
    focusCurrentSurface();
  } catch (error) {
    setText("#startup-message", error.message);
  } finally {
    submit.disabled = false;
  }
}

async function resumeOpenSession() {
  try {
    const document = await api("/api/status", null, "GET");
    if (!document.started) return;
    app.commands = document.commands || [];
    $("#startup-screen").hidden = true;
    $("#hud-shell").hidden = false;
    appendTranscript(
      `Field link restored for ${document.state.character.name}.\n\n${document.state.room.title}\n${document.state.room.description}`,
      "system",
    );
    applyState(document.state);
    focusCurrentSurface();
  } catch (error) {
    setText("#startup-message", error.message);
  }
}

async function issueCommand(command) {
  const raw = String(command || "").trim();
  if (!raw || app.requestPending) return;
  const previousState = app.state;
  const stream = classifyCommand(raw);
  app.onboardingMessage = "";
  setRequestPending(true);
  setSync("busy");
  if (app.commandEcho) appendTranscript(raw, "command", stream);
  if (app.history.at(-1) !== raw) app.history.push(raw);
  if (app.history.length > 100) app.history.shift();
  app.historyIndex = app.history.length;
  $("#command-input").value = "";
  try {
    const document = await api("/api/command", { command: raw });
    appendTranscript(document.output, document.quit ? "system" : "world", document.quit ? "system" : stream);
    applyState(document.state);
    playStateFeedback(previousState, document.state, raw, document.output);
    setSync("ready");
    if (document.quit) {
      toast("Progress saved. Closing the local field link.");
      window.setTimeout(shutdown, 350);
    }
  } catch (error) {
    appendTranscript(error.message, "error", "system");
    playSfx("error", { category: "feedback", gain: 0.82 });
    toast(error.message, "error");
    if (setupIsRequired()) app.onboardingMessage = error.message;
    setSync("error");
  } finally {
    setRequestPending(false);
    focusCurrentSurface();
  }
}

async function shutdown() {
  stopSfx();
  try {
    await api("/api/shutdown", {});
  } catch {
    // The local process may close before the browser receives the final response.
  }
  document.body.classList.add("link-closed");
  setText("#sync-label", "CLOSED");
  setText("#save-label", "FIELD LINK CLOSED");
  $("#command-input").disabled = true;
  $$(".command-deck button, .hud-grid button, .hud-grid select").forEach((node) => {
    node.disabled = true;
  });
}

function applyState(state) {
  app.state = state;
  app.recoveryReceivedAt = performance.now();
  app.recoveryBase = Math.max(
    Number(state.recovery?.remaining_seconds || 0),
    Number(state.recovery?.roundtime_seconds || 0),
    Number(state.recovery?.stun_seconds || 0),
  );
  app.recoveryInitial = Math.max(1, app.recoveryBase);
  renderTopbar(state);
  renderCharacter(state);
  renderWorld(state);
  renderNavigation(state);
  renderCombat(state);
  renderBattlefield(state);
  renderCombatPartner(state);
  renderWithdrawal(state);
  renderTechnique(state);
  renderSpecialization(state);
  renderInventory(state);
  renderEconomy(state);
  renderFoundation(state);
  renderBeginnerExperience(state);
  renderPlaytest(state);
  renderProgress(state);
  renderParty(state);
  renderReport(state);
  renderDistrict(state);
  renderService(state);
  renderHospice(state);
  renderSupportExport(state);
  renderDirective(state);
  renderContextToolbar(state);
  renderHudFocus(state);
  renderOnboarding(state);
  renderRecovery();
  rebuildCommandPalette();
}

function renderTopbar(state) {
  setText("#top-location", state.room?.title || "Unknown location");
  setText("#revision-label", `SAVE ${state.revision}`);
  setText("#content-version", `WORLD ${state.content_version}`);
  setText("#turn-label", `TURN ${state.turn}`);
}

function renderCharacter(state) {
  const character = state.character || {};
  const build = character.build || {};
  const characterClass = build.class || null;
  const factionRoute = build.faction_route || null;
  const health = Number(character.health || 0);
  const maxHealth = Math.max(1, Number(character.max_health || 1));
  const healthPercent = health / maxHealth * 100;
  setText("#character-name", character.name || "Unknown");
  setText("#portrait-initials", String(character.name || "BE")
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0] || "")
    .join("")
    .toUpperCase());
  setText("#character-level", `L${character.level || 1}`);
  setText(
    "#character-class",
    characterClass?.name
      || (build.status === "legacy_preserved" ? "Legacy foundation" : "Foundation pending"),
  );
  const foundation = state.foundation || {};
  const allegiance = factionById(foundation, foundation.allegiance_id);
  const pendingAllegiance = factionById(foundation, foundation.pending_allegiance_id);
  setText(
    "#character-route",
    allegiance
      ? `${allegiance.name} · ${allegiance.rank_title || "entry rank"}`
      : pendingAllegiance
        ? `${pendingAllegiance.name} pledge pending · Action? [Y/N]`
        : factionRoute
          ? `${factionRoute.route_label} · ${factionRoute.candidacy_status || "candidacy unconfirmed"}`
          : "Independent · faction route unassigned",
  );
  setText("#character-profile", character.training?.profile_name || "Balanced path");
  setText("#character-posture", `${titleCase(character.stance)} · ${titleCase(character.defense_mode)}`);
  setText("#health-value", `${health} / ${maxHealth}`);
  setMeter(".health-meter", healthPercent, `${health} of ${maxHealth} integrity`);
  const healthMeter = $(".health-meter");
  healthMeter.classList.toggle("good", healthPercent > 65);
  healthMeter.classList.toggle("warn", healthPercent <= 65 && healthPercent > 30);
  setText("#stat-strength", character.attributes?.strength ?? "—");
  setText("#stat-agility", character.attributes?.agility ?? "—");
  setText("#stat-perception", character.attributes?.perception ?? "—");
  setText("#stat-combat", character.attributes?.combat_skill ?? "—");
  setText("#field-insight", character.field_insight ?? 0);
  setText("#absorbed-insight", character.absorbed_insight ?? 0);

  const statEffects = $("#stat-effect-list");
  clearNode(statEffects);
  for (const attribute of build.attributes || []) {
    const row = element("div", "stat-effect-row");
    const heading = element(
      "strong",
      "",
      `${attribute.abbreviation} ${attribute.effective_value}`,
    );
    const effects = attribute.effect_projection?.current || attribute.effects || [];
    row.append(heading, element("span", "", effects.join(" · ")));
    statEffects.append(row);
  }
  if (!(build.attributes || []).length) {
    statEffects.append(emptyNote("Build effects are unavailable for this record."));
  }

  const levelProgress = character.level_progress || {};
  const learned = Number(levelProgress.learned_in_level || 0);
  const required = Math.max(1, Number(levelProgress.required_per_level || 100));
  const awaiting = Number(levelProgress.awaiting_absorption || 0);
  setText("#level-progress-value", `${learned} / ${required} learned`);
  setText(
    "#level-progress-note",
    awaiting
      ? `${awaiting} field insight awaiting absorption.`
      : `${Number(levelProgress.remaining ?? required)} learned insight to next level.`,
  );
  setMeter(
    ".level-meter",
    learned / required * 100,
    `${learned} of ${required} learned insight; ${awaiting} awaiting absorption`,
  );

  const load = character.encumbrance || {};
  const bulk = Number(load.bulk || 0);
  const comfortable = Number(load.comfortable_limit || 0);
  const hard = Math.max(1, Number(load.hard_limit || 1));
  const loadPercent = bulk / hard * 100;
  setText("#load-value", `${bulk} / ${hard}`);
  const recoveryPenalty = Number(load.recovery_penalty || 0);
  setText(
    "#load-tier",
    recoveryPenalty
      ? `${titleCase(load.tier || "burdened")} · +${recoveryPenalty}s hard-action recovery`
      : `${titleCase(load.tier || "comfortable")} · no action delay · comfortable through ${comfortable}`,
  );
  setMeter(
    ".load-meter",
    loadPercent,
    `${bulk} of ${hard} bulk; ${recoveryPenalty} second recovery penalty`,
  );
  $(".load-meter").classList.toggle("warn", loadPercent > 60 && loadPercent < 90);
  $(".load-meter").classList.toggle("danger", loadPercent >= 90);

  const conditions = [];
  if (state.recovery?.class === "ready") conditions.push(["Ready", "positive"]);
  if (character.resting) conditions.push(["Resting", "positive"]);
  if (character.prone) conditions.push(["Prone", "negative"]);
  if (Number(character.stunned_seconds || 0) > 0) conditions.push(["Stunned", "negative"]);
  if (Number(character.bleeding_rate || 0) > 0) conditions.push([`Bleeding ${character.bleeding_rate}`, "negative"]);
  for (const limb of character.disabled_limbs || []) conditions.push([`${titleCase(limb)} disabled`, "negative"]);
  if (state.incapacitation) conditions.push(["Incapacitated", "negative"]);
  const list = $("#condition-list");
  clearNode(list);
  if (!conditions.length) conditions.push(["Stable", "positive"]);
  for (const [label, kind] of conditions) list.append(element("span", `condition-chip ${kind}`, label));
}

function renderWorld(state) {
  const room = state.room || {};
  const worldBody = String(room.world_body || "unspecified").replaceAll("-", " ").toUpperCase();
  setText("#room-id-label", `${worldBody} // ${String(room.id || "unknown").toUpperCase()}`);
  setText("#world-heading", room.title || "Unknown location");
  setText("#room-description", room.description || "Use LOOK for a full description.");
  const hazard = room.hazard || null;
  const hazardBanner = $("#hazard-banner");
  hazardBanner.hidden = !hazard;
  setText("#hazard-name", hazard?.name || "No active hazard");
  const mitigation = [
    ...(hazard?.mitigation_items || []).map((item) => item.name),
    ...(hazard?.mitigation_classes || []).map((entry) => entry.name),
  ];
  setText(
    "#hazard-summary",
    hazard
      ? `${hazard.text} Exposure: ${hazard.damage} health, +${hazard.roundtime}s recovery. Mitigation: ${mitigation.join(" or ") || "none documented"}.`
      : "No active environmental hazard.",
  );
  setText("#world-cycle", `WORLD CYCLE // ${titleCase(room.world_cycle?.phase || "unknown")}`);

  const facilities = $("#facility-list");
  clearNode(facilities);
  for (const facility of room.facilities || []) facilities.append(element("span", "", titleCase(facility)));

  const items = $("#room-item-list");
  clearNode(items);
  setText("#room-item-count", (room.items || []).length);
  for (const item of room.items || []) {
    const node = button(item.name, `get ${quoteArg(item.name)}`, "object-chip");
    node.title = `Pick up ${item.name}`;
    items.append(node);
  }
  if (!(room.items || []).length) items.append(emptyNote("Nothing loose nearby."));

  const npcs = $("#room-npc-list");
  clearNode(npcs);
  setText("#room-npc-count", (room.npcs || []).length);
  for (const npc of room.npcs || []) {
    const node = button("", npc.command || `talk ${npc.id}`, "object-chip npc-chip");
    const copy = element("span", "npc-chip-copy");
    copy.append(
      element("strong", "", npc.name),
      element(
        "small",
        "",
        `${npc.relationship_label || "Standing"}: ${titleCase(npc.relationship_standing || "uncertain")} (${Number(npc.relationship_score || 0) >= 0 ? "+" : ""}${Number(npc.relationship_score || 0)})`,
      ),
    );
    node.append(copy);
    node.title = `Talk to ${npc.name}. ${npc.description || ""}`.trim();
    node.setAttribute("aria-label", `Talk to ${npc.name}; ${npc.relationship_standing || "uncertain"} standing`);
    npcs.append(node);
  }
  if (!(room.npcs || []).length) npcs.append(emptyNote("No one is close enough to speak with."));

  const inspectables = $("#inspectable-list");
  clearNode(inspectables);
  setText("#inspectable-count", (room.inspectables || []).length);
  for (const noun of room.inspectables || []) {
    const node = button(titleCase(noun), `examine ${quoteArg(noun)}`, "object-chip");
    node.title = `Examine ${noun}`;
    inspectables.append(node);
  }
  if (!(room.inspectables || []).length) inspectables.append(emptyNote("No obvious detail singled out."));
}

function renderNavigation(state) {
  const exits = new Set(state.room?.exits || []);
  $$("#compass-grid button, #vertical-exits button").forEach((node) => {
    const available = exits.has(node.dataset.direction);
    node.disabled = !available;
    node.classList.toggle("available", available);
    const detail = (state.room?.exit_details || []).find(
      (entry) => entry.direction === node.dataset.direction,
    );
    const action = state.room?.creatures?.length ? "Withdraw" : "Go";
    const destination = detail?.destination_title
      ? ` toward ${detail.destination_title}`
      : "";
    const description = available
      ? `${action} ${node.dataset.direction}${destination}`
      : detail?.locked && detail?.lock_reason
        ? detail.lock_reason
        : `${titleCase(node.dataset.direction)} exit unavailable`;
    node.title = description;
    node.setAttribute("aria-label", description);
  });
  const navigation = state.navigation || {};
  setText("#visited-count", `${navigation.visited_count || 0} / ${navigation.total_rooms || 0}`);
  const places = $("#place-list");
  clearNode(places);
  const visitedRooms = navigation.visited_rooms || [];
  const visibleRooms = visitedRooms
    .slice()
    .sort((left, right) => Number(right.id === state.room?.id) - Number(left.id === state.room?.id))
    .slice(0, 12);
  for (const place of visibleRooms) {
    const node = button(place.title, `route ${quoteArg(place.title)}`, "place-button");
    if (place.id === state.room?.id) node.classList.add("current");
    places.append(node);
  }
  if (visitedRooms.length > visibleRooms.length) {
    places.append(emptyNote(`${visitedRooms.length - visibleRooms.length} more known places remain available through ROUTE or the command palette.`));
  }
}

function renderCombat(state) {
  const creatures = state.room?.creatures || [];
  const target = creatures.find((creature) => creature.instance_id === state.target_id) || null;
  const combatPanel = $(".combat-panel");
  const engaged = creatures.length > 0;
  combatPanel?.classList.toggle("area-clear", !engaged);
  document.body.classList.toggle("combat-engaged", engaged);
  setText("#combat-state", engaged ? `${creatures.length} HOSTILE${creatures.length === 1 ? "" : "S"}` : "CLEAR");
  $("#combat-state")?.classList.toggle("engaged", engaged);
  $("#target-card")?.classList.toggle("active", Boolean(target));
  setText("#target-name", target?.name || "No target");
  setText(
    "#target-health-value",
    target
      ? `${target.health} / ${target.max_health} integrity${Number(target.phase_count || 1) > 1 ? ` · phase ${Number(target.phase || 1)}/${Number(target.phase_count || 1)} · exchange ${Number(target.exchange_count || 0)}` : ""}`
      : engaged ? "Select an opponent below" : "Area clear",
  );
  setMeter(
    ".target-health",
    target ? target.health / Math.max(1, target.max_health) * 100 : 0,
    target ? `${target.health} of ${target.max_health} target integrity` : "No target selected",
  );

  const enemies = $("#enemy-list");
  clearNode(enemies);
  const sorted = creatures.slice().sort((left, right) => {
    const leftActor = actorForCreature(state, left);
    const rightActor = actorForCreature(state, right);
    return Number(leftActor?.ready_in_field_seconds ?? 999) - Number(rightActor?.ready_in_field_seconds ?? 999);
  });
  setText("#enemy-count-label", String(sorted.length));
  for (const creature of sorted) {
    const actor = actorForCreature(state, creature) || {};
    const node = button("", `target ${quoteArg(creature.name)}`, "enemy-button enemy-tactical-row");
    const enemyCopy = element("span", "enemy-copy");
    const role = titleCase(creature.behavior_profile || creature.combat_role || "threat");
    const intent = actor.intent ? intentLabel(actor.intent) : "reading the field";
    const targetName = actor.target_name || (actor.target_id === "player" ? state.character?.name || "you" : "");
    const timing = actorTiming(actor);
    enemyCopy.append(
      element("strong", "enemy-name", creature.name),
      element("small", "enemy-role", `${role}${creature.support_power ? ` · restore ${creature.support_power}` : ""}${Number(creature.phase_count || 1) > 1 ? ` · ${creature.phase_label || `phase ${creature.phase}`}` : ""}`),
      element("span", "enemy-intent", `${titleCase(intent)}${targetName ? ` → ${targetName}` : ""} · ${timing}`),
    );
    const effects = actorEffects(actor);
    if (effects.length) {
      const effectRow = element("span", "effect-chip-row");
      effects.forEach((effect) => effectRow.append(element("i", "effect-chip", effect)));
      enemyCopy.append(effectRow);
    }
    node.append(enemyCopy, element("span", "enemy-health", `${creature.health}/${creature.max_health}`));
    if (creature.instance_id === state.target_id) {
      node.classList.add("selected");
      node.setAttribute("aria-current", "true");
    }
    node.setAttribute("aria-label", `Target ${creature.name}. ${role}. ${intent}${targetName ? ` toward ${targetName}` : ""}. ${timing}. Integrity ${creature.health} of ${creature.max_health}.`);
    enemies.append(node);
  }
  if (!creatures.length) enemies.append(emptyNote("No active opponents in this area."));

  $("#stance-select").value = state.character?.stance || "guarded";
  $("#defense-select").value = state.character?.defense_mode || "balanced";
  $$("[data-command=\"attack\"], [data-command=\"assess\"]").forEach((node) => {
    node.disabled = !target;
    node.title = target ? `Use ${node.dataset.command} on ${target.name}` : "Select a target first";
  });
  $("#withdraw-button").disabled = !engaged || !(state.room?.exits || []).length;

  const wounds = $("#wound-list");
  clearNode(wounds);
  for (const wound of state.character?.wounds || []) {
    const row = element("div", "wound-row");
    row.append(
      element("span", "", `${titleCase(wound.location)} · severity ${wound.severity}`),
      element("span", "", wound.bleeding ? `Bleed ${wound.bleeding}` : "Stable"),
    );
    wounds.append(row);
  }
  const hasWounds = Boolean((state.character?.wounds || []).length);
  combatPanel?.classList.toggle("no-wounds", !hasWounds);
  if (!hasWounds) wounds.append(emptyNote("No recorded wounds."));
}

function renderBattlefield(state) {
  const battlefield = state.battlefield || {};
  const actors = battlefieldActors(state).slice().sort((left, right) => {
    const delta = Number(left.ready_in_field_seconds || 0) - Number(right.ready_in_field_seconds || 0);
    if (delta) return delta;
    const order = { player: 0, companion: 1, creature: 2 };
    return Number(order[left.kind] ?? 9) - Number(order[right.kind] ?? 9);
  });
  const creatures = state.room?.creatures || [];
  const active = Boolean(battlefield.active && creatures.length);
  const card = $("#combat-now-card");
  if (card) card.hidden = !active;
  $$("[data-combat-projection]").forEach((node) => {
    const selected = node.dataset.combatProjection === app.combatProjection;
    node.classList.toggle("active", selected);
    node.setAttribute("aria-checked", String(selected));
    node.setAttribute("aria-pressed", String(selected));
  });

  const timeline = $("#battlefield-timeline");
  clearNode(timeline);
  if (!active) {
    setText("#battlefield-summary", "No active battlefield.");
    setText("#battlefield-soft-time", "Reading and status commands do not advance field time.");
    setText("#combat-now-player", "Ready");
    setText("#combat-now-sol", "Unavailable");
    setText("#combat-now-threat", "Area clear");
    const live = $("#tactical-live-region");
    if (live) live.textContent = "";
    app.lastTacticalAnnouncement = "";
    return;
  }

  const hostiles = actors.filter((actor) => actor.kind === "creature");
  const sol = actors.find((actor) => actor.kind === "companion") || null;
  const player = actors.find((actor) => actor.kind === "player") || null;
  const nextThreat = hostiles[0] || null;
  const fieldTime = Number(battlefield.battle_time || 0).toFixed(1);
  setText("#combat-now-player", player ? actorTiming(player) : "Ready");
  setText("#combat-now-sol", sol ? `${titleCase(intentLabel(sol.intent))} · ${actorTiming(sol)}` : "Unavailable");
  setText("#combat-now-threat", nextThreat ? `${nextThreat.name} · ${actorTiming(nextThreat)}` : "Area clear");
  setText(
    "#battlefield-summary",
    `Field T+${fieldTime} · You ${player ? actorTiming(player) : "ready"} · ${sol ? `Sol ${intentLabel(sol.intent)} ${actorTiming(sol)}` : "Sol unavailable"} · ${nextThreat ? `${nextThreat.name} ${intentLabel(nextThreat.intent)} ${actorTiming(nextThreat)}` : "no immediate threat"}`,
  );
  setText(
    "#battlefield-soft-time",
    battlefield.soft_commands_advance_time === false
      ? "LOOK, ASSESS, HEALTH, INJURY, EFFECTS, ROUNDTIME, HELP, and other soft views do not advance battlefield time."
      : "Battlefield time advances only through authoritative game actions.",
  );

  const visible = app.combatProjection === "narrative" ? actors.slice(0, 5) : actors;
  for (const actor of visible) {
    const row = element("div", `battlefield-actor-row actor-${actor.kind || "unknown"}`);
    row.setAttribute("role", "listitem");
    const marker = element("span", "battlefield-marker", actor.kind === "player" ? "YOU" : actor.kind === "companion" ? "SOL" : "THREAT");
    const copy = element("span", "battlefield-actor-copy");
    const action = actor.intent ? intentLabel(actor.intent) : Number(actor.ready_in_field_seconds || 0) <= 0 ? "ready" : "recovering";
    copy.append(
      element("strong", "", actor.name || titleCase(actor.kind)),
      element("small", "", `${titleCase(action)}${actor.target_name && actor.intent ? ` → ${actor.target_name}` : ""}`),
    );
    row.append(marker, copy, element("strong", "battlefield-timing", actorTiming(actor)));
    const effects = actorEffects(actor);
    if (effects.length && app.combatProjection !== "narrative") {
      const effectRow = element("span", "effect-chip-row battlefield-effects");
      effects.forEach((effect) => effectRow.append(element("i", "effect-chip", effect)));
      row.append(effectRow);
    }
    if (app.combatProjection === "audit") {
      row.append(element("code", "battlefield-audit", `${actor.actor_id} · recovery ${Number(actor.recovery_duration || 0).toFixed(1)} · actions ${Number(actor.actions_taken || 0)} · interrupted ${Number(actor.interrupted_for_field_seconds || 0).toFixed(1)}`));
    }
    timeline.append(row);
  }
  if (visible.length < actors.length) timeline.append(emptyNote(`+${actors.length - visible.length} more actor${actors.length - visible.length === 1 ? "" : "s"}; use Tactical for the full field.`));

  const announcement = `${hostiles.length} hostile${hostiles.length === 1 ? "" : "s"}. ${nextThreat ? `${nextThreat.name} ${intentLabel(nextThreat.intent)} ${actorTiming(nextThreat)}.` : ""} ${sol ? `Sol ${intentLabel(sol.intent)} ${actorTiming(sol)}.` : ""} You ${player ? actorTiming(player) : "ready"}.`;
  if (announcement !== app.lastTacticalAnnouncement) {
    const live = $("#tactical-live-region");
    if (live) live.textContent = announcement;
    app.lastTacticalAnnouncement = announcement;
  }
}

function renderCombatPartner(state) {
  const companion = state.economy?.companion || null;
  const actor = companionBattlefieldActor(state);
  const card = $("#combat-partner-card");
  const active = Boolean(companion && companion.assist_kind === "partner");
  card.hidden = !active;
  if (!active) return;

  const health = Number(companion.health || 0);
  const maxHealth = Math.max(1, Number(companion.max_health || 1));
  const level = Math.max(1, Number(companion.level || 1));
  const experience = Math.max(0, Number(companion.experience || 0));
  const downedSeconds = Math.max(0, Number(companion.downed_seconds || 0));
  const order = String(companion.order || "balanced");
  const intent = actor?.intent ? intentLabel(actor.intent) : order === "guard" ? "guard coverage" : order === "assault" ? "assault pressure" : "measured setup";
  const timing = actor ? actorTiming(actor) : downedSeconds > 0 ? `${downedSeconds}s` : "ready";
  const targetName = actor?.target_name && actor.target_name !== "the open lane" ? actor.target_name : "Open lane";
  const effects = actorEffects(actor || {});
  setText("#combat-partner-name", companion.name || "Partner");
  setText("#combat-partner-level", `L${level}`);
  setText("#combat-partner-role", `${companion.role || "Field partner"} · ${titleCase(order)} order`);
  setText("#combat-partner-health-value", `${health} / ${maxHealth}`);
  setText("#combat-partner-intent", titleCase(intent));
  setText("#combat-partner-ready", timing);
  setText("#combat-partner-target", targetName);
  setText("#combat-partner-xp-value", `${experience} insight`);
  setText(
    "#partner-record-summary",
    `${Number(companion.setup_actions || 0)} setups · ${Number(companion.finish_reservations || 0)} finishes yielded · ${Number(companion.player_enabled_finishes || 0)} player conversions · ${Number(companion.finishing_strikes || 0)} Sol finishes · ${Number(companion.damage_intercepted || 0)} damage intercepted.`,
  );
  setMeter(".companion-health-meter", health / maxHealth * 100, `${health} of ${maxHealth} partner integrity`);
  const levelProgress = level >= 10 ? 100 : experience % 100;
  setMeter(".companion-xp-meter", levelProgress, level >= 10 ? "Partner foundation level 10 reached" : `${levelProgress} of 100 insight toward the next partner level`);
  const effectList = $("#combat-partner-effects");
  clearNode(effectList);
  effects.forEach((effect) => effectList.append(element("i", "effect-chip", effect)));
  if (!effects.length) effectList.append(element("small", "", "No active tactical state"));
  card.classList.toggle("downed", downedSeconds > 0);
  setText(
    "#combat-partner-status",
    downedSeconds > 0
      ? `${companion.name} is recovering for ${downedSeconds} seconds and will rejoin automatically.`
      : `${titleCase(intent)} → ${targetName} · ${timing}. ${companion.order_summary || ""}`.trim(),
  );
  $$('[data-companion-order]').forEach((node) => {
    const selected = node.dataset.companionOrder === order;
    node.classList.toggle("active", selected);
    node.setAttribute("aria-pressed", String(selected));
    node.disabled = downedSeconds > 0 || selected;
  });
  const syncButton = $("#combat-partner-sync");
  if (syncButton) {
    const unlocked = Boolean(companion.sync_unlocked);
    const available = Boolean(companion.sync_available);
    syncButton.hidden = !unlocked;
    syncButton.disabled = !available;
    syncButton.dataset.command = available ? (companion.sync_command || "companion sync") : "companion sync status";
    syncButton.textContent = available ? `Synchronize on ${companion.sync_target_name || "target"}` : "Synchrony status";
    syncButton.title = available ? (companion.sync_summary || "Trigger one player-owned shared beat.") : (companion.sync_reason || "Synchrony is not available here.");
  }
}

function renderWithdrawal(state) {
  const withdrawal = state.withdrawal || {};
  const routes = Array.isArray(withdrawal.routes) ? withdrawal.routes : [];
  const contested = Boolean(withdrawal.contested || withdrawal.active);
  const card = $("#withdrawal-card");
  const disclosure = $("details", card);
  card.hidden = !contested;
  if (!contested) {
    if (disclosure) disclosure.open = false;
    return;
  }

  const viable = routes.filter((route) => !route.locked).length;
  setText("#withdrawal-summary", `${viable} viable ${viable === 1 ? "route" : "routes"} · contested`);
  setText("#withdrawal-opponents", `${Number(withdrawal.opponent_count || 0)} HOSTILES`);
  setText("#withdrawal-guidance", withdrawal.guidance || "Withdrawal is an opposed roll; inspect the route before committing.");
  setText("#withdrawal-retry-rule", withdrawal.retry_rule || `Failed same-route attempts gain +${Number(withdrawal.retry_increment || 6)} route-read, capped at +${Number(withdrawal.retry_cap || 18)}.`);
  const list = $("#withdrawal-route-list");
  clearNode(list);
  for (const route of routes) {
    const row = element("button", "withdrawal-route");
    row.type = "button";
    row.disabled = Boolean(route.locked);
    row.dataset.command = route.locked ? "withdraw status" : (route.command || `withdraw ${route.direction}`);
    const needed = Number(route.normal_roll_needed || 1);
    const chance = route.locked
      ? `BLOCKED · ${route.lock_reason || "route unavailable"}`
      : route.contested
        ? needed <= 100 ? `NORMAL D100 ${Math.max(1, needed)}+` : `OPEN-ENDED ${needed}+`
        : "UNCONTESTED";
    const modifiers = route.modifiers || {};
    const detail = route.locked
      ? route.destination_title || route.destination_id
      : `Escape ${Number(route.escape || 0)} vs pressure ${Number(route.pressure || 0)} · Sol +${Number(modifiers.companion_bonus || 0)} · route-read +${Number(modifiers.retry_bonus || 0)}`;
    row.append(
      element("span", "", `${String(route.direction || "?").toUpperCase()} → ${route.destination_title || route.destination_id || "Unknown"}`),
      element("strong", "", chance),
      element("small", "", detail),
    );
    list.append(row);
  }
  if (!routes.length) list.append(emptyNote("No visible withdrawal route."));
}

function techniqueCommand(state) {
  const technique = state.character?.technique || null;
  if (!technique) return "technique";
  const creatures = state.room?.creatures || [];
  const target = creatures.find((creature) => creature.instance_id === state.target_id) || creatures[0] || null;
  if (["power_attack", "precision_attack", "system_attack", "balanced_attack"].includes(technique.kind)) {
    return target ? `technique ${quoteArg(target.name)}` : "technique";
  }
  if (technique.kind === "escape") {
    const direction = (state.room?.exits || [])[0];
    return direction ? `technique ${direction}` : "technique";
  }
  return "technique self";
}

function renderTechnique(state) {
  const technique = state.character?.technique || null;
  const remaining = Number(technique?.ready_in_seconds || 0);
  setText("#technique-name", technique?.name || "Signature technique unavailable");
  setText("#technique-summary", technique?.summary || "Confirm a class foundation to recover a dependable field technique.");
  setText("#passive-name", technique?.passive_name || "Passive");
  setText("#passive-summary", technique?.passive_summary || "No passive foundation.");
  setText("#exploration-name", technique?.exploration_name || "Exploration");
  setText("#exploration-summary", technique?.exploration_summary || "No exploration specialty.");
  setText("#guard-points", `Guard ${Number(state.character?.guard_points || 0)}`);
  const unlocked = Boolean(technique?.unlocked);
  setText("#technique-status", !technique || !unlocked ? "LOCKED" : remaining ? `${remaining}s` : "READY");
  const card = $("#technique-card");
  card.classList.toggle("recovering", remaining > 0);
  card.classList.toggle("locked", !unlocked);
  const useButton = $("#technique-button");
  useButton.disabled = !technique || !unlocked || remaining > 0;
  useButton.dataset.command = techniqueCommand(state);
  useButton.title = !technique
    ? "Confirm a class foundation first"
    : !unlocked
      ? "Claim this signature instinct through the opening story"
      : remaining
      ? `${technique.name} is recovering for ${remaining} more seconds`
      : `Use ${technique.name}`;
  useButton.setAttribute("aria-label", useButton.title);
}

function specializationTargetCommand(state, prefix, kind) {
  const creatures = state.room?.creatures || [];
  const target = creatures.find((creature) => creature.instance_id === state.target_id) || creatures[0] || null;
  if (["attack", "precision", "report"].includes(kind)) {
    return target ? `${prefix} ${quoteArg(target.name)}` : prefix.replace(/ use$/, "");
  }
  if (kind === "escape") {
    const direction = (state.room?.exits || [])[0];
    return direction ? `${prefix} ${direction}` : prefix.replace(/ use$/, "");
  }
  return prefix;
}

function specializationCommand(state) {
  const specialization = state.character?.specialization || null;
  return specializationTargetCommand(
    state,
    "ability use",
    specialization?.selected_kind || null,
  );
}

function specializationFollowUpCommand(state) {
  const specialization = state.character?.specialization || null;
  return specializationTargetCommand(
    state,
    "ability followup",
    specialization?.selected_follow_up?.kind || null,
  );
}

function renderSpecialization(state) {
  const specialization = state.character?.specialization || null;
  const abilityCard = $("#ability-card");
  abilityCard?.classList.toggle("locked", !specialization?.selected_id && !specialization?.point_available);
  const options = $("#ability-options");
  clearNode(options);
  const followUpButton = $("#ability-follow-up-button");
  if (!specialization) {
    setText("#ability-status", "LOCKED");
    setText("#ability-heading", "Class branch unavailable");
    setText("#ability-summary", "Confirm a class foundation first.");
    setText("#ability-passive-name", "Passive");
    setText("#ability-passive-summary", "No branch passive selected.");
    setText("#ability-follow-up-name", "Follow-up");
    setText("#ability-follow-up-summary", "No chained action available.");
    setText("#ability-mastery-name", "Mastery");
    setText("#ability-mastery-summary", "Confirm a class foundation first.");
    setText("#ability-counterplay", "Every strong action creates an opening.");
    setText("#ability-follow-up-status", "No chain ready");
    $("#ability-use-button").disabled = true;
    followUpButton.disabled = true;
    return;
  }
  const remaining = Number(specialization.ready_in_seconds || 0);
  const followUpRemaining = Number(specialization.selected_follow_up?.ready_in_seconds || 0);
  setText(
    "#ability-status",
    specialization.selected_id ? (remaining ? `${remaining}s` : "READY") : specialization.point_available ? "CHOOSE" : "UNLEARNED",
  );
  setText("#ability-heading", specialization.selected_name || "Choose one class path");
  setText(
    "#ability-summary",
    specialization.selected_summary
      || (specialization.point_available
        ? "A first-contact specialization point is available. Choose carefully; no silent respec is granted."
        : "Complete a first faction contact to earn one specialization point."),
  );
  setText("#ability-passive-name", specialization.selected_passive?.name || "Passive");
  setText("#ability-passive-summary", specialization.selected_passive?.summary || "Choose a branch to reveal its passive benefit.");
  setText("#ability-follow-up-name", specialization.selected_follow_up?.name || "Follow-up");
  setText(
    "#ability-follow-up-summary",
    specialization.selected_follow_up?.summary
      || "Choose a branch to reveal its chained action.",
  );
  const masteryUses = Number(specialization.mastery_uses || 0);
  const masteryRequired = Number(specialization.mastery_required || 0);
  setText(
    "#ability-mastery-name",
    specialization.selected_upgrade_name || "Mastery",
  );
  setText(
    "#ability-mastery-summary",
    specialization.selected_id
      ? specialization.selected_upgrade_name
        ? `${specialization.selected_upgrade_name} is locked in. Primary power ${specialization.effective_power}; ${specialization.effective_cooldown}s cooldown.`
        : specialization.mastery_ready
          ? `Mastery ready after ${masteryUses}/${masteryRequired} uses. Choose one permanent refinement below.`
          : `${Math.min(masteryUses, masteryRequired)}/${masteryRequired} successful uses toward a permanent refinement.`
      : "Learn a branch, then use it successfully to develop mastery.",
  );
  setText("#ability-counterplay", specialization.counterplay || "Every strong action creates an opening.");

  for (const branch of specialization.branches || []) {
    const node = element("div", `ability-option${branch.learned ? " learned" : ""}`);
    const copy = element("span");
    copy.append(
      element("strong", "", branch.name),
      element("small", "", `${titleCase(branch.kind)} · power ${branch.power} · ${branch.cooldown}s cooldown`),
      element("small", "", branch.summary),
      element("small", "", `Passive: ${branch.passive_name} — ${branch.passive_summary}`),
      element("small", "", `Follow-up: ${branch.follow_up_name} — ${branch.follow_up_summary}`),
    );
    node.append(copy);
    if (!specialization.selected_id && specialization.point_available) {
      const learn = button("Learn", branch.learn_command, "quiet-button");
      learn.title = `Learn ${branch.name}; this choice persists`;
      node.append(learn);
    }
    options.append(node);
  }

  if (specialization.mastery_ready) {
    const heading = element("div", "ability-upgrade-heading");
    heading.append(
      element("strong", "", "Choose mastery"),
      element("small", "", "One choice persists; neither option silently replaces the other."),
    );
    options.append(heading);
    for (const upgrade of specialization.upgrade_options || []) {
      const node = element("div", "ability-option mastery-option");
      const copy = element("span");
      copy.append(
        element("strong", "", upgrade.name),
        element("small", "", upgrade.summary),
      );
      node.append(copy);
      const choose = button("Choose", upgrade.command, "quiet-button");
      choose.title = `Choose ${upgrade.name}; this mastery persists`;
      node.append(choose);
      options.append(node);
    }
  }

  const useButton = $("#ability-use-button");
  useButton.disabled = !specialization.selected_id || remaining > 0;
  useButton.dataset.command = specializationCommand(state);
  useButton.title = specialization.selected_id
    ? remaining
      ? `${specialization.selected_name} is recovering for ${remaining} seconds`
      : `Use ${specialization.selected_name}`
    : "Learn a branch first";

  followUpButton.disabled = !specialization.selected_id || followUpRemaining <= 0;
  followUpButton.dataset.command = specializationFollowUpCommand(state);
  followUpButton.title = followUpRemaining > 0
    ? `Use ${specialization.selected_follow_up?.name || "follow-up"} within ${followUpRemaining} seconds`
    : "Use the primary specialization to prepare a follow-up";
  setText(
    "#ability-follow-up-status",
    followUpRemaining > 0 ? `${followUpRemaining}s chain window` : "No chain ready",
  );
}

function renderEconomy(state) {
  const economy = state.economy || {};
  setText("#credit-balance", `${Number(economy.credits || 0)} credits`);
  setText(
    "#companion-summary",
    economy.companion
      ? `${economy.companion.name} · level ${economy.companion.level} · ${titleCase(economy.companion.order)} · ${economy.companion.health}/${economy.companion.max_health} integrity`
      : "No companion attached. One bounded contract-support slot is available at the Route Concourse.",
  );
  const vendorList = $("#vendor-list");
  clearNode(vendorList);
  if (economy.vendor) {
    vendorList.append(element("strong", "economy-subtitle", economy.vendor.name));
    for (const item of (economy.vendor.items || []).slice(0, 6)) {
      const buy = button(`${item.name} · ${item.price}c`, item.command, "economy-button");
      buy.disabled = !item.affordable;
      buy.title = item.affordable ? `Buy ${item.name}` : `Need ${item.price} credits`;
      vendorList.append(buy);
    }
  } else {
    vendorList.append(emptyNote("No exchange operates in this room."));
  }
  const recipeList = $("#recipe-list");
  clearNode(recipeList);
  const localRecipes = (economy.recipes || []).filter((recipe) => recipe.facility_present);
  if (localRecipes.length) {
    recipeList.append(element("strong", "economy-subtitle", "Local recipes"));
    for (const recipe of localRecipes.slice(0, 4)) {
      const craft = button(`${recipe.name} · ${recipe.credit_cost}c`, recipe.command, "economy-button");
      craft.disabled = !recipe.available;
      craft.title = recipe.available ? `Craft ${recipe.name}` : `Missing inputs, credits, or facility`;
      recipeList.append(craft);
    }
  }
}

function itemSummary(item) {
  const details = [`bulk ${item.bulk ?? 0}`];
  if (Number(item.upgrade_level || 0)) details.push(`modified +${item.upgrade_level}`);
  if (item.slot) details.push(titleCase(item.slot));
  if (item.damage) details.push(`damage ${item.damage[0]}–${item.damage[1]}`);
  if (item.armor) details.push(`armor ${item.armor}`);
  if (item.equipped) details.push("equipped");
  return details.join(" · ");
}

function renderInventory(state) {
  const inventory = state.character?.inventory || [];
  setText("#inventory-count", `${inventory.length} ${inventory.length === 1 ? "ITEM" : "ITEMS"}`);
  const list = $("#inventory-list");
  clearNode(list);
  for (const item of inventory) {
    const row = element("div", "inventory-item");
    const icon = element("span", "item-icon", item.slot === "main_hand" ? "WPN" : item.slot === "body" ? "ARM" : "ITM");
    const copy = element("div", "item-copy");
    copy.append(
      element("strong", "", `${item.name}${Number(item.upgrade_level || 0) ? ` +${item.upgrade_level}` : ""}`),
      element("small", "", itemSummary(item)),
    );
    const actions = element("div", "item-actions");
    const equipVerb = item.equipped ? "unequip" : "equip";
    if (item.slot) {
      const equip = button(item.equipped ? "−" : "+", `${equipVerb} ${quoteArg(item.name)}`);
      equip.title = `${titleCase(equipVerb)} ${item.name}`;
      equip.setAttribute("aria-label", equip.title);
      actions.append(equip);
    }
    if (item.can_compare) {
      const compare = button("↔", `compare ${quoteArg(item.name)}`);
      compare.title = `Compare ${item.name} with equipped gear`;
      compare.setAttribute("aria-label", compare.title);
      actions.append(compare);
    }
    if (item.can_modify) {
      const modify = button("+", `modify ${quoteArg(item.name)}`);
      modify.title = `Modify ${item.name} at a repair bench`;
      modify.setAttribute("aria-label", modify.title);
      actions.append(modify);
    }
    const inspect = button("i", `examine ${quoteArg(item.name)}`);
    inspect.title = `Examine ${item.name}`;
    inspect.setAttribute("aria-label", inspect.title);
    const drop = button("↓", `drop ${quoteArg(item.name)}`);
    drop.title = `Drop ${item.name}`;
    drop.setAttribute("aria-label", drop.title);
    actions.append(inspect, drop);
    row.append(icon, copy, actions);
    if (item.max_durability) {
      const track = element("div", "item-durability");
      const fill = element("span");
      const durability = Number(item.durability || 0);
      const maximum = Number(item.max_durability || 1);
      fill.style.width = `${clamp(durability / maximum * 100, 0, 100)}%`;
      track.setAttribute("role", "progressbar");
      track.setAttribute("aria-label", `${item.name} durability`);
      track.setAttribute("aria-valuemin", "0");
      track.setAttribute("aria-valuemax", String(maximum));
      track.setAttribute("aria-valuenow", String(durability));
      track.setAttribute("aria-valuetext", `${durability} of ${maximum}`);
      track.append(fill);
      row.append(track);
    }
    list.append(row);
  }
  if (!inventory.length) list.append(emptyNote("You are carrying nothing."));

  $$("#equipment-slots > div").forEach((slot) => {
    const equipped = inventory.find((item) => item.equipped_slot === slot.dataset.slot);
    slot.classList.toggle("filled", Boolean(equipped));
    $("strong", slot).textContent = equipped?.name || "Empty";
  });
}

function renderFoundation(state) {
  const foundation = state.foundation || null;
  const card = $("#sovereignty-card");
  const sprawlCard = $("#sprawl-state-card");
  if (!card) return;
  card.hidden = !foundation;
  if (sprawlCard) sprawlCard.hidden = !foundation;
  if (!foundation) return;

  const allegiance = factionById(foundation, foundation.allegiance_id);
  const pending = factionById(foundation, foundation.pending_allegiance_id);
  const party = foundation.party || {};
  const members = Array.isArray(party.member_ids) ? party.member_ids : [];
  const mercenaries = Array.isArray(party.mercenary_ids) ? party.mercenary_ids : [];
  const status = pending ? "DECISION PENDING" : allegiance ? "PLEDGED" : "INDEPENDENT";
  setText("#sovereignty-title", pending?.name || allegiance?.name || "Independent");
  setText("#sovereignty-rank", pending ? pending.pledge_entry_rank_title || pending.rank_title || "PENDING" : allegiance?.rank_title || "UNPLEDGED");
  setText(
    "#sovereignty-summary",
    `${status} · ${allegiance ? "Confirmed allegiance is authoritative." : pending ? "Allegiance changes only after Action? [Y/N]." : "Faction interest and candidacy never silently become allegiance."}`,
  );
  const trust = Number(foundation.local_trust || 0);
  setText("#sovereignty-trust", `${trust >= 0 ? "+" : ""}${trust} · ${trust >= 10 ? "Trusted" : trust <= -10 ? "Strained" : "Cautious"}`);
  setText("#foundation-party-summary", `${members.length}/6 + ${mercenaries.length}/2`);
  setText("#foundation-party-formation", titleCase(party.formation || "balanced"));

  const confirmation = $("#pledge-confirmation");
  if (confirmation) confirmation.hidden = !pending;
  if (pending) {
    setText("#pledge-confirmation-title", `${pending.name} · ${pending.pledge_entry_rank_title || pending.rank_title || "entry rank"}`);
    const copy = $("p", confirmation);
    if (copy) copy.textContent = "Action? [Y/N] Allegiance changes only after your explicit answer.";
  }

  const factionList = $("#faction-standing-list");
  clearNode(factionList);
  for (const faction of foundation.factions || []) {
    const row = element("div", "foundation-faction-row");
    row.classList.toggle("allegiance", Boolean(faction.is_allegiance));
    row.classList.toggle("pending", Boolean(faction.is_pending_allegiance));
    const copy = element("span", "foundation-faction-copy");
    copy.append(
      element("strong", "", faction.name),
      element("small", "", `${standingText(faction.public_standing)} public · ${faction.standing_label || "Uncommitted"}${faction.rank_title && faction.rank_title !== "Unranked" ? ` · ${faction.rank_title}` : ""}`),
    );
    row.append(copy);
    if (faction.pledge_eligible && !foundation.allegiance_id && !foundation.pending_allegiance_id) {
      row.append(button("Pledge", `faction pledge ${quoteArg(faction.name)}`, "faction-pledge-button"));
    } else {
      const stateLabel = faction.is_allegiance ? "ACTIVE" : faction.is_pending_allegiance ? "PENDING" : faction.pledge_eligible ? "ELIGIBLE" : `${Number(faction.pledge_minimum_standing || 0)}+`;
      row.append(element("small", "foundation-faction-state", stateLabel));
    }
    factionList.append(row);
  }

  const territory = foundation.territory || {};
  setText("#sprawl-state-title", foundation.territory_title || "Sprawl 15 Community State");
  const tension = Number(territory.tension || 0);
  setText("#sprawl-threat-state", tension >= 80 ? "CRITICAL" : tension >= 55 ? "STRAINED" : "STABLE");
  $("#sprawl-state-card")?.setAttribute("data-pressure", tension >= 80 ? "critical" : tension >= 55 ? "strained" : "stable");
  setTerritoryMetric("supply", territory.supply);
  setTerritoryMetric("defense", territory.defense);
  setTerritoryMetric("prosperity", territory.prosperity);
  setTerritoryMetric("tension", territory.tension);

  const mission = foundation.civic_mission || {};
  const objective = Array.isArray(mission.active_objective_ids) ? mission.active_objective_ids[0] : null;
  const selected = String(mission.selected_resolution_id || "");
  const objectiveText = {
    accept: "Inactive · accept the civic responsibility when ready.",
    inspect: "Accepted · inspect the protection economy.",
    select_plan: "Inspected · choose Shared Table, Visible Watch, or Relief Before Tribute.",
    execute: selected ? `Plan selected · execute ${titleCase(selected.replace(/^plan:/, ""))}.` : "Plan selected · execute the civic response.",
    close: "Action complete · close the civic record and preserve its consequences.",
  }[objective] || (mission.status === "complete" ? "Complete · consequences preserved." : `${titleCase(mission.status || "inactive")} · no civic step active.`);
  setText("#civic-mission-step", objectiveText);
  const actions = $("#civic-action-row");
  clearNode(actions);
  if (objective === "accept") actions.append(button("Accept", "civic accept"));
  else if (objective === "inspect") actions.append(button("Inspect", "civic inspect"));
  else if (objective === "select_plan") {
    actions.append(button("Shared Table", "civic plan supply"));
    actions.append(button("Visible Watch", "civic plan watch"));
    actions.append(button("Relief First", "civic plan relief"));
  } else if (objective === "execute") actions.append(button("Execute plan", "civic execute"));
  else if (objective === "close") actions.append(button("Close record", "civic close"));
  actions.append(button("Status", "civic status", "quiet-button"));
}

function renderBeginnerExperience(state) {
  const journey = state.journeyman_experience || null;
  const foundation = journey?.started ? journey : state.beginner_experience || null;
  const card = $("#foundation-card");
  card.hidden = !foundation;
  if (!foundation) return;

  const journeyPhase = Number(foundation.target_level || 10) > 10;

  const chapters = Array.isArray(foundation.chapters) ? foundation.chapters : [];
  const competencies = Array.isArray(foundation.competencies) ? foundation.competencies : [];
  const activeChapter = chapters.find((chapter) => chapter.active) || chapters.find((chapter) => !chapter.complete) || chapters.at(-1);
  const levelReady = Boolean(foundation.level_ready);
  const capstoneReady = Boolean(foundation.ready_for_capstone);
  const complete = Boolean(foundation.complete);
  setText("#foundation-title", foundation.title || (journeyPhase ? "The Echo Between Roads" : "Sprawl 15 Foundation"));
  setText(
    "#foundation-summary",
    foundation.summary || (journeyPhase
      ? "A level 11–20 field phase with a recoverable level 15–18 shock band."
      : "A naturally ramping level 1–10 opening with Sol."),
  );
  setText(
    "#foundation-status",
    complete ? "COMPLETE" : capstoneReady ? "CAPSTONE READY" : levelReady ? "LEVEL READY" : "IN PROGRESS",
  );
  setText("#foundation-level", `${Number(foundation.current_level || 1)} / ${Number(foundation.target_level || 10)}`);
  setText("#foundation-time", `${Number(foundation.estimated_completed_minutes || 0)} / ${Number(foundation.target_minutes || 120)} min`);
  setText("#foundation-rooms", `${Number(foundation.starter_rooms_discovered || 0)} / ${Number(foundation.starter_room_count || 0)} rooms`);
  setText(
    "#foundation-active-chapter",
    activeChapter
      ? `${activeChapter.active ? "CURRENT" : activeChapter.complete ? "CLEARED" : "NEXT"} · ${activeChapter.title} · ${activeChapter.completed_quests}/${activeChapter.quest_count} quests`
      : "Foundation route complete",
  );
  const modeledMinutes = Number(foundation.estimated_completed_minutes || 0);
  const targetMinutes = Number(foundation.target_minutes || 120);
  const campaignPercent = Number(foundation.campaign_percent ?? foundation.percent ?? 0);
  const competencyPercent = Number(foundation.competency_percent ?? 0);
  setMeter(
    ".foundation-meter",
    campaignPercent,
    `${modeledMinutes} of ${targetMinutes} modeled minutes · ${Number(foundation.completed_competencies || 0)} of ${Number(foundation.competency_total || 0)} competencies (${competencyPercent}%)`,
  );

  const difficulty = foundation.difficulty || state.difficulty_curve || {};
  const difficultyModifiers = difficulty.modifiers || {};
  const difficultyInjury = difficulty.injury || {};
  const difficultyStrip = $("#difficulty-strip");
  difficultyStrip.dataset.band = String(difficulty.band_id || "guided_foundation");
  setText("#difficulty-heading", String(difficulty.label || "Guided foundation").toUpperCase());
  setText(
    "#difficulty-summary",
    difficulty.summary || (journeyPhase
      ? "Levels 11–14 establish the route before the level 15–18 sensory-echo shock."
      : "Levels 1–4 teach the field loop before the level 5–8 survival shock."),
  );
  setText(
    "#difficulty-offdef",
    `${Number(difficultyModifiers.enemy_offense || 0) >= 0 ? "+" : ""}${Number(difficultyModifiers.enemy_offense || 0)} / ${Number(difficultyModifiers.enemy_defense || 0) >= 0 ? "+" : ""}${Number(difficultyModifiers.enemy_defense || 0)}`,
  );
  setText(
    "#difficulty-damage",
    `${Number(difficultyModifiers.enemy_damage_min || 0) >= 0 ? "+" : ""}${Number(difficultyModifiers.enemy_damage_min || 0)} / ${Number(difficultyModifiers.enemy_damage_max || 0) >= 0 ? "+" : ""}${Number(difficultyModifiers.enemy_damage_max || 0)}`,
  );
  setText(
    "#difficulty-injury",
    difficultyInjury.active
      ? `SEV ${Number(difficultyInjury.severity || 0)}`
      : difficultyInjury.rehabilitated
        ? "REHABILITATED"
        : "NOT ACTIVE",
  );
  setText(
    "#difficulty-recovery",
    difficultyInjury.active
      ? difficultyInjury.recovery || "Stabilize, rest, and complete field milestones."
      : difficultyInjury.rehabilitated
        ? journeyPhase
          ? "The echo remains in the story record, but levels 19–20 use average combat pressure."
          : "The scar remains in the story, but levels 9–10 use average combat pressure."
        : journeyPhase
          ? "Levels 15–18 introduce a visible sensory-echo trial; levels 19–20 return to average pressure."
          : "Levels 5–8 introduce a visible injury trial; levels 9–10 return to average pressure.",
  );

  const calibration = foundation.calibration || {};
  const calibrationStatus = String(calibration.status || "GREEN").toUpperCase();
  const routeStatus = String(calibration.route_status || calibrationStatus).toUpperCase();
  const combatStatus = String(calibration.combat_status || "GREEN").toUpperCase();
  setText("#calibration-heading", `${calibrationStatus} · ROUTE ${routeStatus} · COMBAT ${combatStatus}`);
  setText("#calibration-summary", calibration.summary || "No sustained friction signal.");
  setText("#calibration-stall", String(Number(calibration.commands_since_progress || 0)));
  setText("#calibration-blocks", String(Number(calibration.current_combat_repetition || 0)));
  setText("#calibration-recoveries", String(Number(calibration.recoveries || 0)));
  const calibrationStrip = $("#calibration-strip");
  calibrationStrip.dataset.status = calibrationStatus.toLowerCase();
  calibrationStrip.title = `${calibration.route_summary || "Route status unavailable"} ${calibration.combat_summary || "Combat status unavailable"}`;

  const resume = foundation.resume_briefing || {};
  const resumeRoute = resume.route || {};
  setText(
    "#resume-briefing-heading",
    resume.chapter_title
      ? `${resume.chapter_title} · level ${Number(resume.level || foundation.current_level || 1)}`
      : "Current objective and return route",
  );
  setText("#resume-checkpoint", resume.checkpoint_label || "Checkpoint preserved");
  setText("#resume-objective", resume.objective || "No active story directive.");
  setText("#resume-route", resumeRoute.summary || "No route guidance is currently required.");
  setText("#resume-sol", resume.sol?.status || "No active field partner.");
  const resumeRouteButton = $("#resume-route-button");
  resumeRouteButton.disabled = !Boolean(resumeRoute.active);
  resumeRouteButton.dataset.command = resume.route_command || "route objective";

  const assignment = foundation.class_assignment || null;
  setText("#foundation-class-title", assignment?.title || "Choose a class foundation");
  setText("#foundation-class-objective", assignment?.objective || "Your class-specific field objective will appear after character confirmation.");
  $("#foundation-class-assignment").classList.toggle("complete", Boolean(assignment?.complete));

  const chapterList = $("#foundation-chapters");
  clearNode(chapterList);
  for (const chapter of chapters) {
    const row = element("div", `foundation-chapter ${chapter.complete ? "complete" : chapter.active ? "active" : "pending"}`);
    row.append(
      element("span", "foundation-chapter-state", chapter.complete ? "✓" : chapter.active ? "◆" : "○"),
      element("strong", "", chapter.title || titleCase(chapter.id)),
      element("small", "", `${Number(chapter.earned_minutes || 0)}/${Number(chapter.minutes || 0)} min`),
    );
    row.title = chapter.summary || chapter.title || (journeyPhase ? "Journey chapter" : "Foundation chapter");
    chapterList.append(row);
  }

  const competencyList = $("#foundation-competencies");
  clearNode(competencyList);
  for (const competency of competencies) {
    const chip = element("div", `foundation-competency ${competency.complete ? "complete" : "pending"}`);
    chip.append(
      element("span", "", competency.complete ? "✓" : "○"),
      element("strong", "", competency.label || titleCase(competency.id)),
    );
    chip.title = competency.description || competency.label || (journeyPhase ? "Journey competency" : "Foundation competency");
    competencyList.append(chip);
  }
}

function renderParty(state) {
  const party = state.party || {};
  const story = state.story || {};
  const relevant = Boolean(
    party.formed
      || party.complete
      || party.active
      || story.quest_id === "one_report_many_lives",
  );
  const card = $("#party-detail");
  card.hidden = !relevant;
  if (!relevant) return;

  setText("#party-detail-heading", party.complete ? "Relief detail closed" : "Relief detail");
  setText("#party-detail-status", party.status || "No temporary detail active");
  setText(
    "#party-detail-summary",
    `${party.authority_scope || "Temporary field coordination."} Report: ${titleCase(party.report_status || "not formed")}. Order: ${party.order || "Not issued"}.`,
  );
  setText(
    "#party-detail-boundary",
    `${party.authority_expiration || "Authority expires with the task."} ${party.boundary || ""}`.trim(),
  );

  const roleList = $("#party-role-list");
  clearNode(roleList);
  for (const role of party.roles || []) {
    const chip = element("div", `party-role ${role.active ? "active" : "inactive"}`);
    chip.append(
      element("span", "party-role-label", role.role || "Role"),
      element("strong", "", role.name || "Unassigned"),
      element("small", "", role.function || "No field duty assigned."),
    );
    roleList.append(chip);
  }
  if (!(party.roles || []).length) {
    roleList.append(emptyNote("No temporary field roles are published."));
  }

  const action = $("#party-detail-action");
  const available = (party.actions || []).find((entry) => entry.available);
  action.dataset.command = available?.command || party.primary_command || "party";
  action.textContent = available?.label || (party.complete ? "Review closed detail" : "Open party status");
  action.disabled = false;
  card.dataset.status = party.complete ? "complete" : party.active ? "active" : "ready";
}

function renderReport(state) {
  const report = state.report || {};
  const story = state.story || {};
  const classLens = report.mode === "class_lens";
  const relevant = Boolean(
    report.formed
      || report.complete
      || report.active
      || story.quest_id === "report_that_arrived_twice"
      || story.quest_id === "fifteen_lenses_one_truth",
  );
  const card = $("#field-report");
  card.hidden = !relevant;
  if (!relevant) return;

  setText(
    "#field-report-heading",
    classLens
      ? report.complete ? "Class evidence lens closed" : "Class evidence lens"
      : report.complete ? "Field report closed" : "Report reliability",
  );
  setText("#field-report-status", report.status || "No bounded report active");
  setText(
    "#field-report-summary",
    report.scope || "One bounded movement decision.",
  );
  setText(
    "#field-report-boundary",
    `${report.expiration || "Authority expires with the decision."} ${report.boundary || ""}`.trim(),
  );

  const facts = $("#field-report-facts");
  clearNode(facts);
  const entries = classLens
    ? [
      ["Class lens", `${report.class_name || "Unselected"} — ${report.lens_name || "Not applied"}`],
      ["Fact", report.fact || "No verified fact separated"],
      ["Inference", report.inference || "Not yet applied"],
      ["Unknown", report.unknown || "Not yet declared"],
      ["Signal state", report.signal_state || "UNCLASSIFIED"],
      ["Review condition", report.review_condition || "Not yet declared"],
    ]
    : [
      ["Sources", report.source_count || "Unclassified"],
      ["Rule", report.doctrine || "Not selected"],
      ["Interference", report.interference || "None recorded"],
      ["Outcome", report.outcome || "Not published"],
    ];
  for (const [label, value] of entries) {
    const fact = element("div", "field-report-fact");
    fact.append(
      element("span", "", label),
      element("strong", "", value),
    );
    if (label === "Rule") fact.title = report.confidence || value;
    facts.append(fact);
  }

  const action = $("#field-report-action");
  const available = (report.actions || []).find((entry) => entry.available);
  action.dataset.command = available?.command || report.primary_command || "report";
  action.textContent = available?.label || (report.complete ? "Review closed evidence" : "Open report status");
  action.disabled = false;
  card.dataset.status = report.complete ? "complete" : report.active ? "active" : "ready";
}


function renderDistrict(state) {
  const district = state.district || {};
  const story = state.story || {};
  const relevant = Boolean(
    district.formed
      || district.complete
      || district.active
      || story.quest_id === "the_road_that_changes_meaning",
  );
  const card = $("#district-passage");
  card.hidden = !relevant;
  if (!relevant) return;

  setText(
    "#district-passage-heading",
    district.complete ? "District 22 passage closed" : "District 22 public passage",
  );
  setText("#district-passage-status", district.status || "No public district passage active");
  setText(
    "#district-passage-summary",
    `${district.destination || "District 22, Shaklas Public Queue"}. ${district.fact || "No verified public-passage fact accepted."}`,
  );
  setText(
    "#district-passage-boundary",
    `${district.expiration || "Passage permission expires with the task."} ${district.boundary || ""}`.trim(),
  );

  const facts = $("#district-passage-facts");
  clearNode(facts);
  const entries = [
    ["Class caution", `${district.class_name || "Unselected"} — ${district.caution_name || "Not read"}`],
    ["Caution", district.caution || "Not yet read"],
    ["Preparation", `${district.preparation || "Not selected"} — ${district.preparation_summary || "No preparation recorded."}`],
    ["Window", district.window || "Not yet reverified"],
    ["Review condition", district.review_condition || "Not yet declared"],
    ["Unknown", district.unknown || "Conditions beyond the public passage remain unknown"],
  ];
  for (const [label, value] of entries) {
    const fact = element("div", "district-passage-fact");
    fact.append(
      element("span", "", label),
      element("strong", "", value),
    );
    facts.append(fact);
  }

  const action = $("#district-passage-action");
  const available = (district.actions || []).find((entry) => entry.available);
  action.dataset.command = available?.command || district.primary_command || "district";
  action.textContent = available?.label || (district.complete ? "Review closed passage" : "Open district status");
  action.disabled = false;
  card.dataset.status = district.complete ? "complete" : district.active ? "active" : "ready";
}

function renderService(state) {
  const service = state.service || {};
  const story = state.story || {};
  const relevant = Boolean(
    service.formed
      || service.complete
      || service.active
      || story.quest_id === "the_public_queue_remembers",
  );
  const card = $("#public-service-review");
  card.hidden = !relevant;
  if (!relevant) return;

  setText(
    "#public-service-heading",
    service.complete ? "Shaklas queue review closed" : "Shaklas public queue memory",
  );
  setText("#public-service-status", service.status || "No public-service review active");
  setText(
    "#public-service-summary",
    `${service.location || "District 22, Shaklas Public Queue"}. ${service.fact || "No queue-memory fact accepted."}`,
  );
  setText(
    "#public-service-boundary",
    `${service.expiration || "Temporary review authority expires at closure."} ${service.boundary || ""}`.trim(),
  );

  const history = service.history || {};
  const facts = $("#public-service-facts");
  clearNode(facts);
  const entries = [
    ["Sources", service.source_state || "Not yet traced"],
    ["Class caution", `${service.class_name || "Unselected"} — ${service.class_caution || "Not available"}`],
    ["Remembered road", `Shelter: ${history.shelter || "none"}; caravan: ${history.caravan || "none"}; report: ${history.report || "none"}; passage: ${history.passage || "none"}`],
    ["Suggested emphasis", service.suggested_method || "Any equal method"],
    ["Selected method", `${service.method || "Not selected"} — ${service.method_summary || "No temporary method selected."}`],
    ["Window", service.window || "Not yet verified"],
    ["Unknown", service.unknown || "Cause, intent, clinical priority, and deeper conditions remain unknown"],
  ];
  for (const [label, value] of entries) {
    const fact = element("div", "public-service-fact");
    fact.append(
      element("span", "", label),
      element("strong", "", value),
    );
    facts.append(fact);
  }

  const action = $("#public-service-action");
  const available = (service.actions || []).find((entry) => entry.available);
  action.dataset.command = available?.command || service.primary_command || "service";
  action.textContent = available?.label || (service.complete ? "Review closed service record" : "Open service status");
  action.disabled = false;
  card.dataset.status = service.complete ? "complete" : service.active ? "active" : "ready";
}

function renderHospice(state) {
  const hospice = state.hospice || {};
  const appeal = state.appeal || {};
  const wayfinding = state.wayfinding || {};
  const story = state.story || {};
  const correction = Boolean(appeal.formed || story.quest_id === "the_appeal_is_not_a_verdict");
  const liveRoute = Boolean(wayfinding.formed || story.quest_id === "the_map_is_not_the_road");
  const receipt = Boolean(hospice.receipt_formed || story.quest_id === "the_receipt_travels_without_you");
  const influence = Boolean(hospice.influence_formed || story.quest_id === "the_name_on_the_gift");
  const stewardship = Boolean(hospice.stewardship_formed || story.quest_id === "the_light_is_borrowed");
  const relevant = Boolean(
    liveRoute
      || correction
      || receipt
      || influence
      || stewardship
      || hospice.formed
      || hospice.complete
      || hospice.active
      || story.quest_id === "the_threshold_has_a_cost",
  );
  const card = $("#hospice-threshold");
  card.hidden = !relevant;
  if (!relevant) return;

  const functions = Array.isArray(hospice.public_functions)
    ? hospice.public_functions.join(", ")
    : "Not yet separated";
  const facts = $("#hospice-threshold-facts");
  clearNode(facts);

  let entries;
  let actions;
  let primaryCommand;
  let complete;
  let active;

  if (liveRoute) {
    complete = Boolean(wayfinding.complete);
    active = Boolean(wayfinding.active);
    setText("#hospice-threshold-kicker", "LIVE ROUTE · CONDITION & EXPIRATION");
    setText(
      "#hospice-threshold-heading",
      complete ? "Live-route review closed" : "The map is not the road",
    );
    setText(
      "#hospice-threshold-status",
      wayfinding.status || "No live-route review active",
    );
    setText(
      "#hospice-threshold-summary",
      `${wayfinding.location || "Shaklas public route"}. ${wayfinding.fact || "No live-route fact accepted."}`,
    );
    setText(
      "#hospice-threshold-boundary",
      `${wayfinding.expiration || "Route-review authority is not active."} ${wayfinding.boundary || ""}`.trim(),
    );
    entries = [
      ["Copied waymark", wayfinding.stale_claim || "OPEN — PUBLIC HOSPICE RETURN"],
      ["Live condition", wayfinding.condition || "Not yet verified"],
      ["Class caution", `${wayfinding.class_name || "Unselected"} — ${wayfinding.class_caution || "Not available"}`],
      ["Selected method", `${wayfinding.method || "Not selected"} — ${wayfinding.method_summary || "No route-safety method selected."}`],
      ["Operative status", wayfinding.operative_status || "Stale sign remains non-operative"],
      ["Unknown", wayfinding.unknown || "Copier, motive, faction, reliance, future conditions, and deeper streets remain unknown"],
      ["Sol / companion", wayfinding.active_companion?.name ? `${wayfinding.active_companion.name} remains a separate companion with no route or tracking authority` : "No active companion authority"],
    ];
    actions = wayfinding.actions || [];
    primaryCommand = wayfinding.primary_command || "wayfinding status";
  } else if (correction) {
    complete = Boolean(appeal.complete);
    active = Boolean(appeal.active);
    setText("#hospice-threshold-kicker", "PUBLIC INDEX · SOURCE-BACKED CORRECTION");
    setText(
      "#hospice-threshold-heading",
      complete ? "Public-index appeal closed" : "The appeal is not a verdict",
    );
    setText(
      "#hospice-threshold-status",
      appeal.status || "No public-index appeal active",
    );
    setText(
      "#hospice-threshold-summary",
      `${appeal.location || "Shaklas public index route"}. ${appeal.fact || "No index mismatch accepted."}`,
    );
    setText(
      "#hospice-threshold-boundary",
      `${appeal.expiration || "Appeal authority is not active."} ${appeal.boundary || ""}`.trim(),
    );
    entries = [
      ["Unsupported label", appeal.unsupported_label || "sponsored restoration"],
      ["Governing supplier record", `${appeal.supplier_method || "Unknown"} — ${appeal.source_line || "Source line not available"}`],
      ["Class caution", `${appeal.class_name || "Unselected"} — ${appeal.class_caution || "Not available"}`],
      ["Selected remedy", `${appeal.remedy || "Not selected"} — ${appeal.remedy_summary || "No correction method selected."}`],
      ["Operative status", appeal.operative_status || "Unsupported label remains uncorrected"],
      ["Unknown", appeal.unknown || "Author, motive, reliance, harm, and faction involvement remain unknown"],
      ["Sol / companion", appeal.active_companion?.name ? `${appeal.active_companion.name} remains a separate companion with no correction authority` : "No active companion authority"],
    ];
    actions = appeal.actions || [];
    primaryCommand = appeal.primary_command || "appeal status";
  } else if (receipt) {
    complete = Boolean(hospice.receipt_complete);
    active = Boolean(hospice.receipt_active);
    setText("#hospice-threshold-kicker", "PUBLIC RECEIPT · SCOPE & EXPIRATION");
    setText(
      "#hospice-threshold-heading",
      complete ? "Copied-receipt review closed" : "The receipt travels without you",
    );
    setText(
      "#hospice-threshold-status",
      hospice.receipt_status || "No copied-receipt review active",
    );
    setText(
      "#hospice-threshold-summary",
      `${hospice.receipt_location || "Shaklas public receipt route"}. ${hospice.receipt_fact || "No copied-receipt fact accepted."}`,
    );
    setText(
      "#hospice-threshold-boundary",
      `${hospice.receipt_expiration || "Receipt-review authority is not active."} ${hospice.receipt_boundary || ""}`.trim(),
    );
    entries = [
      ["Prior transaction line", hospice.receipt_transaction_line || "Not yet available"],
      ["Public owner / sponsor", "None — a receipt proves one transaction, not title, sponsorship, access, or command authority"],
      ["Private and clinical boundary", hospice.receipt_private_boundary || "Not yet verified"],
      ["Class caution", `${hospice.class_name || "Unselected"} — ${hospice.receipt_class_caution || "Not available"}`],
      ["Selected method", `${hospice.receipt_method || "Not selected"} — ${hospice.receipt_summary || "No receipt-scope method selected."}`],
      ["Detail lifecycle", hospice.receipt_lifecycle || "Not yet declared"],
      ["Published scope", hospice.receipt_scope_line || "Not yet published"],
      ["Expiration test", hospice.receipt_expiration || "Not yet exercised"],
      ["Unknown", hospice.receipt_unknown || "Copier, motive, distribution count, faction use, and deeper conditions remain unknown"],
    ];
    actions = hospice.receipt_actions || [];
    primaryCommand = hospice.receipt_primary_command || "hospice status";
  } else if (influence) {
    complete = Boolean(hospice.influence_complete);
    active = Boolean(hospice.influence_active);
    setText("#hospice-threshold-kicker", "PUBLIC CELL · SOURCE & INFLUENCE TERMS");
    setText(
      "#hospice-threshold-heading",
      complete ? "Supplier-offer review closed" : "The name on the gift",
    );
    setText(
      "#hospice-threshold-status",
      hospice.influence_status || "No supplier-offer review active",
    );
    setText(
      "#hospice-threshold-summary",
      `${hospice.influence_location || "Shaklas public supplier route"}. ${hospice.influence_fact || "No supplier-offer fact accepted."}`,
    );
    setText(
      "#hospice-threshold-boundary",
      `${hospice.influence_expiration || "Supplier-offer authority is not active."} ${hospice.influence_boundary || ""}`.trim(),
    );
    entries = [
      ["Public owner line", "Blank by design — supply, acknowledgement, payment, repair, or scheduling grants no title"],
      ["Sponsorship line", "None accepted — a source name is not a sponsor, pass, faction mark, or command office"],
      ["Public functions", functions || "Privacy screen, accountable receipt seal, and return beacons"],
      ["Clinical systems", hospice.isolation || "Medical authority remains with trained hospice staff"],
      ["Class caution", `${hospice.class_name || "Unselected"} — ${hospice.influence_class_caution || "Not available"}`],
      ["Selected method", `${hospice.influence_method || "Not selected"} — ${hospice.influence_summary || "No supplier-offer method selected."}`],
      ["Consideration", hospice.influence_consideration || "Not yet declared"],
      ["Refusal", hospice.influence_refusal || "Not yet verified"],
      ["Public source line", hospice.influence_source_line || "Not yet published"],
      ["Verified terms", hospice.influence_terms || "Not yet verified"],
      ["Independent fallback", hospice.influence_fallback || "Not yet tested"],
      ["Unknown", hospice.influence_unknown || "Upstream source, faction affiliation, future supply, donor, clinical capacity, and deeper conditions remain unknown"],
    ];
    actions = hospice.influence_actions || [];
    primaryCommand = hospice.influence_primary_command || "hospice status";
  } else if (stewardship) {
    complete = Boolean(hospice.stewardship_complete);
    active = Boolean(hospice.stewardship_active);
    setText("#hospice-threshold-kicker", "PUBLIC CELL · BORROWED-LIGHT STEWARDSHIP");
    setText(
      "#hospice-threshold-heading",
      complete ? "Borrowed-light stewardship closed" : "The light is borrowed",
    );
    setText(
      "#hospice-threshold-status",
      hospice.stewardship_status || "No borrowed-light stewardship active",
    );
    setText(
      "#hospice-threshold-summary",
      `${hospice.stewardship_location || "Shaklas public-cell route"}. ${hospice.stewardship_fact || "No borrowed-light fact accepted."}`,
    );
    setText(
      "#hospice-threshold-boundary",
      `${hospice.stewardship_expiration || "Borrowed-light authority is not active."} ${hospice.stewardship_boundary || ""}`.trim(),
    );
    entries = [
      ["Public owner line", "Blank by design — repair, payment, parts supply, or scheduling grants no title"],
      ["Public functions", functions || "Privacy screen, accountable receipt seal, and return beacons"],
      ["Clinical systems", hospice.isolation || "Medical authority remains with trained hospice staff"],
      ["Class caution", `${hospice.class_name || "Unselected"} — ${hospice.stewardship_class_caution || "Not available"}`],
      ["Selected method", `${hospice.stewardship_method || "Not selected"} — ${hospice.stewardship_summary || "No borrowed-light method selected."}`],
      ["Declared cost", hospice.stewardship_cost || "Not yet declared"],
      ["Refusal / abort", hospice.stewardship_refusal || "Not yet declared"],
      ["Verified terms", hospice.stewardship_terms || "Not yet verified"],
      ["Neutral fallback", hospice.stewardship_fallback || "Not yet tested"],
      ["Unknown", hospice.stewardship_unknown || "Owner, donor, remaining cell life, faction involvement, and deeper conditions remain unknown"],
    ];
    actions = hospice.stewardship_actions || [];
    primaryCommand = hospice.stewardship_primary_command || "hospice status";
  } else {
    complete = Boolean(hospice.complete);
    active = Boolean(hospice.active);
    setText("#hospice-threshold-kicker", "HOSPICE THRESHOLD · NONCLINICAL CAPACITY");
    setText(
      "#hospice-threshold-heading",
      complete ? "Threshold-capacity lesson closed" : "The threshold has a cost",
    );
    setText("#hospice-threshold-status", hospice.status || "No threshold-capacity review active");
    setText(
      "#hospice-threshold-summary",
      `${hospice.location || "Shaklas public hospice threshold"}. ${hospice.fact || "No threshold-capacity fact accepted."}`,
    );
    setText(
      "#hospice-threshold-boundary",
      `${hospice.expiration || "Temporary threshold authority expires at public-ledger closure."} ${hospice.boundary || ""}`.trim(),
    );
    entries = [
      ["Public functions", functions || "Not yet separated"],
      ["Clinical systems", hospice.isolation || "Medical authority remains with hospice staff"],
      ["Class caution", `${hospice.class_name || "Unselected"} — ${hospice.class_caution || "Not available"}`],
      ["Selected method", `${hospice.method || "Not selected"} — ${hospice.method_summary || "No temporary method selected."}`],
      ["Declared cost", hospice.cost || "Not yet declared"],
      ["Return route", hospice.return_route || "Not yet verified"],
      ["Unknown", hospice.unknown || "Donor, remaining lifespan, future capacity, and deeper conditions remain unknown"],
    ];
    actions = hospice.actions || [];
    primaryCommand = hospice.primary_command || "hospice status";
  }

  for (const [label, value] of entries) {
    const fact = element("div", "hospice-threshold-fact");
    fact.append(
      element("span", "", label),
      element("strong", "", value),
    );
    facts.append(fact);
  }

  const action = $("#hospice-threshold-action");
  const available = actions.find((entry) => entry.available);
  action.dataset.command = available?.command || primaryCommand;
  action.textContent = available?.label || (complete ? "Review closed public-cell record" : "Open hospice status");
  action.disabled = false;
  card.dataset.status = complete ? "complete" : active ? "active" : "ready";
}

function renderPlaytest(state) {
  const timing = state.playtest || {};
  const profile = timing.profile || {};
  const status = String(timing.status || "not_started");
  setText("#playtest-status", status.replaceAll("_", " ").toUpperCase());
  setText("#playtest-active", timing.active_text || "00:00:00");
  setText("#playtest-idle", timing.idle_text || "00:00:00");
  setText("#playtest-paused", timing.paused_text || "00:00:00");
  setText("#playtest-wall", timing.wall_text || "00:00:00");
  setText("#playtest-basis", timing.timing_basis || "Timing is local, reward-neutral, and never sent over the network.");
  setText(
    "#playtest-profile",
    `Profile: ${String(profile.family || "unassigned").replaceAll("_", " ")} · `
      + `${String(profile.class_name || profile.class_id || "class pending")} · `
      + `${String(profile.mode || "standard").replaceAll("_", " ")} · `
      + `${String(profile.experience || "unspecified").replaceAll("_", " ")}`,
  );
  setText(
    "#playtest-command-count",
    `${Number(timing.command_count || 0)} timed commands · ${Number(timing.notes_count || 0)} notes · `
      + `${Number(timing.issues_count || 0)} structured issues · receipt stays local`,
  );
  const always = ["plan", "checklist", "issues"];
  const allowed = {
    not_started: new Set([...always, "start"]),
    running: new Set([...always, "pause", "complete", "receipt"]),
    paused: new Set([...always, "resume", "complete", "receipt"]),
    completed: new Set([...always, "receipt"]),
  }[status] || new Set([...always, "start"]);
  $$('[data-playtest-action]').forEach((button) => {
    button.disabled = !allowed.has(button.dataset.playtestAction);
  });
  $("#playtest-clock").dataset.status = status;
}

function renderProgress(state) {
  const story = state.story || {};
  const storyActive = Boolean(story.active);
  const checkpointReached = Boolean(story.checkpoint_id);
  const checkpointLabel = story.checkpoint_label || titleCase(story.checkpoint_id || "opening arc");
  setText("#story-card-heading", story.arc_title || checkpointLabel || "First Watch");
  setText(
    "#story-quest-name",
    storyActive
      ? `${story.quest_title} · ${story.stage_title}`
      : checkpointReached
        ? `${checkpointLabel} complete`
        : "No active story directive",
  );
  setText(
    "#story-objective",
    storyActive
      ? story.objective
      : checkpointReached
        ? "The Sprawl remembers your decisions. Review the checkpoint, records, and relationships here."
        : "Your defining decisions and relationships will remain visible here.",
  );
  const progressIndex = Number(story.progress_index || 0);
  const progressTotal = Number(story.progress_total || 0);
  const storyPercent = storyActive && progressTotal
    ? progressIndex / progressTotal * 100
    : checkpointReached
      ? 100
      : 0;
  setMeter(
    ".story-meter",
    storyPercent,
    storyActive && progressTotal
      ? `Step ${progressIndex} of ${progressTotal}`
      : checkpointReached
        ? `${checkpointLabel} checkpoint reached`
        : "Story contact not established",
  );
  setText(
    "#story-progress-label",
    storyActive && progressTotal
      ? `Step ${progressIndex} of ${progressTotal}`
      : checkpointReached
        ? `${(story.completed_quests || []).length} quests completed`
        : "Awaiting story contact",
  );
  setText(
    "#story-checkpoint",
    checkpointReached
      ? `${checkpointLabel} checkpoint reached`
      : `${(story.records || []).length} sovereignty ${(story.records || []).length === 1 ? "record" : "records"}`,
  );
  const contactList = $("#story-contact-list");
  clearNode(contactList);
  for (const contact of story.contacts || []) {
    const chip = element("button", "relationship-chip");
    chip.type = "button";
    chip.dataset.command = contact.route_command || contact.talk_command || "quest";
    chip.disabled = !contact.known;
    chip.append(
      element("span", "", contact.name || "Unknown contact"),
      element("strong", "", contact.known ? contact.room_title : "Unmapped"),
    );
    chip.title = contact.known
      ? `Route to ${contact.name} at ${contact.room_title}`
      : `${contact.name} is not yet in your spatial memory.`;
    contactList.append(chip);
  }
  if (!(story.contacts || []).length) {
    contactList.append(emptyNote("No story contact is required at this step."));
  }

  const routeInterest = story.route_interest || null;
  setText(
    "#story-route-interest",
    routeInterest
      ? routeInterest.handoff_ready
        ? `${routeInterest.faction_name} has opened the ${routeInterest.route_label} handoff. This is passage, not allegiance; membership remains ${routeInterest.membership_status}.`
        : `${routeInterest.faction_name} has noticed your ${routeInterest.route_label} pattern. This is observation only; membership remains ${routeInterest.membership_status}.`
      : "No faction route has formally noticed your work. Membership remains unassigned.",
  );
  const readiness = story.readiness || { completed: 0, total: 0, percent: 0, items: [] };
  setText("#story-readiness-heading", readiness.title || "Unknown confrontation");
  setText(
    "#story-readiness-summary",
    readiness.summary || `${Number(readiness.completed || 0)} of ${Number(readiness.total || 0)} preparations secured.`,
  );
  setMeter(
    ".readiness-meter",
    Number(readiness.percent || 0),
    `${Number(readiness.completed || 0)} of ${Number(readiness.total || 0)} preparations secured`,
  );
  const readinessGrid = $("#story-readiness-grid");
  clearNode(readinessGrid);
  for (const item of readiness.items || []) {
    const chip = element("div", `readiness-chip ${item.complete ? "complete" : "pending"}`);
    chip.setAttribute("role", "listitem");
    chip.title = item.detail || item.label || "Preparation";
    chip.append(
      element("span", "readiness-state", item.complete ? "✓" : "○"),
      element("strong", "", item.label || titleCase(item.id)),
    );
    readinessGrid.append(chip);
  }
  if (!(readiness.items || []).length) {
    readinessGrid.append(emptyNote("Preparation tracking begins when the pattern emerges."));
  }

  const sprawlPulse = story.sprawl_pulse || { title: "Sprawl 15 pulse", summary: "Local consequences pending", items: [] };
  setText("#sprawl-pulse-heading", sprawlPulse.title || "Sprawl 15 pulse");
  setText("#sprawl-pulse-summary", sprawlPulse.summary || "Local consequences pending");
  const pulseGrid = $("#sprawl-pulse-grid");
  clearNode(pulseGrid);
  for (const item of sprawlPulse.items || []) {
    const chip = element("div", "sprawl-pulse-item");
    chip.append(
      element("strong", "", item.label || titleCase(item.id)),
      element("span", "", item.status || "Unresolved"),
    );
    pulseGrid.append(chip);
  }
  if (!(sprawlPulse.items || []).length) pulseGrid.append(emptyNote("The Sprawl has not recorded a local change yet."));

  const relationshipList = $("#story-relationship-list");
  clearNode(relationshipList);
  for (const relationship of story.relationships || []) {
    const chip = element("button", "relationship-chip");
    chip.type = "button";
    chip.dataset.command = "quest relationships";
    chip.append(
      element("span", "", relationship.name),
      element(
        "strong",
        "",
        `${relationship.score >= 0 ? "+" : ""}${relationship.score} · ${titleCase(relationship.standing)}`,
      ),
    );
    chip.title = `${relationship.label} with ${relationship.name}: ${relationship.standing}`;
    relationshipList.append(chip);
  }
  if (!(story.relationships || []).length) {
    relationshipList.append(emptyNote("No lasting relationship readings yet."));
  }

  const training = state.character?.training || {};
  const course = state.character?.course || {};
  setText("#physical-points", training.physical_points ?? 0);
  setText("#mental-points", training.mental_points ?? 0);
  const active = (course.catalog || []).find((entry) => entry.id === course.active_course_id);
  setText("#course-name", active?.name || "No active course");
  if (course.next_step) {
    setText("#course-step", `${course.next_step.number}/${course.next_step.total} · ${course.next_step.description}`);
    setMeter(
      ".course-meter",
      (course.next_step.number - 1) / Math.max(1, course.next_step.total) * 100,
      `Step ${course.next_step.number} of ${course.next_step.total}`,
    );
  } else {
    setText("#course-step", "Visit a course station to begin optional guidance.");
    setMeter(".course-meter", 0, "No active readiness course");
  }

  const build = state.character?.build || {};
  const buildClass = build.class?.name
    || (build.status === "legacy_preserved" ? "Legacy foundation" : "Unselected");
  setText("#build-card-class", buildClass);
  setText(
    "#build-card-route",
    build.faction_route
      ? `${build.faction_route.route_label} · faction membership unassigned.`
      : "Faction route unassigned.",
  );
  const baseSummary = (build.attributes || [])
    .map((attribute) => `${attribute.abbreviation} ${attribute.base_value}`)
    .join(" · ");
  setText(
    "#build-card-allocation",
    build.allocation_mode === "legacy"
      ? `${baseSummary || "Original attributes"} · legacy values preserved.`
      : build.allocation_mode
        ? `${titleCase(build.allocation_mode)} · ${build.spent}/${build.budget} weighted points · ${baseSummary}`
        : "No allocation recorded.",
  );

  const journal = state.journal || {};
  setText("#journal-locations", journal.location_count ?? 0);
  setText("#journal-clues", journal.clue_count ?? 0);
  setText("#journal-courses", journal.course_count ?? 0);
  setText("#journal-victories", journal.victory_count ?? 0);
  setText("#journal-sovereignty", journal.sovereignty_count ?? 0);
}

function renderSupportExport(state) {
  const support = state.support_export || {};
  const status = String(support.status || "not-generated");
  const current = status === "current";
  const partial = status === "partial";
  setText(
    "#support-export-status",
    current
      ? `Current · ${support.item_count || 0}/${support.item_limit || 20} files · checksum recorded.`
      : partial
        ? `Created with warnings · ${(support.collector_errors || []).join(", ") || "review SUMMARY.json"}.`
        : support.message || "Your support package will be created when a character opens.",
  );
  setText(
    "#support-export-path",
    support.relative_path || "SUPPORT_EXPORTS / UPLOAD_THIS_…zip",
  );
  const button = $("#support-export-button");
  button.disabled = app.requestPending;
  button.textContent = status === "error" ? "Retry Support Package" : "Update Support Package";
  button.title = support.sha256
    ? `SHA-256 ${support.sha256}`
    : "Create a fresh privacy-screened support package.";
}

async function refreshSupportExport() {
  if (app.requestPending) return;
  setRequestPending(true);
  setSync("busy", "PREPARING");
  try {
    const document = await api("/api/diagnostics/export", {});
    const support = document.support_export || {};
    if (app.state) {
      app.state.support_export = support;
      renderSupportExport(app.state);
    }
    const path = support.relative_path || support.filename || "SUPPORT_EXPORTS";
    appendTranscript(
      `Support package updated and ready to upload.\n${path}\n${support.sha256 ? `File verification: ${support.sha256}` : "File verification unavailable."}`,
      "system",
    );
    toast("Support package is ready to upload.");
    setSync("ready");
  } catch (error) {
    appendTranscript(error.message, "error");
    toast(error.message, "error");
    setSync("error");
  } finally {
    setRequestPending(false);
  }
}

function renderDirective(state) {
  const directive = state.directive;
  const strip = $("#directive-strip");
  const disclosure = $("#directive-disclosure");
  const action = $("#directive-action");
  const pause = $("#directive-hide");
  const progress = $("#directive-progress");
  const choices = $("#directive-choice-list");
  const companion = $("#guide-companion");
  const guideAction = $("#guide-companion-action");
  const engaged = Boolean((state.room?.creatures || []).length);
  const guidance = state.guidance || {};
  const guideStep = guidance.step || directive?.guide || null;
  const showCompanion = Boolean(guideStep) && directive?.kind !== "tutorial" && !engaged;
  companion.hidden = !showCompanion;
  if (showCompanion) {
    setText("#guide-companion-progress", `${guideStep.step_number || 0}/${guideStep.step_total || 0}`);
    setText("#guide-companion-title", guideStep.description || "Guided Start step");
    setText("#guide-companion-why", `${guideStep.why || "Optional, reward-free guidance."} Actions completed early are remembered.`);
    const guideCommand = String(guideStep.suggested_command || "guide sync");
    guideAction.dataset.command = guideCommand;
    guideAction.textContent = titleCase(guideCommand);
    guideAction.setAttribute("aria-label", `Guided Start: ${guideStep.description}. Use ${guideCommand}.`);
  } else {
    guideAction.removeAttribute("data-command");
  }

  strip.hidden = !directive;
  clearNode(choices);
  choices.hidden = true;
  progress.hidden = true;
  if (!directive) {
    companion.hidden = true;
    action.removeAttribute("data-command");
    pause.removeAttribute("data-command");
    if (disclosure) disclosure.open = false;
    return;
  }

  setText("#directive-tag", engaged ? "AFTER CONTACT" : directive.tag || "CURRENT DIRECTIVE");
  setText("#directive-summary-title", directive.title || "Review your next step");
  setText("#directive-summary-progress", engaged ? "AFTER CONTACT" : directive.kind === "story" ? "STORY" : "GUIDE");
  setText("#directive-title", directive.title || "Review your next step");
  setText("#directive-summary", directive.summary || "");
  setText("#directive-why", directive.why || "");
  if (engaged && disclosure) disclosure.open = false;

  const progressIndex = Number(directive.progress_index || 0);
  const progressTotal = Number(directive.progress_total || 0);
  setText(
    "#directive-summary-progress",
    engaged ? "After contact" : progressTotal > 0 ? `${progressIndex}/${progressTotal}` : directive.kind === "tutorial" ? "Optional" : "Review",
  );
  if (directive.kind === "story" && progressTotal > 0) {
    progress.hidden = false;
    setText("#directive-progress-label", `Step ${progressIndex} of ${progressTotal}`);
    setMeter("#directive-progress .meter", progressIndex / progressTotal * 100, `Story step ${progressIndex} of ${progressTotal}`);
  }

  const suggested = String(directive.suggested_command || "").trim();
  action.hidden = engaged || !suggested;
  if (suggested && !engaged) {
    action.dataset.command = suggested;
    action.textContent = directive.kind === "tutorial" ? "Do this step" : directive.kind === "story" ? titleCase(suggested) : "Continue";
    action.setAttribute("aria-label", `${action.textContent}: ${suggested}`);
  } else {
    action.removeAttribute("data-command");
  }

  const canPause = Boolean(directive.can_pause || directive.pause_command);
  pause.hidden = engaged || !canPause;
  if (!engaged && canPause) pause.dataset.command = directive.pause_command || "guide pause";
  else pause.removeAttribute("data-command");

  const choiceList = Array.isArray(directive.choices) ? directive.choices : [];
  if (choiceList.length && !engaged) {
    choices.hidden = false;
    const summary = $("#directive-choice-summary");
    summary.hidden = false;
    summary.textContent = `${choiceList.length} available choice${choiceList.length === 1 ? "" : "s"}; none is selected automatically.`;
    for (const choice of choiceList) {
      const node = button(choice.label || titleCase(choice.command), choice.command, "directive-choice");
      if (choice.summary) node.title = choice.summary;
      choices.append(node);
    }
  } else {
    $("#directive-choice-summary").hidden = true;
  }
}

function contextCommand(name, state) {
  if (name === "target" && (state.room?.creatures || []).length === 1) {
    return `target ${quoteArg(state.room.creatures[0].name)}`;
  }
  if (name === "get" && (state.room?.items || []).length === 1) {
    return `get ${quoteArg(state.room.items[0].name)}`;
  }
  if (name === "withdraw" && (state.room?.exits || []).length === 1) {
    return `withdraw ${state.room.exits[0]}`;
  }
  return name;
}

function renderContextToolbar(state) {
  const fixed = new Set(["next", "look", "again", "help here"]);
  const exactActions = (state.context_actions || [])
    .map((item) => ({
      command: String(item.command || "").trim(),
      reason: String(item.reason || "").trim(),
    }))
    .filter((item) => item.command && !fixed.has(item.command.toLowerCase()));

  let selected = exactActions.slice(0, 3);
  if (!selected.length) {
    const available = new Set(
      (state.context_commands || [])
        .map((name) => String(name).toLowerCase())
        .filter((name) => !fixed.has(name)),
    );
    const priority = [
      "recover", "signal", "stand", "talk", "choose", "party", "report", "district", "service", "hospice", "appeal", "wayfinding", "interact", "quest",
      "target", "attack", "assess", "withdraw", "stabilize", "get", "search",
      "repair", "train", "retrain", "path", "queue", "cancel", "course",
      "journal", "exits", "route", "plan", "health", "inventory", "save", "build",
    ];
    selected = [
      ...priority.filter((name) => available.has(name)),
      ...[...available].filter((name) => !priority.includes(name)),
    ].slice(0, 3).map((name) => ({
      command: contextCommand(name, state),
      reason: app.commands.find((entry) => entry.name === name)?.summary || `Use ${name}`,
    }));
  }

  $$('[data-context-slot]').forEach((node, index) => {
    const action = selected[index];
    node.hidden = !action;
    if (!action) {
      node.removeAttribute("data-command");
      node.removeAttribute("title");
      node.disabled = true;
      return;
    }
    const command = action.command;
    const label = titleCase(command.split(/\s+/)[0]);
    node.dataset.command = command.toLowerCase();
    node.disabled = false;
    node.title = action.reason || `Use ${command}`;
    node.setAttribute("aria-label", `${command}. ${node.title}`);
    node.replaceChildren(
      element("span", "", "◇"),
      document.createTextNode(` ${label}`),
    );
  });
}

function derivedSetupStep(build) {
  if (!build.class) return 1;
  if (!["recommended", "manual"].includes(build.allocation_mode)
      || Number(build.remaining) !== 0) {
    return 2;
  }
  return 3;
}

function renderClassSetup(build) {
  const select = $("#class-select");
  clearNode(select);
  const prompt = element("option", "", "Select a class…");
  prompt.value = "";
  select.append(prompt);
  for (const definition of build.classes || []) {
    const label = definition.recommended_first_character
      ? `${definition.name} — recommended first character`
      : definition.name;
    const option = element("option", "", label);
    option.value = definition.id;
    select.append(option);
  }
  select.value = build.class_id || "";
  select.disabled = app.requestPending;

  const selected = build.class
    || (build.classes || []).find((definition) => definition.id === build.class_id)
    || null;
  setText(
    ".class-preview-tag",
    selected
      ? `${String(selected.difficulty || "unknown").toUpperCase()}${selected.id === "soldier" ? " · RECOMMENDED" : ""}`
      : "NO CLASS SELECTED",
  );
  setText("#class-preview-name", selected?.name || "Find your play style");
  setText(
    "#class-preview-summary",
    selected?.summary
      || "Choose from fifteen distinct Beta Earth classes. U.F. Soldier is marked as the beginner-friendly starting point.",
  );
  setText("#class-preview-role", selected?.role || "—");
  setText("#class-preview-difficulty", titleCase(selected?.difficulty || "—"));
  setText(
    "#class-preview-route",
    build.faction_route
      ? `${build.faction_route.route_label} · ${build.faction_route.hq_label}`
      : selected?.faction_name
        ? `${selected.faction_name} story route · membership unassigned`
        : "Membership unassigned",
  );
  setText(
    "#class-preview-tradeoff",
    selected?.tradeoff || "Choose a class to see its strengths and limits.",
  );
}

function renderStatSetup(build) {
  const recommended = $("#use-recommended-build");
  const manual = $("#use-manual-build");
  const recommendedActive = build.allocation_mode === "recommended";
  const manualActive = build.allocation_mode === "manual";
  recommended.classList.toggle("active", recommendedActive);
  recommended.setAttribute("aria-pressed", String(recommendedActive));
  manual.classList.toggle("active", manualActive);
  manual.setAttribute("aria-pressed", String(manualActive));
  recommended.disabled = !build.class || app.requestPending;
  manual.disabled = !build.class || app.requestPending;

  const spent = Number(build.spent || 0);
  const budget = Math.max(1, Number(build.budget || 1));
  const budgetMeter = $(".setup-budget-meter");
  const percentage = clamp(spent / budget * 100, 0, 100);
  $("span", budgetMeter).style.width = `${percentage}%`;
  budgetMeter.setAttribute("aria-valuemin", "0");
  budgetMeter.setAttribute("aria-valuemax", String(budget));
  budgetMeter.setAttribute("aria-valuenow", String(spent));
  budgetMeter.setAttribute(
    "aria-valuetext",
    `${spent} of ${budget} weighted points spent; ${Number(build.remaining || 0)} remaining`,
  );
  setText("#setup-budget", `${spent} / ${budget} spent · ${Number(build.remaining || 0)} left`);
  budgetMeter.classList.toggle("complete", Number(build.remaining) === 0);

  const list = $("#stat-allocation-list");
  clearNode(list);
  for (const attribute of build.attributes || []) {
    const row = element("article", "stat-allocation-row");
    const copy = element("div", "stat-allocation-copy");
    const heading = element("div", "stat-allocation-heading");
    heading.append(
      element("strong", "", attribute.name),
      element(
        "span",
        "",
        `${attribute.abbreviation} · ${attribute.weight} budget ${attribute.weight === 1 ? "point" : "points"} each`,
      ),
    );
    copy.append(
      heading,
      element("p", "", attribute.summary),
      element(
        "small",
        "",
        (attribute.effect_projection?.current || attribute.effects || []).join(" · "),
      ),
    );
    const stepper = element("div", "stat-stepper");
    const value = Number(attribute.base_value ?? attribute.minimum);
    const decrease = button("−", "", "stat-step-button");
    decrease.dataset.statAction = "decrease";
    decrease.dataset.attribute = attribute.id;
    decrease.dataset.value = String(value);
    decrease.setAttribute("aria-label", `Decrease ${attribute.name} from ${value}`);
    decrease.disabled = !manualActive || value <= Number(attribute.minimum) || app.requestPending;
    const output = element("output", "", value);
    output.setAttribute("aria-label", `${attribute.name} base value ${value}`);
    const increase = button("+", "", "stat-step-button");
    increase.dataset.statAction = "increase";
    increase.dataset.attribute = attribute.id;
    increase.dataset.value = String(value);
    increase.setAttribute("aria-label", `Increase ${attribute.name} from ${value}`);
    increase.disabled = !manualActive
      || value >= Number(attribute.maximum)
      || Number(build.remaining) < Number(attribute.weight)
      || app.requestPending;
    stepper.append(decrease, output, increase);
    row.append(copy, stepper);
    list.append(row);
  }
}

function renderGuidanceSetup(build) {
  const tutorialStatus = build.tutorial?.status || "offered";
  const guided = $("#choose-guided-start");
  const free = $("#choose-free-start");
  const guidedActive = ["active", "paused", "completed"].includes(tutorialStatus);
  const freeActive = tutorialStatus === "skipped";
  guided.classList.toggle("active", guidedActive);
  free.classList.toggle("active", freeActive);
  guided.setAttribute("aria-pressed", String(guidedActive));
  free.setAttribute("aria-pressed", String(freeActive));
  guided.disabled = app.requestPending;
  free.disabled = app.requestPending;

  setText("#review-class", build.class?.name || "Class not selected");
  setText(
    "#review-route",
    build.faction_route
      ? `${build.faction_route.route_label}; ${build.faction_route.name} membership remains unassigned.`
      : "Faction membership remains unassigned.",
  );
  const stats = $("#review-stats");
  clearNode(stats);
  for (const attribute of build.attributes || []) {
    stats.append(
      element(
        "span",
        "",
        `${attribute.abbreviation} ${attribute.base_value} base / ${attribute.effective_value} effective`,
      ),
    );
  }
  setText(
    "#review-guidance",
    guidedActive
      ? "Guided Start selected · optional, reward-free, and resumable."
      : freeActive
        ? "Explore Freely selected · no tutorial and no penalty."
        : "Choose a guidance preference.",
  );
}

function syncOnboardingStep(build) {
  const step = clamp(app.onboardingStep, 1, 3);
  app.onboardingStep = step;
  $$("[data-setup-step]").forEach((section) => {
    section.hidden = Number(section.dataset.setupStep) !== step;
  });
  $$("[data-step-dot]").forEach((dot) => {
    const dotStep = Number(dot.dataset.stepDot);
    dot.classList.toggle("active", dotStep === step);
    dot.classList.toggle("complete", dotStep < step);
    if (dotStep === step) dot.setAttribute("aria-current", "step");
    else dot.removeAttribute("aria-current");
  });
  $("#setup-back").hidden = step === 1;
  $("#setup-next").hidden = step === 3;
  $("#setup-confirm").hidden = step !== 3;
  $("#setup-next").disabled = app.requestPending
    || (step === 1 && !build.class)
    || (
      step === 2
      && (
        !["recommended", "manual"].includes(build.allocation_mode)
        || Number(build.remaining) !== 0
      )
    );
  $("#setup-confirm").disabled = app.requestPending || !build.can_confirm;
  setText(
    "#setup-save-status",
    app.requestPending ? "Saving this choice locally…" : "Every choice is saved locally.",
  );
  setText("#onboarding-message", app.onboardingMessage);
}

function renderOnboarding(state) {
  const build = state.character?.build || {};
  const required = build.status === "pending";
  const screen = $("#onboarding-screen");
  const hud = $("#hud-shell");
  if (!required) {
    fadeOutIntroMusic();
    screen.hidden = true;
    screen.inert = false;
    document.body.classList.remove("modal-open");
    hud.inert = app.requestPending;
    hud.removeAttribute("aria-hidden");
    app.onboardingVisible = false;
    return;
  }

  if (!app.onboardingVisible) {
    app.onboardingStep = derivedSetupStep(build);
  } else if (!build.class) {
    app.onboardingStep = 1;
  } else if (
    app.onboardingStep === 3
    && (
      !["recommended", "manual"].includes(build.allocation_mode)
      || Number(build.remaining) !== 0
    )
  ) {
    app.onboardingStep = 2;
  }
  app.onboardingVisible = true;
  showIntroMusicDock();
  screen.hidden = false;
  document.body.classList.add("modal-open");
  hud.inert = true;
  hud.setAttribute("aria-hidden", "true");
  renderClassSetup(build);
  renderStatSetup(build);
  renderGuidanceSetup(build);
  syncOnboardingStep(build);
}

function renderRecovery() {
  if (!app.state) return;
  const elapsed = (performance.now() - app.recoveryReceivedAt) / 1000;
  const remaining = Math.max(0, app.recoveryBase - elapsed);
  const recoveryClass = app.state.incapacitation
    ? "incapacitated"
    : remaining > 0
      ? "hard"
      : "ready";
  const progress = recoveryClass === "ready"
    ? 100
    : (1 - remaining / app.recoveryInitial) * 100;
  const track = $("#recovery-track");
  track.classList.toggle("busy", recoveryClass === "hard");
  track.classList.toggle("incapacitated", recoveryClass === "incapacitated");
  setText("#recovery-state", recoveryClass === "hard" ? "RECOVERY" : recoveryClass.toUpperCase());
  setText("#recovery-seconds", `${remaining.toFixed(1)}s`);
  setMeter(".recovery-meter", progress);
  const queue = app.state.queued_action;
  setText(
    "#queue-readout",
    queue
      ? `Queued: ${queue.command.toUpperCase()} ${queue.args.join(" ")} · ${queue.eligible_in_seconds}s`
      : "No action queued",
  );
  const focusRecovery = $("#focus-recovery");
  if (focusRecovery) {
    focusRecovery.textContent = recoveryClass === "incapacitated"
      ? "Down"
      : remaining > 0
        ? `${remaining.toFixed(1)}s`
        : "Ready";
    focusRecovery.closest(".focus-chip")?.classList.toggle("danger", recoveryClass === "incapacitated");
    focusRecovery.closest(".focus-chip")?.classList.toggle("busy", recoveryClass === "hard");
  }
}


const HUD_PRESETS = {
  guided: {
    modules: { character: true, navigation: true, combat: true, inventory: true, progress: true },
    rightTab: "tactical",
    lockedPreview: false,
    density: false,
  },
  tactical: {
    modules: { character: true, navigation: true, combat: true, inventory: true, progress: false },
    rightTab: "tactical",
    lockedPreview: false,
    density: true,
  },
  reading: {
    modules: { character: false, navigation: false, combat: false, inventory: false, progress: false },
    rightTab: "tactical",
    lockedPreview: false,
    density: false,
  },
  full: {
    modules: { character: true, navigation: true, combat: true, inventory: true, progress: true },
    rightTab: "journey",
    lockedPreview: true,
    density: false,
  },
};

const MODULE_SELECTORS = {
  character: ".character-panel",
  navigation: ".navigation-panel",
  combat: '[data-rail-tab="tactical"], [data-rail-panel="tactical"]',
  inventory: '[data-rail-tab="gear"], [data-rail-panel="gear"]',
  progress: '[data-rail-tab="journey"], [data-rail-panel="journey"]',
};

function moduleIsVisible(name) {
  return !document.body.classList.contains(`module-${name}-hidden`);
}

function updateRailAvailability() {
  const leftAvailable = moduleIsVisible("character") || moduleIsVisible("navigation");
  const rightAvailable = moduleIsVisible("combat") || moduleIsVisible("inventory") || moduleIsVisible("progress");
  document.body.classList.toggle("left-rail-empty", !leftAvailable);
  document.body.classList.toggle("right-rail-empty", !rightAvailable);
  if (!rightAvailable) closeRightRail();
  const availableTabs = ["tactical", "gear", "journey"].filter((tab) => {
    const module = tab === "tactical" ? "combat" : tab === "gear" ? "inventory" : "progress";
    return moduleIsVisible(module);
  });
  if (!availableTabs.includes(app.rightTab)) setRightTab(availableTabs[0] || "tactical", false);
}

function setModuleVisibility(name, visible, store = true, markCustom = true) {
  if (!(name in MODULE_SELECTORS)) return;
  document.body.classList.toggle(`module-${name}-hidden`, !visible);
  const control = $(`[data-module-toggle="${name}"]`);
  if (control) control.checked = visible;
  if (store) storePreference(`hud-module-${name}`, visible);
  if (markCustom) {
    app.hudPreset = "custom";
    document.body.dataset.hudPreset = "custom";
    setText("#hud-preset-label", "Custom");
    $$('[data-hud-preset]').forEach((node) => node.setAttribute("aria-checked", "false"));
    storePreference("hud-preset", "custom");
  }
  updateRailAvailability();
}

function setRightTab(tab, store = true) {
  const targetTab = ["tactical", "gear", "journey"].includes(tab) ? tab : "tactical";
  const module = targetTab === "tactical" ? "combat" : targetTab === "gear" ? "inventory" : "progress";
  if (!moduleIsVisible(module)) return;
  app.rightTab = targetTab;
  $$('[data-rail-tab]').forEach((node) => {
    const selected = node.dataset.railTab === targetTab;
    node.setAttribute("aria-selected", String(selected));
    node.classList.toggle("active", selected);
    node.tabIndex = selected ? 0 : -1;
  });
  $$('[data-rail-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.railPanel !== targetTab;
  });
  if (store) storePreference("hud-right-tab", targetTab);
}

function setTranscriptFilter(filter, store = true) {
  app.transcriptFilter = ["all", "world", "combat", "system"].includes(filter) ? filter : "all";
  applyTranscriptFilter();
  if (store) storePreference("hud-transcript-filter", app.transcriptFilter);
}

function setCommandEcho(active, store = true) {
  app.commandEcho = Boolean(active);
  const control = $("#command-echo-toggle");
  if (control) control.checked = app.commandEcho;
  if (store) storePreference("hud-command-echo", app.commandEcho);
}

function setLockedPreview(active, store = true) {
  const enabled = Boolean(active);
  document.body.classList.toggle("show-locked-previews", enabled);
  const control = $("#locked-preview-toggle");
  if (control) control.checked = enabled;
  if (store) storePreference("hud-locked-preview", enabled);
  if (app.state) {
    renderDirective(app.state);
    renderTechnique(app.state);
    renderSpecialization(app.state);
  }
}

function applyHudPreset(name, store = true) {
  const presetName = HUD_PRESETS[name] ? name : "guided";
  const preset = HUD_PRESETS[presetName];
  app.hudPreset = presetName;
  document.body.dataset.hudPreset = presetName;
  setText("#hud-preset-label", titleCase(presetName === "full" ? "explorer" : presetName));
  for (const [module, visible] of Object.entries(preset.modules)) {
    setModuleVisibility(module, visible, store, false);
  }
  setRightTab(preset.rightTab, store);
  setLockedPreview(preset.lockedPreview, store);
  setPreference("density", preset.density);
  $$('[data-hud-preset]').forEach((node) => {
    const selected = node.dataset.hudPreset === presetName;
    node.setAttribute("aria-checked", String(selected));
    node.classList.toggle("active", selected);
  });
  if (store) storePreference("hud-preset", presetName);
}

function setCombatProjection(mode, store = true) {
  app.combatProjection = ["narrative", "tactical", "audit"].includes(mode) ? mode : "tactical";
  document.body.dataset.combatProjection = app.combatProjection;
  $$('[data-combat-projection]').forEach((node) => {
    const selected = node.dataset.combatProjection === app.combatProjection;
    node.classList.toggle("active", selected);
    node.setAttribute("aria-pressed", String(selected));
  });
  if (store) storePreference("hud-combat-projection", app.combatProjection);
  if (app.state) {
    renderCombat(app.state);
    renderBattlefield(app.state);
    renderCombatPartner(app.state);
  }
}

function setCommandTrayExpanded(expanded, store = true) {
  app.commandTrayExpanded = Boolean(expanded);
  document.body.classList.toggle("command-tray-expanded", app.commandTrayExpanded);
  const toggle = $("#command-tray-toggle");
  toggle?.setAttribute("aria-expanded", String(app.commandTrayExpanded));
  if (toggle) toggle.textContent = app.commandTrayExpanded ? "Fewer actions" : "More actions";
  if (store) storePreference("hud-command-tray-expanded", String(app.commandTrayExpanded));
}

function initializeHudPreferences() {
  const preset = readStoredPreference("hud-preset", "guided");
  applyHudPreset(HUD_PRESETS[preset] ? preset : "guided", false);
  for (const module of Object.keys(MODULE_SELECTORS)) {
    const stored = readStoredPreference(`hud-module-${module}`, null);
    if (stored !== null) setModuleVisibility(module, stored === "true", false, false);
  }
  setRightTab(readStoredPreference("hud-right-tab", app.rightTab), false);
  setTranscriptFilter(readStoredPreference("hud-transcript-filter", "all"), false);
  setCommandEcho(readStoredPreference("hud-command-echo", "true") === "true", false);
  setLockedPreview(readStoredPreference("hud-locked-preview", "false") === "true", false);
  setCombatProjection(readStoredPreference("hud-combat-projection", "tactical"), false);
  setCommandTrayExpanded(readStoredPreference("hud-command-tray-expanded", "false") === "true", false);
  if (preset === "custom") {
    app.hudPreset = "custom";
    document.body.dataset.hudPreset = "custom";
    setText("#hud-preset-label", "Custom");
    $$('[data-hud-preset]').forEach((node) => node.setAttribute("aria-checked", "false"));
  }
  updateRailAvailability();
}

function renderHudFocus(state) {
  const directive = state.directive || null;
  const creatures = state.room?.creatures || [];
  const actors = battlefieldActors(state);
  const threat = actors
    .filter((actor) => actor.kind === "creature")
    .sort((left, right) => Number(left.ready_in_field_seconds || 0) - Number(right.ready_in_field_seconds || 0))[0] || null;
  const foundation = state.foundation || {};
  const pending = factionById(foundation, foundation.pending_allegiance_id);
  const action = $("#focus-action");
  const secondary = $("#focus-secondary-action");
  const coaching = $("#focus-coaching");
  const coachingAction = $("#focus-coaching-action");
  const afterContact = $("#focus-after-contact");
  if (!action || !secondary) return;
  secondary.hidden = true;
  coaching.hidden = true;
  afterContact.hidden = true;
  secondary.removeAttribute("data-command");
  coachingAction?.removeAttribute("data-command");

  if (pending) {
    setText("#focus-context-label", "COMMITMENT DECISION");
    setText("#focus-objective", `${pending.name} pledge · ${pending.pledge_entry_rank_title || pending.rank_title || "entry rank"}`);
    setText("#focus-objective-why", "Action? [Y/N] · Allegiance changes only after explicit confirmation; guild, citizenship, territory, and Commander authority remain separate.");
    action.dataset.command = "faction y";
    action.textContent = "Yes — confirm";
    action.title = `Confirm allegiance to ${pending.name}`;
    secondary.hidden = false;
    secondary.dataset.command = "faction n";
    secondary.textContent = "No — cancel";
  } else if (creatures.length) {
    const target = creatures.find((creature) => creature.instance_id === state.target_id) || null;
    setText("#focus-context-label", `COMBAT NOW · ${creatures.length} HOSTILE${creatures.length === 1 ? "" : "S"}`);
    setText("#focus-objective", threat ? `${threat.name} prepares ${intentLabel(threat.intent)} · ${actorTiming(threat)}` : "Immediate hostile pressure");
    setText("#focus-objective-why", `${threat?.target_name ? `Target: ${threat.target_name}. ` : ""}Survival and battlefield choices outrank the longer objective.`);
    if (directive?.title) {
      afterContact.hidden = false;
      afterContact.textContent = `After contact: ${directive.title}`;
    }
    action.dataset.command = target ? "attack" : creatures.length === 1 ? `target ${quoteArg(creatures[0].name)}` : "assess";
    action.textContent = target ? "Attack" : creatures.length === 1 ? "Target threat" : "Assess field";
    action.title = target ? `Attack ${target.name}` : "Establish a target or assess the field";
    const primaryCommand = action.dataset.command || "";
    secondary.hidden = primaryCommand === "assess";
    if (!secondary.hidden) {
      secondary.dataset.command = "assess";
      secondary.textContent = "Assess";
    }
  } else {
    const objective = directive?.title || state.story?.active?.title || "Choose your next meaningful action";
    const why = directive?.why || directive?.summary || "Use NEXT for one exact step or the command palette to choose another valid action.";
    const suggested = String(directive?.suggested_command || "next").trim() || "next";
    setText("#focus-context-label", "NEXT MEANINGFUL STEP");
    setText("#focus-objective", objective);
    setText("#focus-objective-why", why);
    action.dataset.command = suggested;
    action.textContent = titleCase(suggested);
    action.title = directive?.summary || `Use ${suggested}`;
    const guide = state.guidance?.step || directive?.guide || null;
    const guideCommand = String(guide?.suggested_command || "").trim();
    if (guide && guideCommand && guideCommand.toLowerCase() !== suggested.toLowerCase()) {
      coaching.hidden = false;
      setText("#focus-coaching-title", guide.description || "Optional Guided Start step");
      setText("#focus-coaching-why", guide.why || "Optional, reward-free guidance.");
      if (coachingAction) {
        coachingAction.dataset.command = guideCommand;
        coachingAction.textContent = titleCase(guideCommand);
        coachingAction.title = guide.why || "Optional, reward-free guidance.";
      }
    }
  }

  const character = state.character || {};
  setText("#focus-health", `${Number(character.health || 0)} / ${Number(character.max_health || 0)}`);
  const healthChip = $("#focus-health")?.closest(".focus-chip");
  const healthPercent = Number(character.health || 0) / Math.max(1, Number(character.max_health || 1));
  healthChip?.classList.toggle("danger", healthPercent <= 0.30);
  healthChip?.classList.toggle("warn", healthPercent > 0.30 && healthPercent <= 0.60);

  const injury = state.difficulty_curve?.injury || state.beginner_experience?.difficulty?.injury || null;
  const wounds = character.wounds || [];
  const injuryLabel = injury?.active
    ? `${titleCase(injury.label || "Injury")} S${Number(injury.severity || 0)}`
    : wounds.length ? `${wounds.length} ${wounds.length === 1 ? "wound" : "wounds"}` : injury?.rehabilitated ? "Rehabilitated" : "Stable";
  setText("#focus-injury", injuryLabel);
  const injuryChip = $("#focus-injury")?.closest(".focus-chip");
  injuryChip?.classList.toggle("danger", Boolean(injury?.active && Number(injury.severity || 0) >= 4));
  injuryChip?.classList.toggle("warn", Boolean((injury?.active && Number(injury.severity || 0) < 4) || wounds.length));

  const companion = state.economy?.companion || null;
  const solActor = companionBattlefieldActor(state);
  setText("#focus-sol", companion ? `${titleCase(companion.order || "balanced")} · ${solActor ? actorTiming(solActor) : `${companion.health}/${companion.max_health}`}` : "Unavailable");
  $("#focus-sol-button")?.classList.toggle("warn", Boolean(companion && Number(companion.health || 0) / Math.max(1, Number(companion.max_health || 1)) <= 0.45));

  const target = creatures.find((creature) => creature.instance_id === state.target_id) || null;
  setText("#focus-target", target ? `${target.name} · ${target.health}/${target.max_health}` : creatures.length ? `${creatures.length} hostile${creatures.length === 1 ? "" : "s"}` : "Area clear");
  $("#focus-target-button")?.classList.toggle("danger", creatures.length > 0);

  setText("#tactical-tab-badge", creatures.length ? `${creatures.length} THREAT${creatures.length === 1 ? "" : "S"}` : "READY");
  setText("#gear-tab-badge", String((character.inventory || []).length));
  const allegiance = factionById(foundation, foundation.allegiance_id);
  setText("#journey-tab-badge", allegiance ? allegiance.rank_title || `L${Number(character.level || 1)}` : `L${Number(character.level || 1)}`);
}

function syncHudScrim() {
  const active = document.body.classList.contains("hud-settings-open")
    || document.body.classList.contains("command-palette-open")
    || document.body.classList.contains("left-drawer-open")
    || document.body.classList.contains("right-drawer-open");
  const scrim = $("#hud-scrim");
  if (scrim) scrim.hidden = !active;
}

function openHudSettings() {
  closeCommandPalette();
  const sheet = $("#hud-settings-sheet");
  sheet.hidden = false;
  document.body.classList.add("hud-settings-open");
  $("#hud-settings-toggle")?.setAttribute("aria-expanded", "true");
  syncHudScrim();
  window.requestAnimationFrame(() => $('[data-hud-preset][aria-checked="true"]')?.focus());
}

function closeHudSettings() {
  const sheet = $("#hud-settings-sheet");
  if (!sheet || sheet.hidden) return;
  sheet.hidden = true;
  document.body.classList.remove("hud-settings-open");
  $("#hud-settings-toggle")?.setAttribute("aria-expanded", "false");
  syncHudScrim();
}

function openLeftRail() {
  document.body.classList.add("left-drawer-open");
  $("#left-rail-toggle")?.setAttribute("aria-expanded", "true");
  syncHudScrim();
}

function closeLeftRail() {
  document.body.classList.remove("left-drawer-open");
  $("#left-rail-toggle")?.setAttribute("aria-expanded", "false");
  syncHudScrim();
}

function openRightRail(tab = app.rightTab) {
  setRightTab(tab);
  document.body.classList.add("right-drawer-open");
  $("#right-rail-toggle")?.setAttribute("aria-expanded", "true");
  syncHudScrim();
}

function closeRightRail() {
  document.body.classList.remove("right-drawer-open");
  $("#right-rail-toggle")?.setAttribute("aria-expanded", "false");
  syncHudScrim();
}

function closeCommandPalette(returnFocus = false) {
  const palette = $("#command-palette");
  if (!palette || palette.hidden) return;
  palette.hidden = true;
  document.body.classList.remove("command-palette-open");
  $("#command-palette-toggle")?.setAttribute("aria-expanded", "false");
  syncHudScrim();
  if (returnFocus) $("#command-palette-toggle")?.focus();
}

function closeHudOverlays() {
  const hadPalette = document.body.classList.contains("command-palette-open");
  closeCommandPalette(false);
  closeHudSettings();
  closeLeftRail();
  closeRightRail();
  if (hadPalette) $("#command-palette-toggle")?.focus();
}

function paletteCandidate(command, label, detail, group = "COMMAND") {
  const normalized = String(command || "").trim();
  if (!normalized) return null;
  return {
    command: normalized,
    label: label || titleCase(normalized),
    detail: detail || `Use ${normalized}`,
    group,
    search: `${normalized} ${label || ""} ${detail || ""} ${group}`.toLowerCase(),
  };
}

function collectPaletteItems() {
  const items = [];
  const seen = new Set();
  const add = (command, label, detail, group) => {
    const candidate = paletteCandidate(command, label, detail, group);
    const key = candidate?.command.toLowerCase();
    if (!candidate || seen.has(key)) return;
    seen.add(key);
    items.push(candidate);
  };
  const state = app.state || {};
  const directive = state.directive || {};
  add(directive.suggested_command, directive.title || "Current objective", directive.summary || directive.why, "OBJECTIVE");
  for (const action of directive.actions || []) {
    if (action.available) add(action.command, action.label, action.summary, "STORY CHOICE");
  }
  for (const action of state.context_actions || []) add(action.command, titleCase(action.command), action.reason, "HERE");
  const engaged = Boolean((state.room?.creatures || []).length);
  for (const direction of state.room?.exits || []) {
    add(`${engaged ? "withdraw" : "go"} ${direction}`, `${engaged ? "Withdraw" : "Go"} ${titleCase(direction)}`, "Use a currently available exit.", "MOVEMENT");
  }
  for (const npc of state.room?.npcs || []) add(`talk ${quoteArg(npc.name)}`, `Talk to ${npc.name}`, npc.description, "PEOPLE");
  for (const item of state.room?.items || []) add(`get ${quoteArg(item.name)}`, `Get ${item.name}`, item.description, "OBJECTS");
  for (const noun of state.room?.inspectables || []) add(`examine ${quoteArg(noun)}`, `Examine ${titleCase(noun)}`, "Inspect a visible environmental detail.", "OBJECTS");
  const companion = state.economy?.companion;
  for (const order of companion?.order_commands || []) add(order.command, `Sol: ${titleCase(order.id)}`, companion.agency_rule, "SOL");
  if (companion?.sync_unlocked) {
    add(
      companion.sync_available ? companion.sync_command : "companion sync status",
      companion.sync_available ? "Synchronize with Sol" : "Partner synchrony status",
      companion.sync_available ? companion.sync_summary : companion.sync_reason,
      "SOL",
    );
  }
  [
    ["next", "Next objective step", "Shows one exact step without executing it."],
    ["help here", "Context help", "Lists exact commands available in this location."],
    ["look", "Read the full room", "Restores the complete authored room description."],
    ["health", "Health and wounds", "Review integrity, wounds, bleeding, and recovery."],
    ["injury", "Injury and recovery", "Review the level 5–8 injury and recovery plan."],
    ["inventory", "Inventory", "Review carried items."],
    ["equipment", "Equipment", "Review equipped gear and tradeoffs."],
    ["journal", "Journal", "Review discoveries and progress."],
    ["party", "Party status", "Review the current companion and future party boundary."],
    ["save", "Save now", "Write current progress to the local save."],
  ].forEach(([command, label, detail]) => add(command, label, detail, "ESSENTIAL"));
  for (const spec of app.commands || []) {
    add(spec.name, titleCase(spec.name), spec.summary || `Use ${spec.name}`, "ALL COMMANDS");
  }
  return items;
}

function renderCommandPalette(query = "") {
  const search = String(query || "").trim().toLowerCase();
  const results = $("#command-palette-results");
  clearNode(results);
  app.paletteItems = collectPaletteItems().filter((item) => !search || item.search.includes(search)).slice(0, 36);
  app.paletteIndex = clamp(app.paletteIndex, 0, Math.max(0, app.paletteItems.length - 1));
  if (!app.paletteItems.length) {
    results.append(emptyNote("No matching command. Try a verb such as route, talk, inventory, Sol, or help."));
    return;
  }
  app.paletteItems.forEach((item, index) => {
    const node = element("button", "palette-result");
    node.type = "button";
    node.dataset.paletteCommand = item.command;
    node.dataset.paletteIndex = String(index);
    node.id = `palette-option-${index}`;
    node.setAttribute("role", "option");
    node.setAttribute("aria-selected", String(index === app.paletteIndex));
    if (index === app.paletteIndex) node.classList.add("selected");
    const copy = element("span");
    copy.append(element("small", "palette-group", item.group), element("strong", "", item.label), element("small", "", item.detail));
    node.append(copy, element("kbd", "", item.command));
    results.append(node);
  });
  $("#command-palette-input")?.setAttribute("aria-activedescendant", `palette-option-${app.paletteIndex}`);
}

function rebuildCommandPalette() {
  if (!$("#command-palette")?.hidden) renderCommandPalette($("#command-palette-input")?.value || "");
}

function selectPaletteIndex(index) {
  if (!app.paletteItems.length) return;
  app.paletteIndex = (index + app.paletteItems.length) % app.paletteItems.length;
  $$("#command-palette-results .palette-result").forEach((node, nodeIndex) => {
    const selected = nodeIndex === app.paletteIndex;
    node.classList.toggle("selected", selected);
    node.setAttribute("aria-selected", String(selected));
    if (selected) node.scrollIntoView({ block: "nearest" });
  });
  $("#command-palette-input")?.setAttribute("aria-activedescendant", `palette-option-${app.paletteIndex}`);
}

function executePaletteSelection() {
  const item = app.paletteItems[app.paletteIndex];
  if (!item) return;
  closeCommandPalette(false);
  issueCommand(item.command);
}

function openCommandPalette() {
  closeHudSettings();
  closeLeftRail();
  closeRightRail();
  const palette = $("#command-palette");
  palette.hidden = false;
  document.body.classList.add("command-palette-open");
  $("#command-palette-toggle")?.setAttribute("aria-expanded", "true");
  const input = $("#command-palette-input");
  input.value = "";
  app.paletteIndex = 0;
  renderCommandPalette();
  syncHudScrim();
  window.requestAnimationFrame(() => input.focus());
}

function resetHudPreferences() {
  ["density", "contrast", "motion"].forEach((name) => setPreference(name, false));
  setTextScale(100);
  setCommandEcho(true);
  setTranscriptFilter("all");
  applyHudPreset("guided");
  toast("HUD restored to the guided, objective-first layout.");
}

function readStoredPreference(name, fallback = null) {
  try {
    return localStorage.getItem(`beta-earth-${name}`) ?? fallback;
  } catch {
    return fallback;
  }
}

function loadPreference(name) {
  return readStoredPreference(name, "false") === "true";
}

function storePreference(name, value) {
  try {
    localStorage.setItem(`beta-earth-${name}`, String(value));
  } catch {
    // Preferences remain optional when storage is unavailable.
  }
}

function setPreference(name, active) {
  const className = name === "density" ? "compact" : name === "contrast" ? "high-contrast" : "reduced-motion";
  document.body.classList.toggle(className, active);
  const control = $(`#${name}-toggle`);
  control?.setAttribute("aria-pressed", String(active));
  const label = control?.querySelector("small");
  if (label) {
    label.textContent = name === "density"
      ? active ? "Compact" : "Comfortable"
      : name === "contrast"
        ? active ? "High" : "Standard"
        : active ? "Reduced" : "Full";
  }
  storePreference(name, active);
}

function setTextScale(value) {
  const allowed = [100, 125, 150];
  const scale = allowed.includes(Number(value)) ? Number(value) : 100;
  document.documentElement.dataset.textScale = String(scale);
  const button = $("#text-scale-toggle");
  button?.setAttribute("aria-label", `Text scale: ${scale} percent`);
  if (button) button.title = `Text scale: ${scale}%`;
  setText("#text-scale-label", `${scale}%`);
  storePreference("text-scale", scale);
}


$("#intro-music-toggle").addEventListener("click", () => {
  const audio = introAudioElement();
  if (!audio) return;
  if (audio.paused) playIntroMusic({ store: true, userInitiated: true });
  else pauseIntroMusic({ store: true });
});

$("#intro-music-mute").addEventListener("click", () => {
  const audio = introAudioElement();
  if (!audio) return;
  setIntroMusicMuted(!audio.muted, true);
  if (audio.paused && app.introMusicWanted) playIntroMusic({ store: false, userInitiated: true });
});

$("#intro-music-volume").addEventListener("input", (event) => {
  setIntroMusicVolume(event.currentTarget.value, true);
});

for (const selector of ["#intro-sfx-volume", "#settings-sfx-volume"]) {
  $(selector)?.addEventListener("input", (event) => setSfxVolume(event.currentTarget.value, true));
}
for (const selector of ["#intro-sfx-mute", "#settings-sfx-mute"]) {
  $(selector)?.addEventListener("click", () => {
    setSfxMuted(!app.sfxMuted, true);
    if (!app.sfxMuted) playSfx("select", { category: "interaction", gain: 0.76, bypassThrottle: true });
  });
}
for (const selector of ["#intro-sfx-preview", "#settings-sfx-preview"]) {
  $(selector)?.addEventListener("click", previewSfx);
}
$("#sfx-interaction-toggle")?.addEventListener("change", (event) => {
  setSfxCategory("interaction", event.currentTarget.checked, true);
  if (event.currentTarget.checked) playSfx("tick", { category: "interaction", gain: 0.72, bypassThrottle: true });
});
$("#sfx-feedback-toggle")?.addEventListener("change", (event) => {
  setSfxCategory("feedback", event.currentTarget.checked, true);
  if (event.currentTarget.checked) playSfx("confirm", { category: "feedback", gain: 0.65, bypassThrottle: true });
});

$("#session-form").addEventListener("submit", (event) => {
  event.preventDefault();
  openSession($("#player-name").value);
});

$("#class-select").addEventListener("change", (event) => {
  const classId = String(event.currentTarget.value || "").trim();
  if (classId) issueCommand(`build class ${classId}`);
});

$("#use-recommended-build").addEventListener("click", () => {
  issueCommand("build auto");
});

$("#use-manual-build").addEventListener("click", () => {
  issueCommand("build reset");
});

$("#stat-allocation-list").addEventListener("click", (event) => {
  const control = event.target.closest("[data-stat-action]");
  if (!control || control.disabled || app.requestPending) return;
  const current = Number(control.dataset.value);
  const next = control.dataset.statAction === "increase" ? current + 1 : current - 1;
  issueCommand(`build set ${control.dataset.attribute} ${next}`);
});

$("#choose-guided-start").addEventListener("click", () => {
  issueCommand("build tutorial guided");
});

$("#choose-free-start").addEventListener("click", () => {
  issueCommand("build tutorial skip");
});

$("#setup-back").addEventListener("click", () => {
  app.onboardingStep = Math.max(1, app.onboardingStep - 1);
  renderOnboarding(app.state);
  focusSetupStep();
});

$("#setup-next").addEventListener("click", () => {
  app.onboardingStep = Math.min(3, app.onboardingStep + 1);
  renderOnboarding(app.state);
  focusSetupStep();
});

$("#setup-confirm").addEventListener("click", () => {
  issueCommand("build confirm");
});

$("#command-form").addEventListener("submit", (event) => {
  event.preventDefault();
  playSfx("select", { category: "interaction", gain: 0.48 });
  issueCommand($("#command-input").value);
});

$("#route-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const destination = $("#route-input").value.trim();
  if (destination) playSfx("select", { category: "interaction", gain: 0.48 });
  if (destination) {
    issueCommand(`route ${quoteArg(destination)}`);
    $("#route-input").value = "";
  }
});

document.addEventListener("click", (event) => {
  const commandNode = event.target.closest("[data-command]");
  if (commandNode && !commandNode.disabled) issueCommand(commandNode.dataset.command);
  const directionNode = event.target.closest("[data-direction]");
  if (directionNode && !directionNode.disabled) {
    const engaged = (app.state?.room?.creatures || []).length > 0;
    issueCommand(`${engaged ? "withdraw" : "go"} ${directionNode.dataset.direction}`);
  }
});

document.addEventListener("click", (event) => {
  const control = event.target.closest("button, summary, [role=tab]");
  if (!control || control.disabled || control.hasAttribute("data-sfx-silent")) return;
  playSfx("tick", { category: "interaction", gain: 0.58 });
});

document.addEventListener("change", (event) => {
  const control = event.target.closest("select, input[type=checkbox], input[type=radio], input[type=range]");
  if (!control || control.hasAttribute("data-sfx-silent")) return;
  playSfx("select", { category: "interaction", gain: 0.56 });
});

$("#withdraw-button").addEventListener("click", () => {
  const exits = app.state?.room?.exits || [];
  if (exits.length === 1) issueCommand(`withdraw ${exits[0]}`);
  else {
    $("#command-input").value = "withdraw ";
    $("#command-input").focus();
  }
});

$("#command-tray-toggle")?.addEventListener("click", () => setCommandTrayExpanded(!app.commandTrayExpanded));
$$('[data-combat-projection]').forEach((node) => {
  node.addEventListener("click", () => setCombatProjection(node.dataset.combatProjection));
});

$("#stance-select").addEventListener("change", (event) => {
  if (event.target.value !== app.state?.character?.stance) issueCommand(`stance ${event.target.value}`);
});

$("#defense-select").addEventListener("change", (event) => {
  if (event.target.value !== app.state?.character?.defense_mode) issueCommand(`defense ${event.target.value}`);
});

$("#clear-transcript").addEventListener("click", () => {
  clearNode($("#transcript"));
  app.transcriptCount = 0;
  appendTranscript("Transcript cleared. Your character and progress are unchanged.", "system");
});

$("#autoscroll-toggle").addEventListener("click", (event) => {
  app.autoscroll = !app.autoscroll;
  event.currentTarget.classList.toggle("active", app.autoscroll);
  event.currentTarget.setAttribute("aria-pressed", String(app.autoscroll));
  if (app.autoscroll) $("#transcript").scrollTop = $("#transcript").scrollHeight;
});

$("#density-toggle").addEventListener("click", (event) => {
  setPreference("density", event.currentTarget.getAttribute("aria-pressed") !== "true");
});

$("#contrast-toggle").addEventListener("click", (event) => {
  setPreference("contrast", event.currentTarget.getAttribute("aria-pressed") !== "true");
});

$("#text-scale-toggle").addEventListener("click", () => {
  const current = Number(document.documentElement.dataset.textScale || 100);
  setTextScale(current === 100 ? 125 : current === 125 ? 150 : 100);
});

$("#motion-toggle").addEventListener("click", (event) => {
  setPreference("motion", event.currentTarget.getAttribute("aria-pressed") !== "true");
});


$("#hud-settings-toggle").addEventListener("click", () => {
  if (document.body.classList.contains("hud-settings-open")) closeHudSettings();
  else openHudSettings();
});
$("#hud-settings-close").addEventListener("click", closeHudSettings);
$("#command-palette-toggle").addEventListener("click", () => {
  if (document.body.classList.contains("command-palette-open")) closeCommandPalette(true);
  else openCommandPalette();
});
$("#command-palette-close").addEventListener("click", () => closeCommandPalette(true));
$("#left-rail-toggle").addEventListener("click", openLeftRail);
$("#left-rail-close").addEventListener("click", closeLeftRail);
$("#right-rail-toggle").addEventListener("click", () => openRightRail(app.rightTab));
$("#right-rail-close").addEventListener("click", closeRightRail);
$("#hud-scrim").addEventListener("click", closeHudOverlays);
$("#hud-reset").addEventListener("click", resetHudPreferences);

$$('[data-hud-preset]').forEach((node) => {
  node.addEventListener("click", () => applyHudPreset(node.dataset.hudPreset));
});
$$('[data-module-toggle]').forEach((node) => {
  node.addEventListener("change", () => setModuleVisibility(node.dataset.moduleToggle, node.checked));
});
$$('[data-rail-tab]').forEach((node) => {
  node.addEventListener("click", () => setRightTab(node.dataset.railTab));
  node.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const visible = $$('[data-rail-tab]').filter((tab) => tab.getClientRects().length > 0);
    const current = visible.indexOf(node);
    const delta = event.key === "ArrowRight" ? 1 : -1;
    const next = visible[(current + delta + visible.length) % visible.length];
    if (next) {
      setRightTab(next.dataset.railTab);
      next.focus();
    }
  });
});
$$('[data-transcript-filter]').forEach((node) => {
  node.addEventListener("click", () => setTranscriptFilter(node.dataset.transcriptFilter));
});
$("#command-echo-toggle").addEventListener("change", (event) => setCommandEcho(event.currentTarget.checked));
$("#locked-preview-toggle").addEventListener("change", (event) => setLockedPreview(event.currentTarget.checked));

$("#command-palette-input").addEventListener("input", (event) => {
  app.paletteIndex = 0;
  renderCommandPalette(event.currentTarget.value);
});
$("#command-palette-input").addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    selectPaletteIndex(app.paletteIndex + 1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    selectPaletteIndex(app.paletteIndex - 1);
  } else if (event.key === "Enter") {
    event.preventDefault();
    executePaletteSelection();
  } else if (event.key === "Escape") {
    event.preventDefault();
    closeCommandPalette(true);
  }
});
$("#command-palette-results").addEventListener("mousemove", (event) => {
  const option = event.target.closest("[data-palette-index]");
  if (option) selectPaletteIndex(Number(option.dataset.paletteIndex));
});
$("#command-palette-results").addEventListener("click", (event) => {
  const option = event.target.closest("[data-palette-command]");
  if (!option) return;
  closeCommandPalette(false);
  issueCommand(option.dataset.paletteCommand);
});

$("#focus-sol-button").addEventListener("click", () => openRightRail("tactical"));
$("#focus-target-button").addEventListener("click", () => openRightRail("tactical"));

$("#support-export-button").addEventListener("click", refreshSupportExport);

$("#quit-button").addEventListener("click", () => {
  if (window.confirm("Save and close this local Beta Earth session?")) issueCommand("quit");
});

$("#command-input").addEventListener("keydown", (event) => {
  if (event.key === "ArrowUp") {
    event.preventDefault();
    if (app.historyIndex > 0) app.historyIndex -= 1;
    event.currentTarget.value = app.history[app.historyIndex] || "";
  } else if (event.key === "ArrowDown") {
    event.preventDefault();
    if (app.historyIndex < app.history.length) app.historyIndex += 1;
    event.currentTarget.value = app.history[app.historyIndex] || "";
  } else if (event.key === "Escape") {
    event.currentTarget.value = "";
  }
});

document.addEventListener("keydown", (event) => {
  if (app.onboardingVisible) {
    if (event.key === "Escape") {
      event.preventDefault();
      app.onboardingMessage = "Character Foundation is required before entering the Sprawl. Your current choices remain saved.";
      renderOnboarding(app.state);
      focusSetupStep();
      return;
    }
    if (event.key === "Tab") {
      const focusable = $$(
        'button:not([disabled]):not([hidden]), select:not([disabled]):not([hidden]), input:not([disabled]):not([hidden]), [tabindex]:not([tabindex="-1"]):not([hidden])',
        $("#onboarding-screen"),
      ).filter((node) => node.getClientRects().length > 0);
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (!focusable.includes(document.activeElement)) {
        event.preventDefault();
        first.focus();
      }
    }
    return;
  }
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (document.body.classList.contains("command-palette-open")) closeCommandPalette(true);
    else openCommandPalette();
    return;
  }
  if (event.key === "Escape" && (
    document.body.classList.contains("command-palette-open")
    || document.body.classList.contains("hud-settings-open")
    || document.body.classList.contains("left-drawer-open")
    || document.body.classList.contains("right-drawer-open")
  )) {
    event.preventDefault();
    closeHudOverlays();
    return;
  }
  if (!typing && event.key === "[") {
    event.preventDefault();
    openLeftRail();
    return;
  }
  if (!typing && event.key === "]") {
    event.preventDefault();
    openRightRail(app.rightTab);
    return;
  }
  if (!typing && event.altKey && ["1", "2", "3"].includes(event.key)) {
    event.preventDefault();
    const tab = { "1": "tactical", "2": "gear", "3": "journey" }[event.key];
    setRightTab(tab);
    openRightRail(tab);
    return;
  }
  if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    $("#command-input").focus();
  }
});

window.addEventListener("resize", () => {
  if (window.innerWidth > 860 && app.commandTrayExpanded) setCommandTrayExpanded(false);
});
window.addEventListener("pagehide", stopSfx);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopSfx();
});

setPreference("density", loadPreference("density"));
setPreference("contrast", loadPreference("contrast"));
setPreference("motion", loadPreference("motion") || window.matchMedia("(prefers-reduced-motion: reduce)").matches);
setTextScale(Number(readStoredPreference("text-scale", "100")));
initializeSfx();
initializeIntroMusic();
initializeHudPreferences();
window.setInterval(renderRecovery, 100);
resumeOpenSession();
