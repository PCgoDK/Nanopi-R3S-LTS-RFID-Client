"use strict";

const $ = (id) => document.getElementById(id);
const tokenDialog = $("tokenDialog");
const tokenForm = $("tokenForm");
let authRequired = false;
let selectedEpc = "";
let refreshTimer = null;
let continueTimer = null;
let cwTimer = null;
let toastTimer = null;
let connectionKey = null;
let connectionGeneration = 0;
let inventoryRunning = false;
let versionsRequested = false;
let versionsPending = false;
const chartValues = { jammerChart: null, rssiChart: null };
const regionChannels = {
  "01": { startKhz: 920125, stepKhz: 250, count: 20 },
  "04": { startKhz: 840125, stepKhz: 250, count: 20 },
  "02": { startKhz: 902250, stepKhz: 500, count: 52 },
  "03": { startKhz: 865100, stepKhz: 200, count: 15 },
  "06": { startKhz: 917100, stepKhz: 200, count: 19 },
};

function showToast(message, isError = false) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 3800);
}

function showTokenDialog() {
  if (!tokenDialog.open) tokenDialog.showModal();
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Content-Type", "application/json");
  const token = sessionStorage.getItem("rfid-app-token");
  if (token) headers.set("X-API-Token", token);
  const response = await fetch(path, { ...options, headers });
  let data;
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (response.status === 401) {
    showTokenDialog();
    throw new Error(data.detail || "App-token mangler eller er forkert.");
  }
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

function bytesFromHex(value, name = "Hex-værdi") {
  const compact = value.replace(/[\s:-]/g, "");
  if (!/^[0-9a-f]*$/i.test(compact) || compact.length % 2 !== 0) {
    throw new Error(`${name} skal være gyldig hex med hele bytes.`);
  }
  return Uint8Array.from(compact.match(/.{2}/g) || [], (byte) => parseInt(byte, 16));
}

function frameHex(command, payload = []) {
  const data = [0xBB, 0x00, command, (payload.length >> 8) & 0xFF, payload.length & 0xFF, ...payload];
  data.push(data.slice(1).reduce((sum, byte) => sum + byte, 0) & 0xFF, 0x7E);
  return data.map((byte) => byte.toString(16).padStart(2, "0")).join(" ").toUpperCase();
}

function hexByte(value, name) {
  const bytes = bytesFromHex(value, name);
  if (bytes.length !== 1) throw new Error(`${name} skal være præcis én byte.`);
  return bytes[0];
}

function updateChannels() {
  const channel = $("rfChannel");
  const band = regionChannels[$("regionCode").value];
  channel.replaceChildren(new Option(band ? "Vælg frekvens" : "Ukendt region", ""));
  channel.disabled = !band;
  if (!band) return;
  for (let index = 0; index < band.count; index++) {
    const frequency = ((band.startKhz + index * band.stepKhz) / 1000).toFixed(3).replace(".", ",");
    channel.add(new Option(`${frequency} MHz`, index.toString(16).padStart(2, "0").toUpperCase()));
  }
}

function displayRegion(code) {
  const select = $("regionCode");
  const oldUnknown = select.querySelector("[data-unknown]");
  if (oldUnknown) oldUnknown.remove();
  if (!regionChannels[code]) {
    const option = new Option(`Ukendt region (${code})`, code);
    option.dataset.unknown = "true";
    select.add(option);
  }
  select.value = code;
  updateChannels();
}

async function fetchRegion() {
  const data = firstPayload(await sendFrame(0x08), 0x08);
  if (!data || data.length !== 1) throw new Error("Kunne ikke hente en gyldig regionskode fra læseren.");
  return data[0].toString(16).padStart(2, "0").toUpperCase();
}

function requireConfirmation(responses, command, name) {
  const data = firstPayload(responses, command);
  if (!data || data.length !== 1 || data[0] !== 0) {
    throw new Error(`${name} blev ikke bekræftet af læseren. Kontrollér den aktuelle indstilling.`);
  }
}

function passwordBytes(value, name = "Password") {
  const bytes = bytesFromHex(value, name);
  if (bytes.length !== 4) throw new Error(`${name} skal være 4 bytes (8 hex-tegn).`);
  return [...bytes];
}

function numberValue(id, name, min, max) {
  const value = Number($(id).value);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${name} skal være mellem ${min} og ${max}.`);
  }
  return value;
}

async function sendFrame(command, payload = [], timeout = 0.8) {
  const result = await api("/api/command", {
    method: "POST",
    body: JSON.stringify({ frame: frameHex(command, payload), timeout }),
  });
  await updateState(result.state);
  return result.responses;
}

async function guarded(action) {
  try {
    await action();
  } catch (error) {
    showToast(error.message || String(error), true);
  }
}

function responsePayload(hexFrame) {
  try {
    const bytes = bytesFromHex(hexFrame, "Svarramme");
    if (bytes.length < 7 || bytes[0] !== 0xBB || bytes.at(-1) !== 0x7E) return null;
    const length = (bytes[3] << 8) | bytes[4];
    if (bytes.length !== length + 7) return null;
    if ((bytes.slice(1, -2).reduce((sum, byte) => sum + byte, 0) & 0xFF) !== bytes.at(-2)) return null;
    return { type: bytes[1], command: bytes[2], payload: bytes.slice(5, -2) };
  } catch {
    return null;
  }
}

function firstPayload(responses, command) {
  for (const raw of responses || []) {
    const parsed = responsePayload(raw);
    if (parsed && parsed.command === command) return parsed.payload;
  }
  return null;
}

function bytesToHex(bytes) {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("").toUpperCase();
}

function renderTagTable(rows, bodyId, showRssi) {
  const body = $(bodyId);
  body.replaceChildren();
  if (!rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = showRssi ? 6 : 5;
    cell.className = "empty";
    cell.textContent = "Ingen tags endnu";
    row.append(cell);
    body.append(row);
    return;
  }
  rows.forEach((tag, index) => {
    const row = document.createElement("tr");
    row.dataset.epc = tag.epc;
    row.classList.toggle("selected", tag.epc === selectedEpc);
    const cells = [String(index + 1), tag.pc, tag.epc, tag.crc];
    if (showRssi) cells.push(`${tag.rssi_dbm} dBm`);
    cells.push(String(tag.count));
    for (const value of cells) {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (value === tag.epc) cell.className = "epc-cell";
      row.append(cell);
    }
    row.addEventListener("click", () => {
      selectedEpc = tag.epc;
      $("selectedTag").textContent = tag.epc;
      $("selectMask").value = tag.epc;
      $("selectLength").value = String(tag.epc.length * 4);
      renderTagTable(rows, bodyId, showRssi);
    });
    body.append(row);
  });
}

async function updateState(state) {
  if (!state) return;
  const key = state.connected ? `${state.port}:${state.baud}` : null;
  if (key !== connectionKey) {
    connectionKey = key;
    connectionGeneration++;
    versionsRequested = false;
    $("firmwareVersion").textContent = "—";
    $("hardwareVersion").textContent = "—";
  }
  inventoryRunning = Boolean(state.inventory_running);
  $("refreshVersions").disabled = !state.connected || inventoryRunning || versionsPending;
  const badge = $("connectionBadge");
  badge.classList.toggle("connected", state.connected);
  $("connectionText").textContent = state.connected
    ? `${state.port} · ${state.baud}`
    : "Ikke forbundet";
  $("connectButton").textContent = state.connected ? "Afbryd" : "Forbind";
  $("rxCounter").textContent = String(state.rx_bytes || 0);
  $("txCounter").textContent = String(state.tx_bytes || 0);
  $("stopRead").disabled = !state.inventory_running;
  $("readMulti").disabled = !state.connected || state.inventory_running;
  $("readSingle").disabled = !state.connected || state.inventory_running;
  renderTagTable(state.tags || [], "tagRows", true);
  renderTagTable(state.tags || [], "memoryTagRows", false);
  if ($("autoscroll").checked) {
    $("serialLog").textContent = (state.events || []).join("\n");
    $("serialLog").scrollTop = $("serialLog").scrollHeight;
  }
}

async function refreshState() {
  const state = await api("/api/state");
  await updateState(state);
  if (state.connected && !inventoryRunning && !versionsRequested && !versionsPending) {
    await getVersions();
  }
}

async function refreshPorts() {
  const result = await api("/api/ports");
  const input = $("portSelect");
  const selected = input.value;
  const ports = $("serialPorts");
  ports.replaceChildren();
  for (const port of result.ports) {
    const option = document.createElement("option");
    option.value = port.device;
    option.label = port.description;
    ports.append(option);
  }
  if (selected) input.value = selected;
  else if (result.ports.length === 1) input.value = result.ports[0].device;
}

async function setConnected() {
  const current = await api("/api/state");
  if (current.connected) {
    await api("/api/disconnect", { method: "POST" });
    showToast("Seriel forbindelse afbrudt.");
    await refreshState();
    return;
  }
  const port = $("portSelect").value;
  if (!port) throw new Error("Vælg en seriel port først.");
  await api("/api/connect", {
    method: "POST",
    body: JSON.stringify({ port, baud: Number($("baudSelect").value) }),
  });
  showToast(`Forbundet til ${port}.`);
  await refreshState();
}

async function getVersions() {
  if (!connectionKey || inventoryRunning || versionsPending) {
    throw new Error("Forbind læseren og stop inventering før versionerne hentes.");
  }
  const generation = connectionGeneration;
  const errors = [];
  versionsRequested = true;
  versionsPending = true;
  $("refreshVersions").disabled = true;
  try {
    for (const [type, id, label] of [[0x00, "hardwareVersion", "Hardware"], [0x01, "firmwareVersion", "Firmware"]]) {
      try {
        const payload = firstPayload(await sendFrame(0x03, [type]), 0x03);
        if (generation !== connectionGeneration) return;
        if (!payload || payload.length < 2 || payload[0] !== type) {
          throw new Error("intet gyldigt svar");
        }
        const version = new TextDecoder().decode(payload.slice(1)).replace(/\0/g, "").trim();
        if (!version) throw new Error("tomt svar");
        $(id).textContent = version;
      } catch (error) {
        if (generation !== connectionGeneration) return;
        $(id).textContent = "ikke tilgængelig";
        errors.push(`${label}: ${error.message || String(error)}`);
      }
    }
    if (errors.length) showToast(`Kunne ikke hente version: ${errors.join("; ")}`, true);
  } finally {
    versionsPending = false;
    $("refreshVersions").disabled = !connectionKey || inventoryRunning;
  }
}

function requireConfigEnabled() {
  if (!$("configEnabled").checked) throw new Error("Aktivér konfiguration før du ændrer læserindstillinger.");
}

function queryPayload() {
  const dr = Number($("queryDr").value);
  const m = Number($("queryM").value);
  const trext = Number($("queryTrext").value);
  const sel = Number($("querySel").value);
  const session = Number($("querySession").value);
  const target = Number($("queryTarget").value);
  const q = numberValue("queryQ", "Q", 0, 15);
  return [((dr & 1) << 7) | ((m & 3) << 5) | ((trext & 1) << 4) | ((sel & 3) << 2) | (session & 3), ((q & 15) << 3) | ((target & 1) << 2)];
}

function selectPayload() {
  const target = numberValue("selectTarget", "Select target", 0, 7);
  const action = numberValue("selectAction", "Select action", 0, 7);
  const bank = numberValue("selectBank", "Select bank", 0, 3);
  const pointer = numberValue("selectPointer", "Bit-pointer", 0, 0xFFFFFFFF);
  const bitLength = numberValue("selectLength", "Mask-længde", 0, 0xFF);
  const mask = [...bytesFromHex($("selectMask").value, "Select-mask")];
  if (bitLength > mask.length * 8) throw new Error("Select-masken er kortere end den angivne mask-længde.");
  const parameter = ((target & 7) << 5) | ((action & 7) << 2) | (bank & 3);
  return [parameter, (pointer >>> 24) & 0xFF, (pointer >>> 16) & 0xFF, (pointer >>> 8) & 0xFF, pointer & 0xFF, bitLength, $("selectTruncate").checked ? 0x80 : 0x00, ...mask];
}

async function ensureTagSelected() {
  if (!selectedEpc) throw new Error("Vælg et tag i EPC-tabellen først.");
  const epc = bytesFromHex(selectedEpc, "EPC");
  const payload = [0x01, 0x00, 0x00, 0x00, 0x20, epc.length * 8, 0x00, ...epc];
  for (const [command, parameters] of [[0x0C, payload], [0x12, [0x02]]]) {
    const responses = await sendFrame(command, parameters);
    const error = firstPayload(responses, 0xFF);
    if (error) throw new Error(`Select fejlede: ${bytesToHex(error)}.`);
    const response = firstPayload(responses, command);
    if (!response || response.length !== 1 || response[0] !== 0) {
      throw new Error("Select blev ikke bekræftet af læseren; tagkommandoen blev afbrudt.");
    }
  }
}

function selectedMemoryFields() {
  const bank = numberValue("memoryBank", "Memory bank", 0, 3);
  const pointer = numberValue("wordPointer", "Word-pointer", 0, 0xFFFF);
  const count = numberValue("wordCount", "Word-antal", 1, 32);
  const password = passwordBytes($("accessPassword").value, "Access password");
  return { bank, pointer, count, password };
}

async function readMemory() {
  await ensureTagSelected();
  const { bank, pointer, count, password } = selectedMemoryFields();
  const payload = [...password, bank, (pointer >> 8) & 0xFF, pointer & 0xFF, (count >> 8) & 0xFF, count & 0xFF];
  const responses = await sendFrame(0x39, payload, 1.5);
  $("memoryStatus").textContent = responses.length ? "Svar modtaget — se rammer i loggen" : "Intet svar";
}

async function writeMemory() {
  if (!window.confirm("Skriv data til tagget? Denne handling ændrer taghukommelsen.")) return;
  await ensureTagSelected();
  const { bank, pointer, password } = selectedMemoryFields();
  const data = [...bytesFromHex($("memoryData").value, "Data")];
  if (!data.length || data.length % 2 !== 0 || data.length > 64) {
    throw new Error("Skrivedata skal være 2–64 bytes og have et lige antal bytes.");
  }
  const words = data.length / 2;
  const payload = [...password, bank, (pointer >> 8) & 0xFF, pointer & 0xFF, (words >> 8) & 0xFF, words & 0xFF, ...data];
  const responses = await sendFrame(0x49, payload, 1.5);
  $("memoryStatus").textContent = responses.length ? "Skrivekommando sendt — kontrollér svaret" : "Intet svar";
}

function lockPayload() {
  const password = passwordBytes($("accessPassword").value, "Access password");
  const ids = ["lockKill", "lockAccess", "lockEpc", "lockTid", "lockUser"];
  let lockData = 0;
  ids.forEach((id, index) => {
    const action = Number($(id).value);
    if (action > 0) {
      const field = (0x3 << 2) | ((action - 1) & 0x3);
      lockData |= field << ((4 - index) * 4);
    }
  });
  return [...password, (lockData >> 16) & 0xFF, (lockData >> 8) & 0xFF, lockData & 0xFF];
}

function queryFromResponse(responses) {
  const payload = firstPayload(responses, 0x0D);
  if (!payload || payload.length < 2) return;
  $("queryDr").value = String((payload[0] >> 7) & 1);
  $("queryM").value = String((payload[0] >> 5) & 3);
  $("queryTrext").value = String((payload[0] >> 4) & 1);
  $("querySel").value = String((payload[0] >> 2) & 3);
  $("querySession").value = String(payload[0] & 3);
  $("queryQ").value = String((payload[1] >> 3) & 15);
  $("queryTarget").value = String((payload[1] >> 2) & 1);
}

function drawChart(canvasId, spectrum) {
  const canvas = $(canvasId);
  const context = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(1, canvas.clientWidth);
  const height = Math.max(1, canvas.clientHeight);
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  context.scale(ratio, ratio);
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#fff";
  context.fillRect(0, 0, width, height);

  const left = 12;
  const right = 9;
  const top = 13;
  const bottom = 25;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  if (!spectrum) {
    context.fillStyle = "#666";
    context.font = "12px Segoe UI, sans-serif";
    context.textAlign = "center";
    context.fillText("Ingen målinger endnu", width / 2, height / 2);
    return;
  }
  const { start, values } = spectrum;
  const count = values.length;
  const minValue = -128;
  const maxValue = Math.max(0, ...values);
  context.lineWidth = 1;
  const slot = plotWidth / count;
  values.forEach((value, index) => {
    const normalized = Math.min(1, Math.max(0, (value - minValue) / (maxValue - minValue)));
    const barHeight = Math.max(2, normalized * plotHeight);
    const x = left + index * slot + slot * 0.14;
    const barWidth = Math.max(1, slot * 0.72);
    context.fillStyle = "#bfd0ff";
    context.strokeStyle = "#5567bc";
    context.fillRect(x, top + plotHeight - barHeight, barWidth, barHeight);
    context.strokeRect(x, top + plotHeight - barHeight, barWidth, barHeight);
    if (slot >= 10 || index % 5 === 0 || index === count - 1) {
      context.fillStyle = "#58677c";
      context.font = "8px Segoe UI, sans-serif";
      context.textAlign = "center";
      context.fillText(`${start + index}`, x + barWidth / 2, height - 7);
    }
  });
  context.fillStyle = "#58677c";
  context.font = "9px Segoe UI, sans-serif";
  context.textAlign = "left";
  context.fillText("dBm", left + 2, 10);
}

async function runSpectrum(command, canvasId, label) {
  const responses = await sendFrame(command, [], 2.5);
  const response = (responses || []).map(responsePayload)
    .find((frame) => frame && frame.type === 0x01 && frame.command === command);
  const payload = response && response.payload;
  if (!payload || payload.length < 3) {
    throw new Error(`${label}: intet gyldigt målesvar.`);
  }
  const start = payload[0];
  const end = payload[1];
  if (end < start || payload.length !== end - start + 3) {
    throw new Error(`${label}: kanalinterval og antal måleværdier stemmer ikke.`);
  }
  const values = [...payload.slice(2)].map((value) => value > 127 ? value - 256 : value);
  const spectrum = { start, values };
  chartValues[canvasId] = spectrum;
  drawChart(canvasId, spectrum);
  const rangeId = canvasId === "jammerChart" ? "jammerRange" : "rssiRange";
  $(rangeId).textContent = `Kanaler ${start}–${end} · -128–${Math.max(0, ...values)} dBm`;
  showToast(`${label}: ${values.length} kanalværdier modtaget.`);
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((tab) => {
        const active = tab === button;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
      });
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        const active = panel.id === `panel-${button.dataset.tab}`;
        panel.classList.toggle("active", active);
        panel.hidden = !active;
      });
      if (button.dataset.tab === "test") {
        drawChart("jammerChart", chartValues.jammerChart);
        drawChart("rssiChart", chartValues.rssiChart);
      }
    });
  });
}

function bindActions() {
  $("refreshVersions").addEventListener("click", () => guarded(getVersions));
  $("refreshPorts").addEventListener("click", () => guarded(refreshPorts));
  $("connectButton").addEventListener("click", () => guarded(setConnected));
  $("clearTags").addEventListener("click", () => guarded(async () => { await api("/api/tags", { method: "DELETE" }); await refreshState(); }));
  $("clearMemoryTags").addEventListener("click", () => guarded(async () => { await api("/api/tags", { method: "DELETE" }); await refreshState(); }));
  $("clearLog").addEventListener("click", () => guarded(async () => {
    const state = await api("/api/events", { method: "DELETE" });
    await updateState(state);
  }));
  $("sendRaw").addEventListener("click", () => guarded(async () => {
    const raw = $("rawHex").value;
    const frame = responsePayload(raw);
    if (!frame) {
      const bytes = bytesFromHex(raw, "UART-ramme");
      if (bytes.length < 7) throw new Error("UART-rammen er for kort.");
    }
    await api("/api/command", { method: "POST", body: JSON.stringify({ frame: raw, timeout: 1 }) });
    await refreshState();
  }));
  $("readSingle").addEventListener("click", () => guarded(async () => {
    if ($("selectBeforeRead").checked) await ensureTagSelected();
    const result = await api("/api/inventory/single", { method: "POST" });
    await updateState(result.state);
  }));
  $("readMulti").addEventListener("click", () => guarded(async () => {
    const count = numberValue("multiCount", "Antal forespørgsler", 1, 65535);
    await api("/api/inventory/start", { method: "POST", body: JSON.stringify({ count }) });
    await refreshState();
  }));
  $("stopRead").addEventListener("click", () => guarded(async () => {
    const result = await api("/api/inventory/stop", { method: "POST" });
    await updateState(result);
  }));
  $("continueRead").addEventListener("click", () => guarded(async () => {
    const duration = numberValue("continueMs", "Varighed", 100, 60000);
    const result = await api("/api/inventory/start", { method: "POST", body: JSON.stringify({ count: 65535 }) });
    await updateState(result);
    clearTimeout(continueTimer);
    continueTimer = setTimeout(() => guarded(async () => {
      const stopped = await api("/api/inventory/stop", { method: "POST" });
      await updateState(stopped);
    }), duration);
  }));
  $("cwButton").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    if (!window.confirm("Send en RF-bærebølge i maks. to sekunder til en kontrolleret test?")) return;
    await sendFrame(0xB0, [0xFF]);
    $("cwButton").disabled = true;
    showToast("CW-test startet; bærebølgen slukkes automatisk efter 2 sekunder.");
    clearTimeout(cwTimer);
    cwTimer = setTimeout(() => {
      $("cwButton").disabled = false;
      showToast("CW-test afsluttet.");
    }, 2000);
  }));
  $("setMode").addEventListener("click", () => guarded(() => {
    requireConfigEnabled();
    showToast("Modtagerprofil kræver en separat firmwarekommando, som endnu ikke er bekræftet.", true);
  }));
  $("saveConfig").addEventListener("click", () => showToast("Gem-konfiguration er ikke defineret i den tilgængelige M100-protokolreference.", true));
  $("regionCode").addEventListener("change", updateChannels);
  $("getRegion").addEventListener("click", () => guarded(async () => {
    displayRegion(await fetchRegion());
  }));
  $("setRegion").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    const code = $("regionCode").value;
    if (!regionChannels[code]) throw new Error("Vælg en dokumenteret region.");
    if (!window.confirm("Skift region på RFID-læseren? Kontrollér først lokale radioregler og firmwarekoder.")) return;
    requireConfirmation(await sendFrame(0x07, [hexByte(code, "Regionskode")]), 0x07, "Regionsændringen");
    const actual = await fetchRegion();
    displayRegion(actual);
    if (actual !== code) throw new Error("Læseren rapporterer en anden region end den valgte.");
    showToast("Regionen er bekræftet af læseren.");
  }));
  $("setChannel").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    const region = $("regionCode").value;
    const channel = $("rfChannel").value;
    if (!regionChannels[region] || !channel) throw new Error("Vælg en region og en frekvens.");
    const index = hexByte(channel, "RF-kanal");
    if (index >= regionChannels[region].count) throw new Error("RF-kanalen er ikke et kendt frekvensvalg for regionen.");
    if (!window.confirm("Skift RF-kanal? Kontrollér først, at frekvensen er tilladt og understøttet.")) return;
    const actual = await fetchRegion();
    if (actual !== region || $("regionCode").value !== region || $("rfChannel").value !== channel) {
      throw new Error("Regionen eller frekvensen stemmer ikke med læseren. Hent regionen og prøv igen.");
    }
    requireConfirmation(await sendFrame(0xAB, [index]), 0xAB, "Kanalændringen");
    showToast("RF-kanalen er bekræftet af læseren.");
  }));
  $("getChannel").addEventListener("click", () => guarded(async () => {
    const region = await fetchRegion();
    const data = firstPayload(await sendFrame(0xAA), 0xAA);
    if (!data || data.length !== 1) throw new Error("Kunne ikke hente en gyldig RF-kanal fra læseren.");
    displayRegion(region);
    const code = data[0].toString(16).padStart(2, "0").toUpperCase();
    if (!regionChannels[region] || data[0] >= regionChannels[region].count) {
      const option = new Option(`Ukendt kanal (${code})`, code);
      $("rfChannel").add(option);
      $("rfChannel").value = code;
      throw new Error("Kanalen ligger uden for de kendte frekvensvalg for regionen.");
    }
    $("rfChannel").value = code;
  }));
  $("insertChannels").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    const start = numberValue("hopStart", "Hop-start", 0, 255);
    const stop = numberValue("hopStop", "Hop-stop", start, 255);
    if (stop - start > 20) throw new Error("Vælg højst 21 kanaler ad gangen.");
    const channels = [];
    for (let channel = start; channel <= stop; channel += 1) channels.push(channel);
    await sendFrame(0xA9, [channels.length, ...channels]);
  }));
  $("fhssOff").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    if (!window.confirm("Slå frekvenshop fra? Kontrollér, at den faste kanal er tilladt i din region.")) return;
    await sendFrame(0xAD, [0x00]);
  }));
  $("setPower").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    const power = Number($("powerSelect").value);
    await sendFrame(0xB6, [(power >> 8) & 0xFF, power & 0xFF]);
  }));
  $("getPower").addEventListener("click", () => guarded(async () => {
    const data = firstPayload(await sendFrame(0xB7), 0xB7);
    if (data && data.length >= 2) {
      const current = (data[0] << 8) | data[1];
      const option = [...$("powerSelect").options].find((item) => Number(item.value) === current);
      if (option) $("powerSelect").value = option.value;
      showToast(`Læserens sendeeffekt: ${(current / 100).toFixed(2)} dBm`);
    }
  }));
  $("sleepButton").addEventListener("click", () => guarded(async () => {
    if (!window.confirm("Sæt læsermodulet i dvale? Det kan kræve en ny kommando eller genstart for at vågne.")) return;
    await sendFrame(0x17);
  }));
  $("getQuery").addEventListener("click", () => guarded(async () => queryFromResponse(await sendFrame(0x0D))));
  $("setQuery").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    await sendFrame(0x0E, queryPayload());
  }));
  $("setSelect").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    await sendFrame(0x0C, selectPayload());
  }));
  $("getSelect").addEventListener("click", () => guarded(async () => {
    const data = firstPayload(await sendFrame(0x0B), 0x0B);
    if (data) showToast(`Select-svar: ${bytesToHex(data)}`);
  }));
  $("setSelectMode").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    await sendFrame(0x12, [0x01]);
  }));
  $("readMemory").addEventListener("click", () => guarded(readMemory));
  $("writeMemory").addEventListener("click", () => guarded(writeMemory));
  $("lockTag").addEventListener("click", () => guarded(async () => {
    if (!selectedEpc) throw new Error("Vælg et tag i EPC-tabellen først.");
    if (!window.confirm("Send Lock-kommandoen til det valgte tag? Permanent låsning kan ikke fortrydes.")) return;
    await ensureTagSelected();
    const payload = lockPayload();
    if (payload.slice(4).every((byte) => byte === 0)) throw new Error("Vælg mindst én låsehandling.");
    const responses = await sendFrame(0x82, payload, 1.5);
    $("memoryStatus").textContent = responses.length ? "Lock-kommando sendt — kontrollér svaret" : "Intet svar";
  }));
  $("killTag").addEventListener("click", () => guarded(async () => {
    if ($("killConfirm").value !== "KILL") throw new Error("Skriv KILL for at bekræfte permanent deaktivering.");
    if (!window.confirm(`Deaktivér tag ${selectedEpc || "(intet valgt)"} permanent?`)) return;
    await ensureTagSelected();
    const password = passwordBytes($("killPassword").value, "Kill password");
    const responses = await sendFrame(0x65, password, 1.5);
    $("memoryStatus").textContent = responses.length ? "Kill-kommando sendt — kontrollér svaret" : "Intet svar";
  }));
  $("blockPermalock").addEventListener("click", () => guarded(async () => {
    if (!window.confirm("BlockPermalock kan låse brugerblokke permanent. Fortsæt kun med et testtag.")) return;
    await ensureTagSelected();
    const password = passwordBytes($("accessPassword").value, "Access password");
    const pointer = numberValue("blockPointer", "Block-pointer", 0, 0xFFFF);
    const range = numberValue("blockRange", "Block-range", 1, 255);
    const mask = [...bytesFromHex($("blockMask").value, "Block-mask")];
    const operation = hexByte($("blockOperation").value, "BlockPermalock-operation");
    const payload = [...password, operation, (pointer >> 8) & 0xFF, pointer & 0xFF, range, ...(operation === 0x01 ? mask : [])];
    await sendFrame(0xD3, payload, 1.5);
  }));
  $("qtCommand").addEventListener("click", () => guarded(async () => {
    if ($("qtAction").value === "02" && !window.confirm("QT Write ændrer tagindstillinger. Fortsæt kun med et testtag.")) return;
    await ensureTagSelected();
    const password = passwordBytes($("accessPassword").value, "Access password");
    const action = hexByte($("qtAction").value, "QT-kommando");
    const payload = [...password, action, $("qtSr").checked ? 1 : 0, $("qtMem").checked ? 1 : 0, 0x00];
    await sendFrame(0xE5, payload, 1.5);
  }));
  $("nxpProtect").addEventListener("click", () => guarded(async () => {
    await ensureTagSelected();
    await sendFrame(0xE1, [...passwordBytes($("accessPassword").value, "Access password"), 0x00], 1.5);
  }));
  $("nxpReset").addEventListener("click", () => guarded(async () => {
    await ensureTagSelected();
    await sendFrame(0xE1, [...passwordBytes($("accessPassword").value, "Access password"), 0x01], 1.5);
  }));
  $("nxpEas").addEventListener("click", () => guarded(async () => {
    if (!window.confirm("Ændr EAS-status på det valgte NXP-tag?")) return;
    await ensureTagSelected();
    const easState = hexByte($("nxpEasState").value, "EAS-status");
    const payload = [...passwordBytes($("accessPassword").value, "Access password"), easState];
    await sendFrame(0xE3, payload, 1.5);
  }));
  $("nxpAlarm").addEventListener("click", () => guarded(async () => {
    await ensureTagSelected();
    const responses = await sendFrame(0xE4, [], 1.5);
    showToast(responses.length ? `EAS-svar: ${responses.join(" | ")}` : "Intet EAS-svar modtaget.");
  }));
  $("nxpConfig").addEventListener("click", () => guarded(async () => {
    if (!window.confirm("Skriv nyt NXP-config-word til valgt tag?")) return;
    await ensureTagSelected();
    const configWord = [...bytesFromHex($("configWord").value, "Config-word")];
    if (configWord.length !== 2) throw new Error("Config-word skal være to bytes.");
    await sendFrame(0xE0, [...passwordBytes($("accessPassword").value, "Access password"), ...configWord], 1.5);
  }));
  $("scanJammer").addEventListener("click", () => guarded(() => runSpectrum(0xF2, "jammerChart", "Blokeringsscan")));
  $("scanRssi").addEventListener("click", () => guarded(() => runSpectrum(0xF3, "rssiChart", "RSSI-scan")));
  $("setModem").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    const threshold = [...bytesFromHex($("threshold").value, "Threshold")];
    if (threshold.length !== 2) throw new Error("Threshold skal være to bytes.");
    const payload = [Number($("mixerGain").value), Number($("ifGain").value), ...threshold];
    await sendFrame(0xF0, payload);
  }));
  $("getModem").addEventListener("click", () => guarded(async () => {
    const data = firstPayload(await sendFrame(0xF1), 0xF1);
    if (data && data.length >= 4) {
      $("mixerGain").value = String(data[0]);
      $("ifGain").value = String(data[1]);
      $("threshold").value = bytesToHex(data.slice(2, 4));
    }
  }));
}

tokenForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (event.submitter && event.submitter.value === "cancel") {
    tokenDialog.close("cancel");
    return;
  }
  const token = $("tokenInput").value.trim();
  if (!token) return;
  sessionStorage.setItem("rfid-app-token", token);
  $("tokenInput").value = "";
  tokenDialog.close("ok");
  guarded(async () => {
    await refreshPorts();
    await refreshState();
  });
});

bindTabs();
bindActions();

(async function initialize() {
  try {
    const config = await api("/api/config");
    authRequired = config.auth_required;
    if (authRequired && !sessionStorage.getItem("rfid-app-token")) showTokenDialog();
    drawChart("jammerChart", chartValues.jammerChart);
    drawChart("rssiChart", chartValues.rssiChart);
    await refreshPorts();
    await refreshState();
    refreshTimer = setInterval(() => guarded(refreshState), 1200);
  } catch (error) {
    showToast(error.message || String(error), true);
  }
})();

window.addEventListener("resize", () => {
  drawChart("jammerChart", chartValues.jammerChart);
  drawChart("rssiChart", chartValues.rssiChart);
});
