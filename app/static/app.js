"use strict";

const $ = (id) => document.getElementById(id);
const tokenDialog = $("tokenDialog");
const tokenForm = $("tokenForm");
let authRequired = false;
let selectedEpc = "";
let selectionGeneration = 0;
let refreshTimer = null;
let continueTimer = null;
let cwTimer = null;
let toastTimer = null;
let connectionKey = null;
let connectionGeneration = 0;
let inventoryRunning = false;
let versionsRequested = false;
let versionsPending = false;
let snifferRules = [];
let selectedSnifferRule = null;
let selectedSnifferVersion = null;
let snifferRulesLoaded = false;
let bridgeBusy = false;
let bridgeWanIp = null;
let bridgeActive = false;
let savedPrinterAddress = null;
let captureBusy = false;
let latestCaptureId = null;
let latestExtractionKey = null;
let snifferPreviewGeneration = 0;
let jobLogBusy = false;
let jobLogJobs = [];
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

async function refreshWanIp() {
  const label = $("wanIp");
  try {
    const data = await api("/api/network");
    label.textContent = `WAN IP: ${data.wan_ip || bridgeWanIp || "Ingen dynamisk IP"}`;
    label.title = "Dynamisk IPv4-adresse på WAN eller bridge";
  } catch (error) {
    label.textContent = "WAN IP: Kan ikke hentes";
    label.title = error.message || String(error);
  }
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Content-Type", "application/json");
  const token = authRequired ? localStorage.getItem("rfid-app-token") : null;
  if (token) headers.set("X-API-Token", token);
  const response = await fetch(path, { ...options, headers });
  let data;
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (response.status === 401) {
    authRequired = true;
    $("forgetToken").hidden = false;
    $("noAuthWarning").hidden = true;
    if (token && localStorage.getItem("rfid-app-token") === token) {
      localStorage.removeItem("rfid-app-token");
    }
    showTokenDialog();
    throw new Error(data.detail || "App-token mangler eller er forkert.");
  }
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

async function refreshBridge() {
  if (authRequired && !localStorage.getItem("rfid-app-token")) {
    $("bridgeStatus").textContent = "Bridge-status afventer login.";
    return;
  }
  try {
    const state = await api("/api/bridge");
    bridgeActive = state.active;
    bridgeWanIp = state.wan_ip;
    const configuredAddress = `${state.printer.ip}:${state.printer.port}`;
    if (savedPrinterAddress === null || state.active) $("printerAddress").value = configuredAddress;
    savedPrinterAddress = configuredAddress;
    const locked = bridgeBusy || state.active || state.transitioning || state.pending_confirmation || state.needs_recovery;
    $("printerAddress").disabled = locked;
    $("savePrinterAddress").disabled = locked || $("printerAddress").value.trim() === savedPrinterAddress;
    $("startBridge").disabled = locked || $("printerAddress").value.trim() !== savedPrinterAddress;
    $("stopBridge").disabled = bridgeBusy || !state.active;
    $("bridgeConfirmLink").href = `http://${state.printer.ip}:8080/`;
    $("bridgeConfirmLink").textContent = `http://${state.printer.ip}:8080/`;
    $("bridgeStatus").textContent = state.needs_recovery
      ? "Bridge kræver gendannelse. Kontrollér NanoPi'ens tjenestelog eller genstart."
      : state.transitioning && !state.active
        ? "Bridgen stoppes, og WAN gendannes."
      : state.active
        ? (state.pending_confirmation ? "Bridge starter. Åbn printeradressen for at bekræfte adgangen." : "Bridge er aktiv.")
        : (state.pending_confirmation ? "Bridge starter eller gendannes." : "Bridge er slået fra.");
  } catch (error) {
    bridgeActive = false;
    bridgeWanIp = null;
    $("startBridge").disabled = true;
    $("stopBridge").disabled = true;
    $("printerAddress").disabled = true;
    $("savePrinterAddress").disabled = true;
    $("bridgeStatus").textContent = `Bridge-styring ikke tilgængelig: ${error.message || String(error)}`;
  }
}

async function refreshCapture() {
  if (captureBusy) return;
  if (!bridgeActive) {
    $("captureStatus").textContent = "Printeropsamling afventer aktiv bridge.";
    return;
  }
  captureBusy = true;
  try {
    const data = await api("/api/sniffer/capture");
    if (data.error) throw new Error(data.error);
    const latest = data.latest;
    if (latest && latest.id !== latestCaptureId) {
      latestCaptureId = latest.id;
      $("snifferTestData").value = latest.text;
      clearSnifferPreview("Automatisk udtræk afventer.");
      $("snifferResultSource").textContent = "Automatisk udtræk fra seneste printdata.";
    }
    if (latest && latest.extraction) {
      const extraction = latest.extraction;
      const key = `${latest.id}:${extraction.state}:${Boolean(latest.incomplete)}`;
      if (key !== latestExtractionKey) {
        latestExtractionKey = key;
        if (extraction.state === "done") {
          snifferPreviewGeneration++;
          renderSnifferResults(extraction.results);
          $("snifferResultSource").textContent = "Automatisk udtræk fra seneste printdata.";
        } else if (extraction.state === "error") {
          clearSnifferPreview(extraction.error || "Automatisk udtræk fejlede.");
          $("snifferResultSource").textContent = "Automatisk udtræk fejlede.";
        }
      }
    }
    const warnings = [];
    if (data.packet_loss || (latest && latest.incomplete)) warnings.push("Data kan være ufuldstændige.");
    if (latest && latest.invalid_utf8) warnings.push("Binære eller ugyldige UTF-8-data vises ikke præcist.");
    if (latest && latest.extraction_errors) warnings.push("Et eller flere automatiske udtræk fejlede i denne session.");
    $("captureStatus").textContent = (latest ? "Seneste printdata er modtaget." : "Opsamling er aktiv; afventer printdata.")
      + (warnings.length ? ` ${warnings.join(" ")}` : "");
  } catch (error) {
    $("captureStatus").textContent = `Printeropsamling ikke tilgængelig: ${error.message || String(error)}`;
  } finally {
    captureBusy = false;
  }
}

async function changeBridge(action) {
  bridgeBusy = true;
  $("startBridge").disabled = true;
  $("stopBridge").disabled = true;
  try {
    await api(`/api/bridge/${action}`, { method: "POST" });
    if (action === "start") showToast("Åbn printeradressen inden 2 minutter for at beholde bridgen.");
    if (action === "stop") showToast("Bridgen stoppes. NanoPi'ens DHCP-adresse kan ændre sig.");
  } finally {
    bridgeBusy = false;
    await refreshBridge();
  }
}

async function confirmBridgeIfNeeded() {
  const state = await api("/api/bridge");
  if (location.hostname === state.printer.ip && state.active && state.pending_confirmation) {
    await api("/api/bridge/confirm", { method: "POST" });
  }
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
      if (selectedEpc !== tag.epc) {
        selectionGeneration++;
        if ($("memoryReadData")) $("memoryReadData").textContent = "—";
        $("memoryStatus").textContent = "Valgt tag ændret.";
      }
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
  if (selectedEpc && (!state.connected || !(state.tags || []).some((tag) => tag.epc === selectedEpc))) {
    selectedEpc = "";
    selectionGeneration++;
    $("selectedTag").textContent = "—";
    if ($("memoryReadData")) $("memoryReadData").textContent = "—";
    $("memoryStatus").textContent = "Valgt tag er ikke længere i tabellen.";
  }
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
  if (dr !== 0 || m !== 0 || trext !== 1) throw new Error("Læseren understøtter kun DR=8, M=1 og pilottonen aktiveret.");
  return [((dr & 1) << 7) | ((m & 3) << 5) | ((trext & 1) << 4) | ((sel & 3) << 2) | (session & 3), ((q & 15) << 3) | ((target & 1) << 2)];
}

function selectPayload() {
  const target = numberValue("selectTarget", "Select target", 0, 4);
  const action = numberValue("selectAction", "Select action", 0, 7);
  const bank = numberValue("selectBank", "Select bank", 0, 3);
  const pointer = numberValue("selectPointer", "Bit-pointer", 0, 0xFFFFFFFF);
  const bitLength = numberValue("selectLength", "Mask-længde", 0, 0xFF);
  const mask = [...bytesFromHex($("selectMask").value, "Select-mask")];
  if (mask.length !== Math.ceil(bitLength / 8)) throw new Error("Select-maskens byteantal skal svare til mask-længden i bit.");
  const parameter = ((target & 7) << 5) | ((action & 7) << 2) | (bank & 3);
  return [parameter, (pointer >>> 24) & 0xFF, (pointer >>> 16) & 0xFF, (pointer >>> 8) & 0xFF, pointer & 0xFF, bitLength, $("selectTruncate").checked ? 0x80 : 0x00, ...mask];
}

function selectFromResponse(responses) {
  const data = firstPayload(responses, 0x0B);
  if (!data || data.length < 7 || data.length !== 7 + Math.ceil(data[5] / 8) ||
      (data[0] >> 5) > 4 || (data[6] !== 0 && data[6] !== 0x80)) {
    throw new Error("Ugyldigt Select-svar fra læseren.");
  }
  $("selectTarget").value = String(data[0] >> 5);
  $("selectAction").value = String((data[0] >> 2) & 7);
  $("selectBank").value = String(data[0] & 3);
  $("selectPointer").value = String(data[1] * 0x1000000 + (data[2] << 16) + (data[3] << 8) + data[4]);
  $("selectLength").value = String(data[5]);
  $("selectTruncate").checked = data[6] === 0x80;
  $("selectMask").value = bytesToHex(data.slice(7));
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
  if (!$("memoryReadData")) throw new Error("Opdatér siden med Ctrl+F5 før taghukommelse læses.");
  $("readMemory").disabled = true;
  $("memoryReadData").textContent = "—";
  $("memoryStatus").textContent = "Læser...";
  try {
    const epc = selectedEpc;
    const generation = selectionGeneration;
    const connection = connectionGeneration;
    if (!epc) throw new Error("Vælg et tag i EPC-tabellen først.");
    const { bank, pointer, count, password } = selectedMemoryFields();
    await ensureTagSelected();
    if (selectionGeneration !== generation || connectionGeneration !== connection) {
      throw new Error("Tag eller forbindelse blev ændret; læsningen blev afbrudt.");
    }
    const payload = [...password, bank, (pointer >> 8) & 0xFF, pointer & 0xFF, (count >> 8) & 0xFF, count & 0xFF];
    const responses = await sendFrame(0x39, payload, 1.5);
    if (selectionGeneration !== generation || connectionGeneration !== connection) {
      throw new Error("Tag eller forbindelse blev ændret; læseresultatet blev afvist.");
    }
    const frames = (responses || []).map(responsePayload).filter(Boolean);
    const error = frames.find((frame) => frame.type === 0x01 && frame.command === 0xFF);
    if (error) {
      const code = error.payload.length ? `0x${error.payload[0].toString(16).padStart(2, "0").toUpperCase()}` : "ukendt";
      throw new Error(`Læseren afviste taglæsningen (${code}); se rammer i loggen.`);
    }
    const replies = frames.filter((frame) => frame.type === 0x01 && frame.command === 0x39);
    if (replies.length !== 1) throw new Error("Intet entydigt læsesvar fra læseren.");
    const data = replies[0].payload;
    const idLength = data[0];
    if (idLength < 2 || data.length !== 1 + idLength + count * 2) {
      throw new Error("Ugyldig længde i læsesvaret.");
    }
    if (bytesToHex(data.slice(3, 1 + idLength)) !== epc) {
      throw new Error("Læsesvaret kom fra et andet EPC; resultatet blev afvist.");
    }
    $("memoryReadData").textContent = [...data.slice(1 + idLength)]
      .map((byte) => byte.toString(16).padStart(2, "0").toUpperCase()).join(" ");
    $("memoryStatus").textContent = `Læst ${count} ord fra bank ${bank}, adresse ${pointer}.`;
  } catch (error) {
    $("memoryStatus").textContent = error.message || String(error);
    throw error;
  } finally {
    $("readMemory").disabled = false;
  }
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
    const value = $(id).value;
    if (value === "") return;
    if (!["0", "1", "2", "3"].includes(value)) throw new Error("Ugyldig låsehandling.");
    const action = Number(value);
    const shift = (4 - index) * 2;
    const mask = action & 1 ? 3 : 2;
    lockData |= (mask << (10 + shift)) | (action << shift);
  });
  return [...password, (lockData >> 16) & 0xFF, (lockData >> 8) & 0xFF, lockData & 0xFF];
}

function blockPermalockPayload() {
  const password = passwordBytes($("accessPassword").value, "Access password");
  const operation = hexByte($("blockOperation").value, "BlockPermalock-operation");
  if (operation !== 0 && operation !== 1) throw new Error("Ugyldig BlockPermalock-operation.");
  const pointer = numberValue("blockPointer", "Block-pointer", 0, 0xFFFF);
  const range = numberValue("blockRange", "Block-range", 1, 255);
  const mask = operation === 1 ? [...bytesFromHex($("blockMask").value, "Block-mask")] : [];
  if (operation === 1 && mask.length !== range * 2) throw new Error("Block-mask skal indeholde to bytes pr. Block-range.");
  return [...password, operation, 0x03, (pointer >> 8) & 0xFF, pointer & 0xFF, range, ...mask];
}

function qtPayload() {
  const password = passwordBytes($("accessPassword").value, "Access password");
  const action = hexByte($("qtAction").value, "QT-kommando");
  const persistence = hexByte($("qtPersistence").value, "QT-lagring");
  if ((action !== 0 && action !== 1) || (persistence !== 0 && persistence !== 1)) throw new Error("Ugyldige QT-parametre.");
  const control = ($("qtSr").checked ? 0x80 : 0) | ($("qtMem").checked ? 0x40 : 0);
  return [...password, action, persistence, control, 0];
}

function tagReply(responses, command, epc, resultLength) {
  const frames = (responses || []).map(responsePayload).filter(Boolean);
  const error = frames.find((frame) => frame.type === 0x01 && frame.command === 0xFF);
  if (error) throw new Error(`Læseren afviste kommandoen: ${bytesToHex(error.payload) || "ukendt fejl"}.`);
  const replies = frames.filter((frame) => frame.type === 0x01 && frame.command === command);
  if (replies.length !== 1) throw new Error("Intet entydigt svar fra læseren.");
  const data = replies[0].payload;
  const idLength = data[0];
  if (idLength < 2 || data.length !== 1 + idLength + resultLength ||
      bytesToHex(data.slice(3, 1 + idLength)) !== epc) {
    throw new Error("Svaret har forkert længde eller stammer fra et andet tag.");
  }
  return data.slice(1 + idLength);
}

async function sendSelectedTagFrame(command, payload, responseCommand, resultLength) {
  const epc = selectedEpc;
  const generation = selectionGeneration;
  const connection = connectionGeneration;
  await ensureTagSelected();
  if (selectionGeneration !== generation || connectionGeneration !== connection) {
    throw new Error("Tag eller forbindelse blev ændret; kommandoen blev afbrudt.");
  }
  const responses = await sendFrame(command, payload, 1.5);
  if (selectionGeneration !== generation || connectionGeneration !== connection) {
    throw new Error("Tag eller forbindelse blev ændret; kontrollér kommandosvaret i loggen.");
  }
  return tagReply(responses, responseCommand, epc, resultLength);
}

function requireTagSuccess(data) {
  if (data.length !== 1 || data[0] !== 0) throw new Error("Tagkommandoen blev ikke bekræftet som vellykket.");
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
      if (button.dataset.tab === "sniffer" && !snifferRulesLoaded) {
        guarded(refreshSnifferRules);
      }
    });
  });
}

function updateSnifferEndFields() {
  const lengthMode = $("snifferRuleEnd").value === "length";
  $("snifferRuleLength").disabled = !lengthMode;
  $("snifferRuleTerminator").disabled = lengthMode;
}

function resetSnifferRule() {
  selectedSnifferRule = null;
  selectedSnifferVersion = null;
  $("snifferRuleName").value = "";
  $("snifferRuleMarker").value = "^FD";
  $("snifferRuleEnd").value = "terminator";
  $("snifferRuleLength").value = "";
  $("snifferRuleTerminator").value = "^FS";
  $("deleteSnifferRule").disabled = true;
  updateSnifferEndFields();
  renderSnifferRules();
}

function emptyTableRow(body, columns, message) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = columns;
  cell.className = "empty";
  cell.textContent = message;
  row.append(cell);
  body.append(row);
}

function renderSnifferRules() {
  const body = $("snifferRuleRows");
  body.replaceChildren();
  if (!snifferRules.length) {
    emptyTableRow(body, 4, "Ingen gemte regler");
    return;
  }
  for (const rule of snifferRules) {
    const row = document.createElement("tr");
    row.classList.toggle("selected", rule.id === selectedSnifferRule);
    for (const value of [rule.name, rule.marker, rule.length === null ? "Afslutningstekst" : "Antal tegn", rule.length ?? rule.terminator]) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
    row.addEventListener("click", () => {
      selectedSnifferRule = rule.id;
      selectedSnifferVersion = rule.version;
      $("snifferRuleName").value = rule.name;
      $("snifferRuleMarker").value = rule.marker;
      $("snifferRuleEnd").value = rule.length === null ? "terminator" : "length";
      $("snifferRuleLength").value = rule.length === null ? "" : String(rule.length);
      $("snifferRuleTerminator").value = rule.terminator || "";
      $("deleteSnifferRule").disabled = false;
      updateSnifferEndFields();
      renderSnifferRules();
    });
    body.append(row);
  }
}

async function refreshSnifferRules() {
  const data = await api("/api/sniffer/rules");
  snifferRules = data.rules;
  snifferRulesLoaded = true;
  if (selectedSnifferRule !== null && !snifferRules.some((rule) => rule.id === selectedSnifferRule && rule.version === selectedSnifferVersion)) {
    resetSnifferRule();
    showToast("Den valgte regel er ændret eller fjernet; vælg den igen.", true);
  } else renderSnifferRules();
}

function clearSnifferPreview(message = "Ingen prøve udført") {
  snifferPreviewGeneration++;
  $("snifferResultSource").textContent = message;
  const body = $("snifferPreviewRows");
  body.replaceChildren();
  emptyTableRow(body, 3, message);
}

async function saveSnifferRule() {
  const name = $("snifferRuleName").value.trim();
  const marker = $("snifferRuleMarker").value;
  const lengthMode = $("snifferRuleEnd").value === "length";
  const rawLength = $("snifferRuleLength").value;
  const terminator = $("snifferRuleTerminator").value;
  if (!name || !marker) throw new Error("Angiv både navn og søgetekst.");
  if (lengthMode && (!rawLength || !Number.isInteger(Number(rawLength)) || Number(rawLength) < 1 || Number(rawLength) > 100000)) {
    throw new Error("Antal tegn skal være mellem 1 og 100000.");
  }
  if (!lengthMode && !terminator) throw new Error("Angiv en afslutningstekst.");
  const id = selectedSnifferRule;
  const payload = { name, marker, length: lengthMode ? Number(rawLength) : null, terminator: lengthMode ? null : terminator, version: selectedSnifferVersion };
  $("saveSnifferRule").disabled = true;
  try {
    const saved = await api(id === null ? "/api/sniffer/rules" : `/api/sniffer/rules/${id}`, {
      method: id === null ? "POST" : "PUT", body: JSON.stringify(payload),
    });
    selectedSnifferRule = saved.id;
    selectedSnifferVersion = saved.version;
    await refreshSnifferRules();
    $("deleteSnifferRule").disabled = false;
    clearSnifferPreview("Reglerne er ændret; udfør prøven igen.");
    showToast("Reglen er gemt.");
  } finally {
    $("saveSnifferRule").disabled = false;
  }
}

async function previewSnifferRules() {
  const generation = ++snifferPreviewGeneration;
  const data = await api("/api/sniffer/preview", {
    method: "POST", body: JSON.stringify({ text: $("snifferTestData").value }),
  });
  if (generation !== snifferPreviewGeneration) return;
  renderSnifferResults(data.results);
  $("snifferResultSource").textContent = "Manuel prøve af testfeltet.";
}

function renderSnifferResults(results) {
  const body = $("snifferPreviewRows");
  body.replaceChildren();
  if (!results.length) {
    emptyTableRow(body, 3, "Ingen gemte regler");
    return;
  }
  const statuses = { matched: "Fundet", not_found: "Ikke fundet", incomplete: "Ufuldstændig", invalid_utf8: "Ugyldig UTF-8", unreliable: "Usikre printdata" };
  for (const result of results) {
    const row = document.createElement("tr");
    for (const value of [result.name, statuses[result.status] || result.status, result.value === null ? "—" : result.value]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
}

function formatJobTime(seconds) {
  return new Date(seconds * 1000).toLocaleString("da-DK");
}

function replaceSelectOptions(select, placeholder, entries) {
  const selected = select.value;
  select.replaceChildren(new Option(placeholder, ""));
  for (const [value, label] of entries) select.add(new Option(label, value));
  if (entries.some(([value]) => value === selected)) select.value = selected;
}

function renderLinkedEpcs() {
  const job = jobLogJobs.find((entry) => entry.id === $("jobLogSelect").value);
  const links = job ? job.epcs : [];
  replaceSelectOptions($("jobLinkedSelect"), "Vælg kobling", links.map((link) => [
    String(link.observation_id), `${link.epc} · ${formatJobTime(link.observed_at)}`,
  ]));
}

async function refreshJobLog() {
  if (jobLogBusy) return;
  if (authRequired && !localStorage.getItem("rfid-app-token")) {
    $("jobLogStatus").textContent = "Loggen afventer login.";
    return;
  }
  jobLogBusy = true;
  try {
    const data = await api("/api/sniffer/log");
    jobLogJobs = data.jobs;
    replaceSelectOptions($("jobLogSelect"), "Vælg job", data.jobs.map((job) => [
      job.id, `${formatJobTime(job.created_at)} · ${job.id.slice(0, 8)}`,
    ]));
    replaceSelectOptions($("jobObservationSelect"), "Vælg RFID-observation", data.observations.map((observation) => [
      String(observation.id), `${observation.epc} · ${formatJobTime(observation.first_seen)}`,
    ]));
    renderLinkedEpcs();
    const body = $("jobLogRows");
    body.replaceChildren();
    if (!data.jobs.length) emptyTableRow(body, 4, "Ingen gemte job");
    const statuses = { pending: "Afventer afklaring", linked: "Manuelt koblet", no_tag: "Manuelt NoTag" };
    for (const job of data.jobs) {
      const row = document.createElement("tr");
      const values = job.results === null
        ? (job.error || "Udtræk afventer")
        : job.results.map((item) => `${item.name}: ${item.value === null ? item.status : item.value}`).join("; ");
      const status = `${statuses[job.resolution] || job.resolution}${job.incomplete ? " · usikre printdata" : ""}`;
      for (const value of [formatJobTime(job.created_at), values, job.epcs.map((item) => item.epc).join(", "), status]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      body.append(row);
    }
    const readerStates = {
      running: "RFID-læseren har svaret. Inventeringen genstartes hvert 30. sekund; tags kan overses, især under pausen. Svaret garanterer ikke fortsat læsning.",
      starting: "RFID-inventering startet; afventer svar fra læseren. Tags kan overses.",
      restarting: "RFID-inventering genstartes; tags kan overses under pausen.",
      waiting_job: "RFID-opsamling afventer første printjob.",
      bridge_off: "RFID-opsamling afventer aktiv bridge.",
      disconnected: "RFID-læseren er ikke forbundet.",
      busy: "RFID-læseren bruges manuelt; automatisk opsamling er ikke aktiv.",
      stopped: "RFID-opsamling er stoppet.",
      error: `RFID-opsamling fejlede: ${data.rfid.error || "ukendt fejl"}`,
    };
    let status = readerStates[data.rfid.status] || "RFID-status er ukendt.";
    if (data.rfid.dropped) status += ` ${data.rfid.dropped} RFID-læsninger kunne ikke gemmes.`;
    if (data.rfid.storage_error) status += ` ${data.rfid.storage_error}`;
    $("jobRfidStatus").textContent = status;
    $("jobLogStatus").textContent = "Loggen viser seneste job; poster slettes efter 1 døgn.";
  } catch (error) {
    $("jobLogStatus").textContent = `Loggen kan ikke opdateres: ${error.message || String(error)}`;
    $("jobRfidStatus").textContent = "RFID-status kan ikke hentes.";
  } finally {
    jobLogBusy = false;
  }
}

async function exportJobLog() {
  const headers = new Headers();
  const token = authRequired ? localStorage.getItem("rfid-app-token") : null;
  if (token) headers.set("X-API-Token", token);
  const response = await fetch("/api/sniffer/log.csv", { headers });
  if (!response.ok) throw new Error(`CSV-eksport fejlede: HTTP ${response.status}`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = "datasniffer-log.csv";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function bindActions() {
  $("printerAddress").addEventListener("input", () => {
    const changed = $("printerAddress").value.trim() !== savedPrinterAddress;
    $("savePrinterAddress").disabled = !changed;
    if (changed) $("startBridge").disabled = true;
    else refreshBridge();
  });
  $("savePrinterAddress").addEventListener("click", () => guarded(async () => {
    const match = /^([^:]+):([0-9]+)$/.exec($("printerAddress").value.trim());
    if (!match) throw new Error("Angiv printeradressen som IPv4:port.");
    await api("/api/bridge/config", {
      method: "POST",
      body: JSON.stringify({ ip: match[1], port: Number(match[2]) }),
    });
    savedPrinterAddress = null;
    await refreshBridge();
    showToast("Printeradressen er gemt.");
  }));
  $("startBridge").addEventListener("click", () => guarded(async () => {
    if (!window.confirm("Start bridge? Forbindelsen kan afbrydes kortvarigt. Åbn printeradressen inden 2 minutter.")) return;
    await changeBridge("start");
  }));
  $("stopBridge").addEventListener("click", () => guarded(() => changeBridge("stop")));
  $("snifferRuleEnd").addEventListener("change", updateSnifferEndFields);
  $("newSnifferRule").addEventListener("click", resetSnifferRule);
  $("refreshSnifferRules").addEventListener("click", () => guarded(refreshSnifferRules));
  $("saveSnifferRule").addEventListener("click", () => guarded(saveSnifferRule));
  $("deleteSnifferRule").addEventListener("click", () => guarded(async () => {
    const id = selectedSnifferRule;
    if (id === null) throw new Error("Vælg en regel først.");
    if (!window.confirm("Fjern den valgte regel?")) return;
    await api(`/api/sniffer/rules/${id}?version=${selectedSnifferVersion}`, { method: "DELETE" });
    resetSnifferRule();
    await refreshSnifferRules();
    clearSnifferPreview("Reglerne er ændret; udfør prøven igen.");
    showToast("Reglen er fjernet.");
  }));
  $("previewSnifferRules").addEventListener("click", () => guarded(previewSnifferRules));
  $("jobLogSelect").addEventListener("change", renderLinkedEpcs);
  $("refreshJobLog").addEventListener("click", refreshJobLog);
  $("exportJobLog").addEventListener("click", () => guarded(exportJobLog));
  $("linkJobEpc").addEventListener("click", () => guarded(async () => {
    const job = $("jobLogSelect").value;
    const observation = $("jobObservationSelect").value;
    if (!job || !observation) throw new Error("Vælg både job og RFID-observation.");
    if (!window.confirm("Bekræft den manuelle kobling. Tidspunkt alene beviser ikke, at EPC'en tilhører jobbet.")) return;
    await api(`/api/sniffer/log/${job}/epcs`, { method: "POST", body: JSON.stringify({ observation_id: Number(observation) }) });
    await refreshJobLog();
  }));
  $("unlinkJobEpc").addEventListener("click", () => guarded(async () => {
    const job = $("jobLogSelect").value;
    const observation = $("jobLinkedSelect").value;
    if (!job || !observation) throw new Error("Vælg job og kobling.");
    if (!window.confirm("Fjern den valgte EPC-kobling?")) return;
    await api(`/api/sniffer/log/${job}/epcs/${observation}`, { method: "DELETE" });
    await refreshJobLog();
  }));
  $("markJobNoTag").addEventListener("click", () => guarded(async () => {
    const job = $("jobLogSelect").value;
    if (!job) throw new Error("Vælg et job.");
    if (!window.confirm("Markér dette job som manuelt afklaret uden tag?")) return;
    await api(`/api/sniffer/log/${job}/no-tag`, { method: "POST", body: "{}" });
    await refreshJobLog();
  }));
  $("clearJobNoTag").addEventListener("click", () => guarded(async () => {
    const job = $("jobLogSelect").value;
    if (!job) throw new Error("Vælg et job.");
    await api(`/api/sniffer/log/${job}/no-tag`, { method: "DELETE" });
    await refreshJobLog();
  }));
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
    const frames = (result.responses || []).map(responsePayload).filter(Boolean);
    const error = frames.find((frame) => frame.type === 0x01 && frame.command === 0xFF);
    if (error) {
      if (error.payload.length === 1 && error.payload[0] === 0x15) {
        throw new Error("Enkeltlæsning: intet gyldigt tag i et svar, eller CRC-fejl. Tidligere læste tags bliver i tabellen.");
      }
      throw new Error(`Enkeltlæsning fejlede: ${bytesToHex(error.payload) || "ukendt fejl"}.`);
    }
    if (!frames.some((frame) => frame.type === 0x02 && frame.command === 0x22 && frame.payload.length >= 5)) {
      throw new Error("Enkeltlæsning: intet gyldigt svar fra læseren.");
    }
    showToast("Enkeltlæsning: tagsvar modtaget.");
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
    selectFromResponse(await sendFrame(0x0B));
    showToast("Select-parametre hentet.");
  }));
  $("setSelectMode").addEventListener("click", () => guarded(async () => {
    requireConfigEnabled();
    const mode = numberValue("selectMode", "Select-tilstand", 0, 2);
    requireConfirmation(await sendFrame(0x12, [mode]), 0x12, "Select-tilstand");
  }));
  $("readMemory").addEventListener("click", () => guarded(readMemory));
  $("writeMemory").addEventListener("click", () => guarded(writeMemory));
  $("lockTag").addEventListener("click", () => guarded(async () => {
    if (!selectedEpc) throw new Error("Vælg et tag i EPC-tabellen først.");
    const payload = lockPayload();
    if (payload.slice(4).every((byte) => byte === 0)) throw new Error("Vælg mindst én låsehandling.");
    if (!window.confirm(`Send Lock-kommandoen til ${selectedEpc}? Permanente handlinger kan ikke fortrydes.`)) return;
    requireTagSuccess(await sendSelectedTagFrame(0x82, payload, 0x82, 1));
    $("memoryStatus").textContent = "Lock bekræftet af tagget.";
  }));
  $("killTag").addEventListener("click", () => guarded(async () => {
    if ($("killConfirm").value !== "KILL") throw new Error("Skriv KILL for at bekræfte permanent deaktivering.");
    if (!window.confirm(`Deaktivér tag ${selectedEpc || "(intet valgt)"} permanent?`)) return;
    const password = passwordBytes($("killPassword").value, "Kill password");
    requireTagSuccess(await sendSelectedTagFrame(0x65, password, 0x65, 1));
    $("memoryStatus").textContent = "Kill bekræftet af tagget.";
  }));
  $("blockPermalock").addEventListener("click", () => guarded(async () => {
    const payload = blockPermalockPayload();
    const operation = payload[4];
    const range = numberValue("blockRange", "Block-range", 1, 255);
    if (operation === 1 && !window.confirm(`Lås brugerblokke permanent på ${selectedEpc || "(intet valgt)"}?`)) return;
    const result = await sendSelectedTagFrame(0xD3, payload, operation === 1 ? 0xD4 : 0xD3, operation === 1 ? 1 : 1 + range * 2);
    if (operation === 1) {
      requireTagSuccess(result);
      showToast("BlockPermalock bekræftet af tagget.");
    } else {
      if (result[0] !== range) throw new Error("BlockPermalock-svarets range matcher ikke forespørgslen.");
      showToast(`Block-låsestatus: ${bytesToHex(result.slice(1))}`);
    }
  }));
  $("qtCommand").addEventListener("click", () => guarded(async () => {
    const payload = qtPayload();
    const action = payload[4];
    if (action === 1 && !window.confirm(`Ændr QT-indstillinger på ${selectedEpc || "(intet valgt)"}?`)) return;
    const result = await sendSelectedTagFrame(0xE5, payload, action === 1 ? 0xE6 : 0xE5, action === 1 ? 1 : 2);
    if (action === 1) {
      requireTagSuccess(result);
      showToast("QT Write bekræftet af tagget.");
    } else {
      showToast(`QT control word: ${bytesToHex(result)}`);
    }
  }));
  $("nxpProtect").addEventListener("click", () => guarded(async () => {
    if (!window.confirm(`Aktivér ReadProtect på ${selectedEpc || "(intet valgt)"}?`)) return;
    requireTagSuccess(await sendSelectedTagFrame(0xE1, [...passwordBytes($("accessPassword").value, "Access password"), 0x00], 0xE1, 1));
    showToast("ReadProtect bekræftet af tagget.");
  }));
  $("nxpReset").addEventListener("click", () => guarded(async () => {
    if (!window.confirm(`Nulstil ReadProtect på ${selectedEpc || "(intet valgt)"}?`)) return;
    requireTagSuccess(await sendSelectedTagFrame(0xE1, [...passwordBytes($("accessPassword").value, "Access password"), 0x01], 0xE2, 1));
    showToast("Reset ReadProtect bekræftet af tagget.");
  }));
  $("nxpEas").addEventListener("click", () => guarded(async () => {
    if (!window.confirm("Ændr EAS-status på det valgte NXP-tag?")) return;
    const easState = hexByte($("nxpEasState").value, "EAS-status");
    if (easState !== 0 && easState !== 1) throw new Error("Ugyldig EAS-status.");
    const payload = [...passwordBytes($("accessPassword").value, "Access password"), easState];
    requireTagSuccess(await sendSelectedTagFrame(0xE3, payload, 0xE3, 1));
    showToast("Change EAS bekræftet af tagget.");
  }));
  $("nxpAlarm").addEventListener("click", () => guarded(async () => {
    await ensureTagSelected();
    const responses = await sendFrame(0xE4, [], 1.5);
    const error = firstPayload(responses, 0xFF);
    if (error) throw new Error(`EAS Alarm blev afvist: ${bytesToHex(error)}.`);
    const result = firstPayload(responses, 0xE4);
    if (!result || result.length !== 8) throw new Error("Intet gyldigt EAS Alarm-svar modtaget.");
    showToast(`EAS Alarm: ${bytesToHex(result)}`);
  }));
  $("nxpConfig").addEventListener("click", () => guarded(async () => {
    const configWord = [...bytesFromHex($("configWord").value, "Config-word")];
    if (configWord.length !== 2) throw new Error("Config-word skal være to bytes.");
    if (configWord.some((byte) => byte !== 0) && !window.confirm(`Invertér de valgte config-bits på ${selectedEpc || "(intet valgt)"}?`)) return;
    const result = await sendSelectedTagFrame(0xE0, [...passwordBytes($("accessPassword").value, "Access password"), ...configWord], 0xE0, 2);
    showToast(`NXP config-word: ${bytesToHex(result)}`);
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
  guarded(async () => {
    const token = $("tokenInput").value.trim();
    if (!token) return;
    localStorage.setItem("rfid-app-token", token);
    sessionStorage.removeItem("rfid-app-token");
    $("tokenInput").value = "";
    tokenDialog.close("ok");
    await guarded(confirmBridgeIfNeeded);
    await refreshBridge();
    await refreshWanIp();
    await refreshPorts();
    await refreshState();
  });
});

$("forgetToken").addEventListener("click", () => guarded(async () => {
  localStorage.removeItem("rfid-app-token");
  sessionStorage.removeItem("rfid-app-token");
  showTokenDialog();
}));

bindTabs();
bindActions();

(async function initialize() {
  try {
    const config = await api("/api/config");
    authRequired = config.auth_required;
    $("forgetToken").hidden = !authRequired;
    $("noAuthWarning").hidden = authRequired;
    refreshTimer = setInterval(() => {
      if (!authRequired || localStorage.getItem("rfid-app-token")) guarded(refreshState);
    }, 1200);
    setInterval(() => {
      if (!authRequired || localStorage.getItem("rfid-app-token")) refreshWanIp();
      else $("wanIp").textContent = "WAN IP: Afventer login";
    }, 10000);
    if (authRequired && !localStorage.getItem("rfid-app-token")) showTokenDialog();
    if (!authRequired || localStorage.getItem("rfid-app-token")) await guarded(confirmBridgeIfNeeded);
    await refreshBridge();
    await refreshCapture();
    await refreshJobLog();
    setInterval(refreshBridge, 5000);
    setInterval(refreshCapture, 1500);
    setInterval(refreshJobLog, 3000);
    drawChart("jammerChart", chartValues.jammerChart);
    drawChart("rssiChart", chartValues.rssiChart);
    if (!authRequired || localStorage.getItem("rfid-app-token")) await refreshWanIp();
    await refreshPorts();
    await refreshState();
  } catch (error) {
    showToast(error.message || String(error), true);
  }
})();

window.addEventListener("resize", () => {
  drawChart("jammerChart", chartValues.jammerChart);
  drawChart("rssiChart", chartValues.rssiChart);
});
