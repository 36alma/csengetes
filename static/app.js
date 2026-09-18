const API = "";

(function stripTokenFromUrl() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("token")) {
    params.delete("token");
    const query = params.toString();
    const newUrl = window.location.pathname + (query ? "?" + query : "");
    window.history.replaceState(null, "", newUrl);
  }
})();

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let body = null;
  try {
    body = await res.json();
  } catch (e) {
    body = null;
  }
  return body;
}

function get(path) {
  return api(path);
}
function post(path, data) {
  return api(path, { method: "POST", body: JSON.stringify(data ?? {}) });
}

// ---------- fulek ----------

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("panel-" + tab.dataset.tab).classList.add("active");
  });
});

// ---------- alert popup ----------

let alertTimer = null;

function showAlert(title, detail, success) {
  const overlay = document.getElementById("alertOverlay");
  const titleEl = document.getElementById("alertTitle");
  const detailEl = document.getElementById("alertDetail");
  titleEl.textContent = title;
  titleEl.style.color = success ? "var(--success-text)" : "var(--danger-text)";
  detailEl.textContent = detail;
  overlay.classList.add("visible");

  if (alertTimer) clearTimeout(alertTimer);
  alertTimer = setTimeout(() => overlay.classList.remove("visible"), 10000);
}

document.getElementById("alertOk").addEventListener("click", () => {
  document.getElementById("alertOverlay").classList.remove("visible");
  if (alertTimer) clearTimeout(alertTimer);
});

async function playAndAlert(filename, force) {
  const result = await post("/api/media/play", { filename, force });
  if (result && result.ok) {
    showAlert("Lejatszas sikeres", filename, true);
  } else {
    showAlert("Hiba a lejatszasban", `${filename}\n${result ? result.error : "Ismeretlen hiba"}`, false);
  }
}

// ---------- Csengetesi rend ful ----------

let currentScheduleData = null;
let editingOriginal = null;
let selectedRowKeys = new Set();
let selectionAnchorIndex = null;
let defaultMediaFile = "";

function eventKey(ev) {
  return ev.time + "||" + ev.file;
}

async function loadSchedulesList() {
  const data = await get("/api/schedules");
  const select = document.getElementById("scheduleSelect");
  select.innerHTML = "";
  data.schedules.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });
  if (data.active && data.schedules.includes(data.active)) {
    select.value = data.active;
    await loadScheduleEvents(data.active);
  } else if (data.schedules.length) {
    select.value = data.schedules[0];
  }
}

async function loadScheduleEvents(name) {
  const data = await get("/api/schedules/" + encodeURIComponent(name));
  if (data.error) {
    alert(data.error);
    return;
  }
  currentScheduleData = data;
  await post("/api/schedules/" + encodeURIComponent(name) + "/activate", {});
  renderEventsTable();
}

function sortedEvents() {
  return [...(currentScheduleData ? currentScheduleData.events : [])].sort((a, b) =>
    a.time.localeCompare(b.time)
  );
}

function renderEventsTable() {
  const tbody = document.getElementById("eventsTbody");
  tbody.innerHTML = "";
  if (!currentScheduleData) return;
  const events = sortedEvents();
  events.forEach((ev, i) => {
    const tr = document.createElement("tr");
    const key = eventKey(ev);
    if (selectedRowKeys.has(key)) tr.classList.add("selected");
    else if (i % 2 === 1) tr.classList.add("alt");
    const tdTime = document.createElement("td");
    tdTime.textContent = ev.time;
    const tdFile = document.createElement("td");
    tdFile.textContent = ev.file;
    tr.appendChild(tdTime);
    tr.appendChild(tdFile);
    tr.addEventListener("click", (e) => handleRowClick(e, i, key));
    tr.addEventListener("dblclick", () => startEditEvent(ev));
    tbody.appendChild(tr);
  });
}

function handleRowClick(e, index, key) {
  const events = sortedEvents();
  if (e.shiftKey && selectionAnchorIndex !== null) {
    const [start, end] = [selectionAnchorIndex, index].sort((a, b) => a - b);
    selectedRowKeys = new Set(events.slice(start, end + 1).map(eventKey));
  } else if (e.ctrlKey || e.metaKey) {
    if (selectedRowKeys.has(key)) selectedRowKeys.delete(key);
    else selectedRowKeys.add(key);
    selectionAnchorIndex = index;
  } else {
    selectedRowKeys = new Set([key]);
    selectionAnchorIndex = index;
  }
  renderEventsTable();
}

function startEditEvent(ev) {
  editingOriginal = { ...ev };
  document.getElementById("eventTime").value = ev.time;
  document.getElementById("eventFile").value = ev.file;
  document.getElementById("btnAddEvent").textContent = "Modositas mentese";
  document.getElementById("btnCancelEdit").style.display = "inline-flex";
  document.getElementById("editHint").textContent = `Szerkesztes: ${ev.time} → ${ev.file}`;
}

function cancelEdit() {
  editingOriginal = null;
  document.getElementById("eventTime").value = "";
  document.getElementById("eventFile").value = defaultMediaFile;
  document.getElementById("btnAddEvent").textContent = "Esemeny hozzaadas";
  document.getElementById("btnCancelEdit").style.display = "none";
  document.getElementById("editHint").textContent = "";
}

document.getElementById("btnCancelEdit").addEventListener("click", cancelEdit);

document.getElementById("btnAddEvent").addEventListener("click", () => {
  if (!currentScheduleData) {
    alert("Eloszor valassz ki egy rendet.");
    return;
  }
  const time = document.getElementById("eventTime").value.trim();
  const file = document.getElementById("eventFile").value.trim();
  if (!time || !file) {
    alert("Add meg az idopontot es a fajlt.");
    return;
  }
  if (!/^([01]\d|2[0-3]):([0-5]\d)$/.test(time)) {
    alert("Ervenytelen idopont formatum (HH:MM varva).");
    return;
  }

  if (editingOriginal) {
    const idx = currentScheduleData.events.findIndex(
      (e) => e.time === editingOriginal.time && e.file === editingOriginal.file
    );
    if (idx !== -1) currentScheduleData.events[idx] = { time, file };
  } else {
    currentScheduleData.events.push({ time, file });
  }
  selectedRowKeys = new Set([eventKey({ time, file })]);
  cancelEdit();
  renderEventsTable();
});

document.getElementById("btnRemoveEvent").addEventListener("click", () => {
  if (!currentScheduleData || selectedRowKeys.size === 0) return;
  const count = selectedRowKeys.size;
  if (count > 1 && !confirm(`Biztosan torlod a kijelolt ${count} esemenyt?`)) return;
  currentScheduleData.events = currentScheduleData.events.filter((e) => !selectedRowKeys.has(eventKey(e)));
  selectedRowKeys = new Set();
  selectionAnchorIndex = null;
  renderEventsTable();
});

document.getElementById("btnRemoveAllEvents").addEventListener("click", () => {
  if (!currentScheduleData || currentScheduleData.events.length === 0) return;
  if (!confirm(`Biztosan torlod MIND a(z) ${currentScheduleData.events.length} esemenyt ebbol a rendbol?`)) return;
  currentScheduleData.events = [];
  selectedRowKeys = new Set();
  selectionAnchorIndex = null;
  renderEventsTable();
});

document.getElementById("btnSaveSchedule").addEventListener("click", async () => {
  if (!currentScheduleData) return;
  const name = document.getElementById("scheduleSelect").value;
  const result = await post("/api/schedules/" + encodeURIComponent(name), currentScheduleData);
  if (result && result.error) {
    alert(result.error);
    return;
  }
  alert(`${name} elmentve.`);
});

document.getElementById("btnRingNow").addEventListener("click", () => {
  const filename = document.getElementById("eventFile").value.trim() || defaultMediaFile;
  if (!filename) {
    alert("Nincs kivalasztott vagy alapertelmezett hangfajl.");
    return;
  }
  playAndAlert(filename, true);
});

document.getElementById("scheduleSelect").addEventListener("change", (e) => {
  selectedRowKeys = new Set();
  selectionAnchorIndex = null;
  loadScheduleEvents(e.target.value);
});

document.getElementById("btnRefreshSchedules").addEventListener("click", loadSchedulesList);

document.getElementById("btnNewSchedule").addEventListener("click", async () => {
  const name = prompt("Fajlnev (pl. rovid.json):");
  if (!name) return;
  const result = await post("/api/schedules", { name });
  if (result && result.error) {
    alert(result.error);
    return;
  }
  await loadSchedulesList();
  document.getElementById("scheduleSelect").value = result.filename;
  await loadScheduleEvents(result.filename);
});

// ---------- Media ful ----------

async function loadMediaList() {
  const data = await get("/api/media");
  defaultMediaFile = data.default || "";

  const listbox = document.getElementById("mediaListbox");
  listbox.innerHTML = "";
  data.files.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f;
    opt.textContent = f;
    listbox.appendChild(opt);
  });

  const eventFile = document.getElementById("eventFile");
  const prevValue = eventFile.value;
  eventFile.innerHTML = "";
  data.files.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f;
    opt.textContent = f;
    eventFile.appendChild(opt);
  });
  if (prevValue && data.files.includes(prevValue)) eventFile.value = prevValue;
  else if (defaultMediaFile && data.files.includes(defaultMediaFile)) eventFile.value = defaultMediaFile;

  const defaultSelect = document.getElementById("defaultMediaSelect");
  defaultSelect.innerHTML = "";
  data.files.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f;
    opt.textContent = f;
    defaultSelect.appendChild(opt);
  });
  if (defaultMediaFile && data.files.includes(defaultMediaFile)) defaultSelect.value = defaultMediaFile;
}

document.getElementById("btnRefreshMedia").addEventListener("click", loadMediaList);

document.getElementById("btnUploadMedia").addEventListener("click", () => {
  document.getElementById("fileInput").click();
});

document.getElementById("fileInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch("/api/media/upload", { method: "POST", body: formData });
  const result = await res.json();
  if (result.error) {
    alert(result.error);
  } else {
    await loadMediaList();
  }
  e.target.value = "";
});

document.getElementById("btnPlaySelected").addEventListener("click", () => {
  const listbox = document.getElementById("mediaListbox");
  if (!listbox.value) {
    alert("Valassz ki egy fajlt a listabol.");
    return;
  }
  const force = document.getElementById("forcePlay").checked;
  playAndAlert(listbox.value, force);
});

document.getElementById("btnSaveDefaultMedia").addEventListener("click", async () => {
  const filename = document.getElementById("defaultMediaSelect").value;
  if (!filename) {
    alert("Valassz ki egy fajlt az alapertelmezetthez.");
    return;
  }
  const result = await post("/api/media/default", { filename });
  if (result && result.error) {
    alert(result.error);
    return;
  }
  defaultMediaFile = filename;
  const eventFile = document.getElementById("eventFile");
  if (!eventFile.value) eventFile.value = filename;
  alert(`Alapertelmezett csengohang: ${filename}`);
});

async function loadUploadLimit() {
  const data = await get("/api/media/upload-limit");
  document.getElementById("uploadLimitInput").value = data.max_upload_mb;
}

document.getElementById("btnSaveUploadLimit").addEventListener("click", async () => {
  const mb = Number(document.getElementById("uploadLimitInput").value);
  const result = await post("/api/media/upload-limit", { max_upload_mb: mb });
  if (result && result.error) {
    alert(result.error);
    return;
  }
  alert(`Max. feltoltheto fajlmeret: ${result.max_upload_mb} MB`);
});

// ---------- Ido ful ----------

async function loadTimeState() {
  const data = await get("/api/time");
  document.getElementById("modeNtp").checked = data.mode === "ntp";
  document.getElementById("modeManual").checked = data.mode === "manual";
  document.getElementById("ntpServer").value = data.ntp_server;
  document.getElementById("currentTime").textContent = "Aktualis szamitott ido: " + data.now;
}

document.querySelectorAll('input[name="timeMode"]').forEach((radio) => {
  radio.addEventListener("change", async (e) => {
    await post("/api/time/mode", { mode: e.target.value });
  });
});

document.getElementById("btnSyncNtp").addEventListener("click", async () => {
  const server = document.getElementById("ntpServer").value.trim();
  const result = await post("/api/time/sync", { ntp_server: server });
  if (!result || !result.ok) {
    alert("NTP hiba: " + (result ? result.error : "ismeretlen hiba") + "\nRendszerora kerul hasznalatra.");
  }
});

document.getElementById("btnApplyManual").addEventListener("click", async () => {
  const value = document.getElementById("manualTime").value.trim();
  const result = await post("/api/time/manual", { value });
  if (result && result.error) {
    alert(result.error);
    return;
  }
  document.getElementById("modeManual").checked = true;
});

setInterval(() => {
  get("/api/time").then((data) => {
    document.getElementById("currentTime").textContent = "Aktualis szamitott ido: " + data.now;
  });
}, 1000);

// ---------- Kimenet ful ----------

let deviceMap = {};

async function loadOutputState() {
  const data = await get("/api/output/devices");
  const select = document.getElementById("deviceSelect");
  select.innerHTML = "";
  deviceMap = {};

  const defaultOpt = document.createElement("option");
  defaultOpt.value = "default";
  defaultOpt.textContent = "Rendszer alapertelmezett";
  select.appendChild(defaultOpt);
  deviceMap["default"] = null;

  data.devices.forEach((d) => {
    const key = "dev-" + d.index;
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = `${d.name} (#${d.index})`;
    select.appendChild(opt);
    deviceMap[key] = d.index;
  });

  let selectedKey = "default";
  for (const [key, idx] of Object.entries(deviceMap)) {
    if (idx === data.current) {
      selectedKey = key;
      break;
    }
  }
  select.value = selectedKey;

  const slider = document.getElementById("volumeSlider");
  slider.max = data.max_volume;
  slider.value = data.volume;
  updateVolumeLabel(data.volume);
}

document.getElementById("deviceSelect").addEventListener("change", async (e) => {
  const idx = deviceMap[e.target.value];
  await post("/api/output/device", { index: idx });
});

function updateVolumeLabel(value) {
  const rounded = Math.round(value);
  const suffix = rounded > 100 ? " (Boost)" : "";
  document.getElementById("volumeLabel").textContent = `Hangero: ${rounded}%${suffix}`;
}

let volumeDebounce = null;
document.getElementById("volumeSlider").addEventListener("input", (e) => {
  const value = Number(e.target.value);
  updateVolumeLabel(value);
  if (volumeDebounce) clearTimeout(volumeDebounce);
  volumeDebounce = setTimeout(() => post("/api/output/volume", { percent: value }), 150);
});

// ---------- Naplo ful ----------

async function loadLogs() {
  const data = await get("/api/logs?limit=300");
  const box = document.getElementById("logBox");
  const wasAtBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
  box.textContent = (data.lines || []).join("\n");
  if (wasAtBottom) box.scrollTop = box.scrollHeight;
}

setInterval(loadLogs, 2000);

// ---------- inicializalas ----------

async function init() {
  await loadMediaList();
  await loadSchedulesList();
  await loadTimeState();
  await loadOutputState();
  await loadUploadLimit();
  await loadLogs();
  cancelEdit();
}

init();
