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

document.querySelectorAll(".nav-item").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((t) => {
      t.classList.remove("active");
      t.removeAttribute("aria-current");
    });
    document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    tab.setAttribute("aria-current", "page");
    document.getElementById("panel-" + tab.dataset.tab).classList.add("active");
  });
});

function isPageActive(name) {
  return document.getElementById("panel-" + name).classList.contains("active");
}

// ---------- statuszsav ----------

function setStatus(id, tone, text) {
  const el = document.getElementById(id);
  el.dataset.tone = tone;
  el.textContent = text;
}

let serverReachable = null;

function setConnection(ok, text) {
  if (serverReachable === ok) return;
  serverReachable = ok;
  setStatus("sbConnection", ok ? "ok" : "warn", text);
}

function renderDeviceSummary(devices) {
  if (!Array.isArray(devices)) return;
  const connected = devices.filter((d) => d.status === "connected").length;
  if (devices.length === 0) setStatus("sbDevices", "off", "Hangszórók: nincs felvett eszköz");
  else setStatus("sbDevices", connected ? "ok" : "off", `Hangszórók: ${connected}/${devices.length} csatlakozva`);
}

let outputDeviceName = "–";

function renderOutputSummary(mixer) {
  let tone = "off";
  let suffix = "";
  if (mixer && mixer.running && mixer.output_ok === false) {
    tone = "warn";
    suffix = " (nem érhető el)";
  } else if (mixer && mixer.running) {
    tone = "ok";
  }
  setStatus("sbOutput", tone, `Kimenet: ${outputDeviceName}${suffix}`);
  const masterName = document.getElementById("masterOutputName");
  masterName.textContent = outputDeviceName;
  masterName.title = outputDeviceName;
}

// ---------- felso sav: ora es kovetkezo csengetes ----------

let activeSchedule = null; // { name, events } - az utemezo altal hasznalt, mentett rend

async function loadActiveSchedule() {
  const list = await get("/api/schedules");
  const name = list && list.active;
  if (!name) {
    activeSchedule = null;
    return;
  }
  const data = await get("/api/schedules/" + encodeURIComponent(name));
  activeSchedule = data && !data.error ? { name, events: data.events || [] } : null;
}

function parseClock(text) {
  const m = /^\d{2}:\d{2}(?::\d{2})?$/.test(text) ? text.split(":").map(Number) : null;
  return m ? m[0] * 3600 + m[1] * 60 + (m[2] || 0) : null;
}

function nextBellFrom(nowSeconds, events) {
  const times = [...new Set(events.map((e) => e.time))].map(parseClock).filter((s) => s !== null).sort((a, b) => a - b);
  if (!times.length) return null;
  const later = times.find((s) => s > nowSeconds);
  if (later !== undefined) return { at: later, in: later - nowSeconds, tomorrow: false };
  return { at: times[0], in: 86400 - nowSeconds + times[0], tomorrow: true };
}

const pad2 = (n) => String(n).padStart(2, "0");

function formatHM(seconds) {
  return pad2(Math.floor(seconds / 3600) % 24) + ":" + pad2(Math.floor(seconds / 60) % 60);
}

function formatCountdown(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor(seconds / 60) % 60;
  const s = seconds % 60;
  return (h ? h + ":" + pad2(m) : String(m)) + ":" + pad2(s);
}

// Mixer / Csengo sav: kovetkezo csengetes es a rend mai esemenyei
let bellListKey = null;

function renderBellStrip(nowSeconds) {
  const events = activeSchedule
    ? activeSchedule.events.filter((e) => parseClock(e.time) !== null).sort((a, b) => a.time.localeCompare(b.time))
    : [];
  const next = activeSchedule ? nextBellFrom(nowSeconds, events) : null;
  document.getElementById("bellScheduleName").textContent = activeSchedule ? activeSchedule.name : "Nincs aktív rend";
  document.getElementById("bellNextTime").textContent = next ? (next.tomorrow ? "holnap " : "") + formatHM(next.at) : "–";
  document.getElementById("bellNextIn").textContent = next
    ? formatCountdown(next.in) + " múlva"
    : activeSchedule ? "Nincs csengetés a rendben" : "Válassz rendet az Ütemezés oldalon";

  const key = [activeSchedule && activeSchedule.name, events.map((e) => e.time + e.file).join(","), next && next.at, next && next.tomorrow].join("|");
  if (key === bellListKey) return;
  bellListKey = key;
  const list = document.getElementById("bellList");
  list.innerHTML = "";
  let nextItem = null;
  if (activeSchedule && !events.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "Ebben a rendben még nincs csengetés.";
    list.appendChild(li);
  }
  for (const ev of events) {
    const at = parseClock(ev.time);
    const li = document.createElement("li");
    if (next && at === next.at) {
      li.className = "next";
      nextItem = nextItem || li;
    } else if (next && (next.tomorrow || at < next.at)) {
      li.className = "past";
    }
    const time = document.createElement("span");
    time.textContent = ev.time;
    const file = document.createElement("span");
    file.className = "file";
    file.textContent = ev.file;
    file.title = ev.file;
    li.append(time, file);
    list.appendChild(li);
  }
  if (nextItem) list.scrollTop = Math.max(0, nextItem.offsetTop - list.offsetTop - 32);
}

function renderClock(data) {
  const [date, time] = String(data.now || "").split(" ");
  if (!time) return;
  document.getElementById("clockTime").textContent = time;
  const [y, mo, d] = date.split("-").map(Number);
  const weekday = new Date(y, mo - 1, d).toLocaleDateString("hu-HU", { weekday: "long" });
  document.getElementById("clockDate").textContent = `${y}. ${pad2(mo)}. ${pad2(d)}., ${weekday}`;
  document.getElementById("clockSource").textContent = data.mode === "ntp" ? "NTP-idő" : "Kézi idő";

  const nowSeconds = parseClock(time);
  renderBellStrip(nowSeconds);

  const timeEl = document.getElementById("nextBellTime");
  const countdownEl = document.getElementById("nextBellCountdown");
  if (!activeSchedule) {
    timeEl.textContent = "Nincs aktív rend";
    countdownEl.textContent = "";
    return;
  }
  const next = nextBellFrom(nowSeconds, activeSchedule.events);
  if (!next) {
    timeEl.textContent = "Nincs esemény";
    countdownEl.textContent = "";
    return;
  }
  timeEl.textContent = (next.tomorrow ? "holnap " : "") + formatHM(next.at);
  countdownEl.textContent = formatCountdown(next.in) + " múlva";
}

// ---------- csuszkak kitoltese ----------
// A csuszka savjanak kitoltott resze a --p (0..1) CSS valtozobol rajzolodik.

function setSliderFill(slider) {
  const min = Number(slider.min) || 0;
  const max = Number(slider.max) || 100;
  const p = max > min ? (Number(slider.value) - min) / (max - min) : 0;
  slider.style.setProperty("--p", Math.min(1, Math.max(0, p)).toFixed(4));
}

document.addEventListener("input", (e) => {
  const cl = e.target && e.target.classList;
  if (cl && (cl.contains("slider") || cl.contains("fader"))) setSliderFill(e.target);
});

// ---------- alert popup ----------

let alertTimer = null;

function showAlert(title, detail, success) {
  const overlay = document.getElementById("alertOverlay");
  const titleEl = document.getElementById("alertTitle");
  const detailEl = document.getElementById("alertDetail");
  titleEl.textContent = title;
  titleEl.style.color = success ? "var(--ok)" : "var(--warn)";
  detailEl.textContent = detail;
  overlay.classList.add("visible");

  if (alertTimer) clearTimeout(alertTimer);
  alertTimer = setTimeout(() => overlay.classList.remove("visible"), 10000);
}

document.getElementById("alertOk").addEventListener("click", () => {
  document.getElementById("alertOverlay").classList.remove("visible");
  if (alertTimer) clearTimeout(alertTimer);
});

// ---------- modal (alert/confirm/prompt csere) ----------

const modal = (() => {
  const overlay = document.getElementById("modalOverlay");
  const titleEl = document.getElementById("modalTitle");
  const messageEl = document.getElementById("modalMessage");
  const inputEl = document.getElementById("modalInput");
  const okBtn = document.getElementById("modalOk");
  const cancelBtn = document.getElementById("modalCancel");
  let queue = Promise.resolve();

  function open({ title, message, input, okText, cancelText, danger, cancelable }) {
    return new Promise((resolve) => {
      titleEl.textContent = title;
      titleEl.style.color = danger ? "var(--warn)" : "";
      messageEl.textContent = message || "";
      inputEl.style.display = input ? "" : "none";
      inputEl.value = "";
      inputEl.placeholder = input && input.placeholder ? input.placeholder : "";
      okBtn.textContent = okText;
      okBtn.className = "btn " + (danger ? "btn-danger-solid" : "btn-primary");
      cancelBtn.textContent = cancelText || "Mégse";
      cancelBtn.style.display = cancelable ? "" : "none";
      overlay.classList.add("visible");
      (input ? inputEl : okBtn).focus();

      function close(value) {
        overlay.classList.remove("visible");
        okBtn.removeEventListener("click", onOk);
        cancelBtn.removeEventListener("click", onCancel);
        overlay.removeEventListener("mousedown", onBackdrop);
        document.removeEventListener("keydown", onKey, true);
        resolve(value);
      }
      const onOk = () => close(input ? inputEl.value : true);
      const onCancel = () => close(input ? null : false);
      const onBackdrop = (e) => { if (e.target === overlay && cancelable) onCancel(); };
      const onKey = (e) => {
        if (e.key === "Escape") { e.preventDefault(); (cancelable ? onCancel : onOk)(); }
        else if (e.key === "Enter") { e.preventDefault(); onOk(); }
      };
      okBtn.addEventListener("click", onOk);
      cancelBtn.addEventListener("click", onCancel);
      overlay.addEventListener("mousedown", onBackdrop);
      document.addEventListener("keydown", onKey, true);
    });
  }

  // egyszerre csak egy modal latszik, a tobbi sorba all
  function enqueue(opts) {
    const p = queue.then(() => open(opts));
    queue = p.catch(() => {});
    return p;
  }

  return {
    alert: (message, { title = "Értesítés", danger = false } = {}) =>
      enqueue({ title, message, okText: "OK", danger, cancelable: false }),
    confirm: (message, { title = "Megerősítés", okText = "Igen", danger = false } = {}) =>
      enqueue({ title, message, okText, danger, cancelable: true }),
    prompt: (message, { title = "Adatbevitel", placeholder = "" } = {}) =>
      enqueue({ title, message, input: { placeholder }, okText: "OK", cancelable: true }),
  };
})();

const showError = (message) => modal.alert(message, { title: "Hiba", danger: true });

async function playAndAlert(filename, force) {
  const result = await post("/api/media/play", { filename, force });
  if (result && result.ok) {
    showAlert("Lejátszás sikeres", filename, true);
  } else {
    showAlert("Hiba a lejátszásban", `${filename}\n${result ? result.error : "Ismeretlen hiba"}`, false);
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
  } else {
    if (data.schedules.length) select.value = data.schedules[0];
    renderEventsTable();
  }
}

async function loadScheduleEvents(name) {
  const data = await get("/api/schedules/" + encodeURIComponent(name));
  if (data.error) {
    await showError(data.error);
    return;
  }
  currentScheduleData = data;
  await post("/api/schedules/" + encodeURIComponent(name) + "/activate", {});
  loadActiveSchedule();
  renderEventsTable({ resetScroll: true });
}

function sortedEvents() {
  return [...(currentScheduleData ? currentScheduleData.events : [])].sort((a, b) =>
    a.time.localeCompare(b.time)
  );
}

// Ures tablazat: egyetlen, a teljes szelessegu sor magyarazo szoveggel
function emptyRow(tbody, colspan, text) {
  const tr = document.createElement("tr");
  tr.className = "empty-row";
  const td = document.createElement("td");
  td.colSpan = colspan;
  td.textContent = text;
  tr.appendChild(td);
  tbody.appendChild(tr);
}

function renderEventsTable({ resetScroll = false } = {}) {
  const tbody = document.getElementById("eventsTbody");
  const scroller = tbody.closest(".table-wrap");
  const scrollTop = resetScroll ? 0 : scroller.scrollTop;
  tbody.innerHTML = "";
  const count = document.getElementById("eventCount");
  if (!currentScheduleData) {
    count.textContent = "";
    emptyRow(tbody, 2, "Nincs kiválasztott rend. Válassz egyet fent, vagy hozz létre újat.");
    return;
  }
  const events = sortedEvents();
  count.textContent = events.length + " esemény";
  if (!events.length) emptyRow(tbody, 2, "Ebben a rendben még nincs esemény. Adj hozzá egyet a jobb oldali űrlappal.");
  events.forEach((ev, i) => {
    const tr = document.createElement("tr");
    const key = eventKey(ev);
    if (selectedRowKeys.has(key)) tr.classList.add("selected");
    else if (i % 2 === 1) tr.classList.add("alt");
    const tdTime = document.createElement("td");
    tdTime.className = "num";
    tdTime.textContent = ev.time;
    const tdFile = document.createElement("td");
    tdFile.textContent = ev.file;
    tr.appendChild(tdTime);
    tr.appendChild(tdFile);
    tr.addEventListener("click", (e) => handleRowClick(e, i, key));
    tr.addEventListener("dblclick", () => startEditEvent(ev));
    tbody.appendChild(tr);
  });
  scroller.scrollTop = scrollTop;
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
  document.getElementById("btnAddEvent").textContent = "Módosítás";
  document.getElementById("btnCancelEdit").style.display = "inline-flex";
  document.getElementById("eventEditorTitle").textContent = "Esemény szerkesztése";
  document.getElementById("editHint").textContent = `Eredeti: ${ev.time} – ${ev.file}`;
}

function cancelEdit() {
  editingOriginal = null;
  document.getElementById("eventTime").value = "";
  document.getElementById("eventFile").value = defaultMediaFile;
  document.getElementById("btnAddEvent").textContent = "Hozzáadás";
  document.getElementById("btnCancelEdit").style.display = "none";
  document.getElementById("eventEditorTitle").textContent = "Új esemény";
  document.getElementById("editHint").textContent = "";
}

document.getElementById("btnCancelEdit").addEventListener("click", cancelEdit);

document.getElementById("btnAddEvent").addEventListener("click", async () => {
  if (!currentScheduleData) {
    await showError("Először válassz ki egy rendet.");
    return;
  }
  const time = document.getElementById("eventTime").value.trim();
  const file = document.getElementById("eventFile").value.trim();
  if (!time || !file) {
    await showError("Add meg az időpontot és a fájlt.");
    return;
  }
  if (!/^([01]\d|2[0-3]):([0-5]\d)$/.test(time)) {
    await showError("Érvénytelen időpont formátum (HH:MM várva).");
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
  document.querySelector("#eventsTbody tr.selected")?.scrollIntoView({ block: "nearest" });
});

document.getElementById("btnRemoveEvent").addEventListener("click", async () => {
  if (!currentScheduleData || selectedRowKeys.size === 0) return;
  const count = selectedRowKeys.size;
  if (count > 1 && !(await modal.confirm(`Biztosan törlöd a kijelölt ${count} eseményt?`, { title: "Törlés", okText: "Törlés", danger: true }))) return;
  currentScheduleData.events = currentScheduleData.events.filter((e) => !selectedRowKeys.has(eventKey(e)));
  selectedRowKeys = new Set();
  selectionAnchorIndex = null;
  renderEventsTable();
});

document.getElementById("btnRemoveAllEvents").addEventListener("click", async () => {
  if (!currentScheduleData || currentScheduleData.events.length === 0) return;
  if (!(await modal.confirm(`Biztosan törlöd MIND a(z) ${currentScheduleData.events.length} eseményt ebből a rendből?`, { title: "Összes törlése", okText: "Összes törlése", danger: true }))) return;
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
    await showError(result.error);
    return;
  }
  loadActiveSchedule();
  await modal.alert(`${name} elmentve.`, { title: "Mentés sikeres" });
});

document.getElementById("btnRingNow").addEventListener("click", async () => {
  const filename = document.getElementById("eventFile").value.trim() || defaultMediaFile;
  if (!filename) {
    await showError("Nincs kiválasztott vagy alapértelmezett hangfájl.");
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
  const name = await modal.prompt("Fájlnév (pl. rovid.json):", { title: "Új rend", placeholder: "rovid.json" });
  if (!name) return;
  const result = await post("/api/schedules", { name });
  if (result && result.error) {
    await showError(result.error);
    return;
  }
  await loadSchedulesList();
  document.getElementById("scheduleSelect").value = result.filename;
  await loadScheduleEvents(result.filename);
});

// ---------- Media ful ----------

// Ures select: egyetlen, nem valaszthato opcio a magyarazattal
function addPlaceholderOption(select, text) {
  const opt = document.createElement("option");
  opt.value = "";
  opt.textContent = text;
  opt.disabled = true;
  select.appendChild(opt);
  select.value = "";
}

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
  document.getElementById("mediaEmpty").hidden = data.files.length > 0;

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
  if (!data.files.length) addPlaceholderOption(eventFile, "Nincs csengőhang");

  const defaultSelect = document.getElementById("defaultMediaSelect");
  defaultSelect.innerHTML = "";
  data.files.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f;
    opt.textContent = f;
    defaultSelect.appendChild(opt);
  });
  if (defaultMediaFile && data.files.includes(defaultMediaFile)) defaultSelect.value = defaultMediaFile;
  if (!data.files.length) addPlaceholderOption(defaultSelect, "Nincs csengőhang");
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
    await showError(result.error);
  } else {
    await loadMediaList();
  }
  e.target.value = "";
});

document.getElementById("btnPlaySelected").addEventListener("click", async () => {
  const listbox = document.getElementById("mediaListbox");
  if (!listbox.value) {
    await showError("Válassz ki egy fájlt a listából.");
    return;
  }
  const force = document.getElementById("forcePlay").checked;
  playAndAlert(listbox.value, force);
});

document.getElementById("btnSaveDefaultMedia").addEventListener("click", async () => {
  const filename = document.getElementById("defaultMediaSelect").value;
  if (!filename) {
    await showError("Válassz ki egy fájlt az alapértelmezetthez.");
    return;
  }
  const result = await post("/api/media/default", { filename });
  if (result && result.error) {
    await showError(result.error);
    return;
  }
  defaultMediaFile = filename;
  const eventFile = document.getElementById("eventFile");
  if (!eventFile.value) eventFile.value = filename;
  await modal.alert(filename, { title: "Alapértelmezett csengőhang beállítva" });
});

async function loadUploadLimit() {
  const data = await get("/api/media/upload-limit");
  document.getElementById("uploadLimitInput").value = data.max_upload_mb;
}

document.getElementById("btnSaveUploadLimit").addEventListener("click", async () => {
  const mb = Number(document.getElementById("uploadLimitInput").value);
  const result = await post("/api/media/upload-limit", { max_upload_mb: mb });
  if (result && result.error) {
    await showError(result.error);
    return;
  }
  await modal.alert(`Max. feltölthető fájlméret: ${result.max_upload_mb} MB`, { title: "Mentés sikeres" });
});

// ---------- Ido ful ----------

async function loadTimeState() {
  const data = await get("/api/time");
  document.getElementById("modeNtp").checked = data.mode === "ntp";
  document.getElementById("modeManual").checked = data.mode === "manual";
  document.getElementById("ntpServer").value = data.ntp_server;
  document.getElementById("currentTime").textContent = data.now;
  renderClock(data);
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
    await showError("NTP hiba: " + (result ? result.error : "ismeretlen hiba") + "\nRendszeróra kerül használatra.");
  }
});

document.getElementById("btnApplyManual").addEventListener("click", async () => {
  const value = document.getElementById("manualTime").value.trim();
  const result = await post("/api/time/manual", { value });
  if (result && result.error) {
    await showError(result.error);
    return;
  }
  document.getElementById("modeManual").checked = true;
});

setInterval(() => {
  get("/api/time")
    .then((data) => {
      if (!data || !data.now) return;
      document.getElementById("currentTime").textContent = data.now;
      renderClock(data);
    })
    .catch(() => {});
}, 1000);

// az utemezo altal hasznalt rend (a kovetkezo csengeteshez); mentes es valtas utan azonnal is frissul
setInterval(() => loadActiveSchedule().catch(() => {}), 15000);

// ---------- Kimenet ful ----------

let deviceMap = {};

async function loadOutputState() {
  const data = await get("/api/output/devices");
  const select = document.getElementById("deviceSelect");
  select.innerHTML = "";
  deviceMap = {};

  const defaultOpt = document.createElement("option");
  defaultOpt.value = "default";
  defaultOpt.textContent = "Rendszer alapértelmezett";
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
  const currentDevice = data.devices.find((d) => d.index === data.current);
  outputDeviceName = currentDevice ? currentDevice.name : "Rendszer alapértelmezett";
  renderOutputSummary(lastMixerData);

  const slider = document.getElementById("volumeSlider");
  slider.max = data.max_volume;
  slider.value = data.volume;
  setSliderFill(slider);
  updateVolumeLabel(data.volume);
}

document.getElementById("deviceSelect").addEventListener("change", async (e) => {
  const idx = deviceMap[e.target.value];
  await post("/api/output/device", { index: idx });
  const option = e.target.options[e.target.selectedIndex];
  outputDeviceName = idx === null ? "Rendszer alapértelmezett" : option.textContent.replace(/ \(#\d+\)$/, "");
  renderOutputSummary(lastMixerData);
});

function updateVolumeLabel(value) {
  const rounded = Math.round(value);
  const suffix = rounded > 100 ? " (Boost)" : "";
  document.getElementById("volumeLabel").textContent = `Hangerő: ${rounded}%${suffix}`;
}

let volumeDebounce = null;
document.getElementById("volumeSlider").addEventListener("input", (e) => {
  const value = Number(e.target.value);
  updateVolumeLabel(value);
  if (volumeDebounce) clearTimeout(volumeDebounce);
  volumeDebounce = setTimeout(() => post("/api/output/volume", { percent: value }), 150);
});

// ---------- Naplo ful ----------

// a doboz aljan marad, ha a felhasznalo nem gorgetett feljebb
function fillLog(box, lines, emptyText) {
  const wasAtBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
  box.textContent = lines.length ? lines.join("\n") : emptyText;
  if (wasAtBottom) box.scrollTop = box.scrollHeight;
}

async function loadLogs() {
  const data = await get("/api/logs?limit=300");
  const lines = data.lines || [];
  fillLog(document.getElementById("logBox"), lines, "A napló üres.");
  fillLog(document.getElementById("mixerLogBox"), lines.slice(-60), "Még nincs esemény.");
}

setInterval(loadLogs, 2000);

// ---------- Eszkozok ful ----------

const DEVICE_STATUS_TEXT = {
  connected: "Csatlakozva",
  offline: "Nincs kapcsolat",
  disabled: "Letiltva",
  expired: "Lejárt token",
};

// StatusBadge tonus: zold = el, sarga = figyelmeztetes, szurke = inaktiv
const DEVICE_STATUS_TONE = {
  connected: "ok",
  offline: "off",
  disabled: "off",
  expired: "warn",
};

function statusBadge(text, tone) {
  const badge = document.createElement("span");
  badge.className = "status";
  badge.dataset.tone = tone || "off";
  badge.textContent = text;
  return badge;
}

let editingDeviceId = null;

function formatDeviceTime(epoch) {
  if (!epoch) return "-";
  return new Date(epoch * 1000).toLocaleString("hu-HU");
}

function deviceFields() {
  return {
    name: document.getElementById("deviceName").value.trim(),
    ip_address: document.getElementById("deviceIp").value.trim(),
    mac_address: document.getElementById("deviceMac").value.trim() || null,
    hostname: document.getElementById("deviceHostname").value.trim() || null,
    version: document.getElementById("deviceVersion").value.trim() || null,
  };
}

function resetDeviceForm() {
  editingDeviceId = null;
  for (const id of ["deviceName", "deviceIp", "deviceMac", "deviceHostname", "deviceVersion"]) {
    document.getElementById(id).value = "";
  }
  document.getElementById("btnDeviceSave").textContent = "Hozzáadás";
  document.getElementById("btnDeviceCancel").style.display = "none";
  document.getElementById("deviceEditHint").textContent = "";
}

function startEditDevice(device) {
  editingDeviceId = device.id;
  document.getElementById("deviceName").value = device.name;
  document.getElementById("deviceIp").value = device.ip_address;
  document.getElementById("deviceMac").value = device.mac_address || "";
  document.getElementById("deviceHostname").value = device.hostname || "";
  document.getElementById("deviceVersion").value = device.version || "";
  document.getElementById("btnDeviceSave").textContent = "Mentés";
  document.getElementById("btnDeviceCancel").style.display = "";
  document.getElementById("deviceEditHint").textContent = "Szerkesztés: " + device.name;
}

function showToken(deviceName, token) {
  document.getElementById("tokenDeviceName").textContent = "Új token: " + deviceName;
  document.getElementById("tokenValue").value = token;
  document.getElementById("tokenOverlay").classList.add("visible");
  document.getElementById("tokenValue").select();
}

function closeToken() {
  document.getElementById("tokenValue").value = "";
  document.getElementById("tokenOverlay").classList.remove("visible");
}

document.getElementById("btnTokenClose").addEventListener("click", closeToken);
document.getElementById("btnTokenCopy").addEventListener("click", async () => {
  const input = document.getElementById("tokenValue");
  try {
    await navigator.clipboard.writeText(input.value);
  } catch (e) {
    input.select();
    document.execCommand("copy");
  }
});

function deviceActionButton(label, cls, handler) {
  const btn = document.createElement("button");
  btn.className = "btn " + cls;
  btn.textContent = label;
  btn.addEventListener("click", handler);
  return btn;
}

async function rotateDevice(device) {
  const ok = await modal.confirm(
    `Új tokent kap: ${device.name}. A régi token azonnal érvénytelen, a futó kapcsolat megszakad, az eszközt újra be kell állítani.`,
    { title: "Új token kiadása", okText: "Új token", danger: true }
  );
  if (!ok) return;
  const result = await post(`/api/devices/${device.id}/rotate`);
  if (!result || result.error) return showError((result && result.error) || "Ismeretlen hiba");
  showToken(device.name, result.token);
  await loadDevices();
}

async function toggleDevice(device) {
  const result = await api(`/api/devices/${device.id}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled: !device.enabled }),
  });
  if (!result || result.error) return showError((result && result.error) || "Ismeretlen hiba");
  await loadDevices();
}

async function deleteDevice(device) {
  const ok = await modal.confirm(`Törlöd az eszközt: ${device.name}? A futó kapcsolata megszakad.`, {
    title: "Eszköz törlése",
    okText: "Törlés",
    danger: true,
  });
  if (!ok) return;
  const result = await api(`/api/devices/${device.id}`, { method: "DELETE" });
  if (!result || result.error) return showError((result && result.error) || "Ismeretlen hiba");
  if (editingDeviceId === device.id) resetDeviceForm();
  await loadDevices();
}

function renderDevices(devices) {
  const body = document.getElementById("devicesBody");
  body.innerHTML = "";
  devices.forEach((device, index) => {
    const tr = document.createElement("tr");
    if (index % 2) tr.className = "alt";
    const cell = (text) => {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
      return td;
    };
    cell(device.name);
    cell(device.ip_address);
    cell("").appendChild(statusBadge(DEVICE_STATUS_TEXT[device.status] || device.status, DEVICE_STATUS_TONE[device.status]));
    const soon = device.expiring_soon && device.status !== "expired";
    const expires = cell(formatDeviceTime(device.token_expires_at) + (soon ? " (hamarosan lejár)" : ""));
    if (device.expiring_soon) expires.className = "expiring-soon";
    cell(formatDeviceTime(device.last_seen));
    const td = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.appendChild(deviceActionButton("Szerkesztés", "btn-sm", () => startEditDevice(device)));
    actions.appendChild(deviceActionButton(device.enabled ? "Letiltás" : "Engedélyezés", "btn-sm", () => toggleDevice(device)));
    actions.appendChild(deviceActionButton("Új token", "btn-sm", () => rotateDevice(device)));
    actions.appendChild(deviceActionButton("Törlés", "btn-sm btn-danger", () => deleteDevice(device)));
    td.appendChild(actions);
    tr.appendChild(td);
    body.appendChild(tr);
  });
}

const PENDING_CONFLICT_TEXT = {
  ip_changed: "Ütközés: más IP-ről is jelentkezett",
  uuid_known: "Ütközés: ez az UUID már felvett eszköz, más IP-ről",
  ip_taken: "Ütközés: az IP-cím már foglalt",
};

async function approvePending(entry) {
  const name = await modal.prompt(`Eszköz neve (${entry.ip_address}):`, { title: "Eszköz felvétele", placeholder: "pl. Aula ESP32" });
  if (!name) return;
  const result = await post(`/api/devices/pending/${entry.uuid}/approve`, { name: name.trim() });
  if (!result || result.error) return showError((result && result.error) || "Ismeretlen hiba");
  showToken(result.device.name, result.token);
  await loadDevices();
  await loadDeviceLog();
}

async function dismissPending(entry) {
  const result = await api(`/api/devices/pending/${entry.uuid}`, { method: "DELETE" });
  if (!result || result.error) return showError((result && result.error) || "Ismeretlen hiba");
  await loadDevices();
}

function renderPending(data) {
  const pending = (data && data.pending) || [];
  document.getElementById("pendingHint").hidden = !data || data.enabled !== false;
  document.getElementById("pendingCard").hidden = pending.length === 0;
  const body = document.getElementById("pendingBody");
  body.innerHTML = "";
  pending.forEach((entry, index) => {
    const tr = document.createElement("tr");
    if (index % 2) tr.className = "alt";
    const cell = (text) => {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
      return td;
    };
    cell(entry.uuid);
    cell(entry.ip_address);
    cell(entry.version || "-");
    cell(formatDeviceTime(entry.last_seen));
    const statusText = entry.conflict ? PENDING_CONFLICT_TEXT[entry.conflict] || "Ütközés" : "Jóváhagyásra vár";
    cell("").appendChild(statusBadge(statusText, entry.conflict ? "warn" : "off"));
    const td = document.createElement("td");
    const actions = document.createElement("div");
    actions.className = "actions";
    if (!entry.conflict) actions.appendChild(deviceActionButton("Felvétel", "btn-sm btn-primary", () => approvePending(entry)));
    actions.appendChild(deviceActionButton("Elvetés", "btn-sm", () => dismissPending(entry)));
    td.appendChild(actions);
    tr.appendChild(td);
    body.appendChild(tr);
  });
}

async function loadDevices() {
  const data = await get("/api/devices");
  renderDeviceSummary(data && data.devices);
  renderDevices((data && data.devices) || []);
  renderPending(await get("/api/devices/pending"));
}

async function loadDeviceLog() {
  const data = await get("/api/logs?limit=1000");
  const lines = ((data && data.lines) || []).filter((line) => line.includes("[device:"));
  const box = document.getElementById("deviceLogBox");
  const wasAtBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
  box.textContent = lines.slice(-200).join("\n");
  if (wasAtBottom) box.scrollTop = box.scrollHeight;
}

document.getElementById("btnDeviceSave").addEventListener("click", async () => {
  const fields = deviceFields();
  const result = editingDeviceId
    ? await api(`/api/devices/${editingDeviceId}`, { method: "PATCH", body: JSON.stringify(fields) })
    : await post("/api/devices", fields);
  if (!result || result.error) return showError((result && result.error) || "Ismeretlen hiba");
  const created = !editingDeviceId;
  resetDeviceForm();
  if (created) showToken(result.device.name, result.token);
  await loadDevices();
  await loadDeviceLog();
});

document.getElementById("btnDeviceCancel").addEventListener("click", resetDeviceForm);

document.querySelector('.nav-item[data-tab="devices"]').addEventListener("click", () => {
  loadDevices();
  loadDeviceLog();
});

setInterval(() => {
  if (isPageActive("devices")) {
    loadDevices();
    loadDeviceLog();
  } else {
    // a statuszsav hangszoro-szamlaloja mas fulon is frissul
    get("/api/devices").then((data) => renderDeviceSummary(data && data.devices)).catch(() => {});
  }
}, 4000);

// ---------- zene (YouTube) ----------

const MUSIC_STATUS_TEXT = {
  idle: "Nincs aktív zene",
  pending: "Indul…",
  downloading: "Letöltés…",
  playing: "Szól",
  skipped: "Kihagyva: nem fér bele a következő csengetésig",
  done: "Lejátszás kész",
  stopped: "Leállítva",
  error: "Hiba",
};

async function loadMusicList() {
  const data = await get("/api/music");
  const box = document.getElementById("musicListbox");
  const selected = box.value;
  box.innerHTML = "";
  for (const name of (data && data.files) || []) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    box.appendChild(opt);
  }
  if (selected) box.value = selected;
  document.getElementById("musicEmpty").hidden = box.options.length > 0;
  markCurrentMusic();
}

let currentMusic = null;

function markCurrentMusic() {
  for (const opt of document.getElementById("musicListbox").options) {
    const playing = opt.value === currentMusic;
    opt.classList.toggle("now-playing", playing);
    opt.textContent = (playing ? "▶ " : "") + opt.value;
  }
}

let lastMusicStatus = null;

async function loadMusicStatus() {
  const data = await get("/api/music/status");
  if (!data) return;
  let text = MUSIC_STATUS_TEXT[data.status] || data.status;
  if (data.error) text += ": " + data.error;
  currentMusic = data.status === "playing" ? data.current : null;
  if (currentMusic) text += " – " + currentMusic;
  markCurrentMusic();
  setStatus("musicStatus", data.status === "playing" ? "ok" : data.status === "error" ? "warn" : "off", text);
  document.getElementById("musicStatus").title = text;
  document.getElementById("btnMusicStop").disabled = !MUSIC_BUSY.includes(data.status);
  // letoltes utan frissitjuk a listat
  if (lastMusicStatus === "downloading" && data.status !== "downloading") loadMusicList();
  lastMusicStatus = data.status;
}

async function startMusic(path, body, label) {
  const result = await post(path, {
    ...body,
    force: document.getElementById("musicForce").checked,
    now_play: document.getElementById("musicNowPlay").checked,
  });
  if (result && result.error) {
    await showError(result.error);
    return;
  }
  showAlert("Zene elindítva", label, true);
  loadMusicStatus();
}

document.getElementById("btnMusicPlay").addEventListener("click", async () => {
  const url = document.getElementById("musicUrl").value.trim();
  if (!url) {
    await showError("Adj meg egy YouTube linket.");
    return;
  }
  await startMusic("/api/music/play", { urls: [url] }, url);
});

document.getElementById("btnMusicPlayFile").addEventListener("click", async () => {
  const name = document.getElementById("musicListbox").value;
  if (!name) {
    await showError("Válassz ki egy fájlt a listából.");
    return;
  }
  await startMusic("/api/music/play-file", { filenames: [name] }, name);
});

document.getElementById("btnMusicStop").addEventListener("click", async () => {
  await post("/api/music/stop");
  loadMusicStatus();
});

document.getElementById("btnMusicDelete").addEventListener("click", async () => {
  const name = document.getElementById("musicListbox").value;
  if (!name) {
    await showError("Válassz ki egy fájlt a listából.");
    return;
  }
  if (!(await modal.confirm(name, { title: "Fájl törlése", okText: "Törlés", danger: true }))) return;
  const result = await api("/api/music/" + encodeURIComponent(name), { method: "DELETE" });
  if (result && result.error) {
    await showError(result.error);
    return;
  }
  await loadMusicList();
});

document.getElementById("btnMusicRefresh").addEventListener("click", loadMusicList);

setInterval(() => {
  if (isPageActive("library")) loadMusicStatus();
}, 2000);

// ---------- Mixer ful ----------
// A felulet csak azt mutatja, hogy van-e jel es mi szol (zene, mikrofon);
// a belso mukodes (csomagok, buffer, stream) szandekosan nem latszik.

const SIGNAL_ON = 0.03;   // efelett "Van jel"
const SIGNAL_OFF = 0.015; // ez alatt "Nincs jel" (hiszterezis, hogy ne villogjon)
let mixerHasSignal = false;
const mixerVolumeDebounce = {};

const MIXER_VOLUMES = [
  { key: "master", slider: "mixerVolMaster", label: "mixerVolMasterVal" },
  { key: "music", slider: "mixerVolMusic", label: "mixerVolMusicVal" },
  { key: "mic", slider: "mixerVolMic", label: "mixerVolMicVal" },
];

let lastMixerData = null;

const MUSIC_BUSY = ["pending", "downloading", "playing"];
const MUSIC_STRIP_STATE = {
  idle: "Nincs zene",
  pending: "Indul",
  downloading: "Letöltés",
  playing: "Szól",
  skipped: "Kihagyva",
  done: "Vége",
  stopped: "Leállítva",
  error: "Hiba",
};

// Jelszintmero: a csucsertek (0..1) dBFS-ben, -60..0 dB skalan
const METER_FLOOR_DB = -60;

function renderMeter(level) {
  const db = level > 0 ? 20 * Math.log10(level) : -Infinity;
  const shown = Math.max(0, Math.min(1, (db - METER_FLOOR_DB) / -METER_FLOOR_DB));
  const fill = document.getElementById("meterFill");
  fill.style.height = (shown * 100).toFixed(1) + "%";
  fill.classList.toggle("hot", level >= 0.95);
  const meter = document.getElementById("meter");
  meter.setAttribute("aria-valuenow", (shown * 100).toFixed(0));
  meter.setAttribute("aria-valuetext", Number.isFinite(db) ? Math.round(db) + " dBFS" : "nincs jel");
}

// A mixer allapota egy helyen, a felso savban latszik.
function setMixerStateView(tone, text) {
  const el = document.getElementById("headerMixer");
  el.dataset.tone = tone;
  document.getElementById("headerMixerText").textContent = text;
}

function renderMixer(data) {
  lastMixerData = data;
  if (!data.running) mixerHasSignal = false;
  else if (data.level >= SIGNAL_ON) mixerHasSignal = true;
  else if (data.level < SIGNAL_OFF) mixerHasSignal = false;

  if (!data.running) setMixerStateView("off", "Mixer: leállítva");
  else if (data.output_ok === false) setMixerStateView("warn", mixerHasSignal ? "Mixer: van jel, nincs kimenet" : "Mixer: nincs kimenet");
  else if (mixerHasSignal) setMixerStateView("ok", "Mixer: van jel");
  else setMixerStateView("off", "Mixer: nincs jel");
  renderOutputSummary(data);
  document.getElementById("onAirBadge").hidden = !data.mic_live;

  const flags = data.flags || [];

  // Csengo csatorna
  const bellOn = data.running && flags.includes("BELL");
  setStatus("stripBellState", bellOn ? "ok" : "off", bellOn ? "Szól" : "Csend");

  // Mikrofon csatorna: adasban piros jelzes a csatornan es a felso savban
  const live = !!data.mic_live;
  const power = document.getElementById("mixerPower");
  if (document.activeElement !== power) power.checked = live;
  document.getElementById("mixerPowerLabel").textContent = live ? "Adás leállítása" : "Adás indítása";
  document.getElementById("stripMic").classList.toggle("is-onair", live);
  document.getElementById("stripMicOnAir").hidden = !live;
  document.getElementById("stripMicState").hidden = live;

  // Master csatorna: kimenet allapota es jelszint
  document.getElementById("mixerWarning").hidden = data.output_ok !== false;
  if (!data.running) setStatus("stripMasterState", "off", "Leállítva");
  else if (data.output_ok === false) setStatus("stripMasterState", "warn", "Nincs kimenet");
  else setStatus("stripMasterState", "ok", "Él");
  renderMeter(data.level || 0);

  // Zene csatorna: a zene fulon inditott szam itt is latszik
  const music = data.music || {};
  const musicSelect = document.getElementById("mixerMusicSelect");
  if (music.status === "playing" && music.current && document.activeElement !== musicSelect) {
    musicSelect.value = music.current;
  }
  const musicOn = data.running && flags.includes("PLAYING_MUSIC");
  const musicBusy = MUSIC_BUSY.includes(music.status);
  setStatus("stripMusicState", musicOn ? "ok" : music.status === "error" ? "warn" : "off", musicOn ? "Szól" : MUSIC_STRIP_STATE[music.status] || "Nincs zene");
  // lejatszas kozben a Lejatszas gomb tiltott (masik szamra valtani tovabbra is lehet)
  document.getElementById("btnMixerMusicPlay").disabled = !musicSelect.value || (musicBusy && music.current === musicSelect.value);
  document.getElementById("btnMixerMusicStop").disabled = !musicBusy;
  let now = "Nincs lejátszás";
  if (music.status === "playing" && music.current) now = "Most szól: " + music.current;
  else if (music.status === "error") now = "Hiba" + (music.error ? ": " + music.error : "");
  else if (musicBusy) now = MUSIC_STATUS_TEXT[music.status];
  else if (!musicSelect.value) now = "Nincs kiválasztott zene";
  const nowEl = document.getElementById("mixerMusicNow");
  nowEl.textContent = now;
  nowEl.title = now;

  for (const v of MIXER_VOLUMES) {
    const slider = document.getElementById(v.slider);
    // amig huzod / var a mentesre, nem irjuk felul
    if (document.activeElement !== slider && !mixerVolumeDebounce[v.key]) {
      slider.value = Math.round(data.volume[v.key] * 100);
      setSliderFill(slider);
    }
    document.getElementById(v.label).textContent = slider.value + "%";
  }
}

async function pollMixer() {
  try {
    const data = await get("/api/mixer");
    if (data && !data.error) {
      renderMixer(data);
      setConnection(true, "Szerver: kapcsolódva");
    } else {
      setConnection(false, "Szerver: hibás válasz");
    }
  } catch (e) {
    // a kovetkezo korben ujra probalja
    setConnection(false, "Szerver: nem érhető el");
  }
}

(function startMixerPolling() {
  const tick = async () => {
    await pollMixer();
    const active = document.getElementById("panel-mixer").classList.contains("active");
    setTimeout(tick, active ? 200 : 1000);
  };
  tick();
})();

document.getElementById("mixerPower").addEventListener("change", async (e) => {
  const result = await post("/api/mixer/mic", { live: e.target.checked });
  if (result && result.error) await showError(result.error);
  e.target.blur(); // a fókusz elengedése után a lekérdezés a valós állapotot mutatja (hiba esetén visszakapcsol)
  await pollMixer();
});

for (const v of MIXER_VOLUMES) {
  document.getElementById(v.slider).addEventListener("input", (e) => {
    const value = Number(e.target.value);
    document.getElementById(v.label).textContent = value + "%";
    clearTimeout(mixerVolumeDebounce[v.key]);
    mixerVolumeDebounce[v.key] = setTimeout(async () => {
      mixerVolumeDebounce[v.key] = null;
      const result = await post("/api/mixer/volume", { [v.key]: value / 100 });
      if (result && result.error) await showError(result.error);
    }, 150);
  });
}

async function loadMixerInputs() {
  const data = await get("/api/mixer/inputs");
  if (!data || data.error) return;
  const select = document.getElementById("mixerInputSelect");
  select.innerHTML = "";
  const inputs = data.inputs || [];
  if (inputs.length === 0) {
    // mikrofon nelkuli gep: egy letiltott jelzo opcio, a tobbi betoltes fut tovabb
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "Nincs mikrofon";
    opt.disabled = true;
    select.appendChild(opt);
    select.value = "";
    return;
  }
  for (const d of inputs) {
    const opt = document.createElement("option");
    opt.value = d.index;
    opt.textContent = d.name;
    select.appendChild(opt);
  }
  select.value = data.current ? data.current.index : "";
}

document.getElementById("mixerInputSelect").addEventListener("change", async (e) => {
  const result = await post("/api/mixer/input", { index: Number(e.target.value) });
  if (result && result.error) {
    await showError(result.error);
    await loadMixerInputs();
  }
});

async function loadMixerMusic() {
  const data = await get("/api/music");
  const select = document.getElementById("mixerMusicSelect");
  const selected = select.value;
  select.innerHTML = "";
  const files = data && !data.error && Array.isArray(data.files) ? data.files : [];
  if (files.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "Nincs letöltött zene";
    opt.disabled = true;
    select.appendChild(opt);
    select.value = "";
    return;
  }
  for (const name of files) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
  if (selected) select.value = selected;
}

document.getElementById("btnMixerMusicPlay").addEventListener("click", async () => {
  const name = document.getElementById("mixerMusicSelect").value;
  if (!name) {
    await showError("Válassz ki egy zenét a listából.");
    return;
  }
  const result = await post("/api/mixer/music", { name });
  if (result && result.error) await showError(result.error);
  await pollMixer();
});

document.getElementById("btnMixerMusicStop").addEventListener("click", async () => {
  await api("/api/mixer/music", { method: "DELETE" });
  await pollMixer();
});

document.querySelector('.nav-item[data-tab="mixer"]').addEventListener("click", () => {
  loadMixerMusic();
  loadMixerInputs();
});

// ---------- inicializalas ----------

async function init() {
  // minden betoltes kulon: egy hiba (pl. nincs mikrofon) ne allitsa le a tobbit
  const loaders = [
    loadMixerInputs,
    loadMixerMusic,
    loadMediaList,
    loadSchedulesList,
    loadActiveSchedule,
    loadTimeState,
    loadOutputState,
    loadUploadLimit,
    loadMusicList,
    loadMusicStatus,
    loadLogs,
    loadDevices,
    loadDeviceLog,
    cancelEdit,
  ];
  for (const load of loaders) {
    try {
      await load();
    } catch (e) {
      console.error("Betöltési hiba (" + load.name + "):", e);
    }
  }
}

init();
