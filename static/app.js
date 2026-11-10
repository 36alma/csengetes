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
    const page = document.getElementById("panel-" + tab.dataset.tab);
    page.classList.add("active");
    // a rejtett oldalon betoltott naplo megnyitaskor a legfrissebb soroknal alljon
    page.querySelectorAll(".log").forEach((box) => { box.scrollTop = box.scrollHeight; });
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
  document.getElementById("bellScheduleName").textContent = activeSchedule ? activeSchedule.name : "nincs aktív rend";
  document.getElementById("bellNextTime").textContent = next ? formatHM(next.at) : "–";
  document.getElementById("bellNextIn").textContent = next
    ? (next.tomorrow ? "holnap, " : "") + formatCountdown(next.in) + " múlva"
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
  if (nextItem) list.scrollTop = Math.max(0, nextItem.offsetTop - 32);
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

// ---------- csuszka kezeles: dupla katt, gorgo, ertek beirasa ----------

const SLIDER_DEFAULT = 100;

function setSliderValue(slider, raw) {
  const min = Number(slider.min) || 0;
  const max = Number(slider.max) || 100;
  const value = Math.min(max, Math.max(min, Math.round(Number(raw))));
  if (Number.isNaN(value)) return;
  slider.value = value;
  setSliderFill(slider);
  slider.dispatchEvent(new Event("input", { bubbles: true }));
}

function isVolumeSlider(el) {
  return el && el.classList && (el.classList.contains("slider") || el.classList.contains("fader"));
}

// dupla katt: vissza az alapertekre (100%)
document.addEventListener("dblclick", (e) => {
  if (isVolumeSlider(e.target)) setSliderValue(e.target, SLIDER_DEFAULT);
});

// gorgo: csak akkor allit, ha az egér a csuszka folott van
document.addEventListener("wheel", (e) => {
  if (!isVolumeSlider(e.target) || !e.deltaY) return;
  e.preventDefault();
  const step = e.shiftKey ? 5 : 1;
  setSliderValue(e.target, Number(e.target.value) + (e.deltaY < 0 ? step : -step));
}, { passive: false });

// az ertekre kattintva be lehet irni a szamot a min..max tartomanyban
document.addEventListener("click", (e) => {
  const out = e.target.closest && e.target.closest("output[for]");
  if (!out || out.hidden) return;
  const slider = document.getElementById(out.getAttribute("for"));
  if (!isVolumeSlider(slider)) return;
  const input = document.createElement("input");
  input.type = "number";
  input.className = "value-edit num";
  input.min = slider.min;
  input.max = slider.max;
  input.step = 1;
  input.value = slider.value;
  input.setAttribute("aria-label", `Érték (${slider.min}–${slider.max})`);
  let done = false;
  const finish = (commit) => {
    if (done) return;
    done = true;
    if (commit && input.value !== "") setSliderValue(slider, input.value);
    out.hidden = false;
    input.remove();
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") finish(true);
    else if (ev.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
  out.hidden = true;
  out.after(input);
  input.focus();
  input.select();
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
  const label = document.getElementById("volumeLabel");
  label.textContent = rounded > 100 ? `${rounded}% erősítés` : `${rounded}%`;
  label.classList.toggle("is-boost", rounded > 100);
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

const LOG_LINE_RE = /^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:[.,]\d+)? \[([A-Za-z]+)\] ?(.*)$/;
const LOG_LEVELS = { info: "info", debug: "info", warning: "warn", warn: "warn", error: "error", critical: "error" };
const LOG_LEVEL_TEXT = { info: "INFO", warn: "FIGYELEM", error: "HIBA" };
const LOG_NOISE_RE = /NTP szinkron sikeres/i;

const logView = { lines: [], level: "all", query: "", range: 0, hideNoise: false };

function parseLogLine(raw) {
  const m = LOG_LINE_RE.exec(raw);
  if (!m) return { raw, date: "", time: "", level: "info", text: raw, stamp: null };
  const level = LOG_LEVELS[m[3].toLowerCase()] || "info";
  const stamp = new Date(`${m[1]}T${m[2]}`).getTime();
  return { raw, date: m[1], time: m[2], level, text: m[4], stamp: Number.isNaN(stamp) ? null : stamp };
}

function filterLogEntries(entries, view) {
  const query = view.query.trim().toLowerCase();
  const terms = query ? query.split(/\s+/) : [];
  const newest = entries.reduce((max, e) => (e.stamp && e.stamp > max ? e.stamp : max), 0);
  const since = view.range && newest ? newest - view.range * 3600 * 1000 : 0;
  return entries.filter((e) => {
    if (view.level !== "all" && e.level !== view.level) return false;
    if (since && e.stamp !== null && e.stamp < since) return false;
    if (view.hideNoise && LOG_NOISE_RE.test(e.text)) return false;
    if (terms.length) {
      const hay = e.raw.toLowerCase();
      if (!terms.every((t) => hay.includes(t))) return false;
    }
    return true;
  });
}

function appendHighlighted(node, text, terms) {
  if (!terms.length) { node.textContent = text; return; }
  const escaped = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${escaped.join("|")})`, "gi");
  text.split(re).forEach((part, i) => {
    if (i % 2 === 1) {
      const mark = document.createElement("mark");
      mark.textContent = part;
      node.appendChild(mark);
    } else if (part) {
      node.appendChild(document.createTextNode(part));
    }
  });
}

function renderLogView() {
  const box = document.getElementById("logBox");
  const wasAtBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 4;
  const entries = logView.lines.map(parseLogLine);
  const shown = filterLogEntries(entries, logView);
  const terms = logView.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const frag = document.createDocumentFragment();
  let lastDate = "";
  for (const e of shown) {
    if (e.date && e.date !== lastDate) {
      lastDate = e.date;
      const sep = document.createElement("div");
      sep.className = "log-day";
      sep.textContent = e.date;
      frag.appendChild(sep);
    }
    const row = document.createElement("div");
    row.className = `log-row log-${e.level}`;
    const time = document.createElement("span");
    time.className = "log-time";
    time.textContent = e.time;
    const badge = document.createElement("span");
    badge.className = "log-badge";
    badge.textContent = LOG_LEVEL_TEXT[e.level];
    const text = document.createElement("span");
    text.className = "log-text";
    appendHighlighted(text, e.text, terms);
    row.append(time, badge, text);
    frag.appendChild(row);
  }
  if (!shown.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = entries.length ? "Nincs a szűrésnek megfelelő sor." : "A napló üres.";
    frag.appendChild(empty);
  }
  box.replaceChildren(frag);
  if (wasAtBottom) box.scrollTop = box.scrollHeight;

  const filtered = logView.level !== "all" || terms.length > 0 || logView.range > 0 || logView.hideNoise;
  document.getElementById("logClear").hidden = !filtered;
  document.getElementById("logSummary").textContent = filtered
    ? `${shown.length} / ${entries.length} sor – automatikusan frissül`
    : `Az utolsó ${entries.length} sor – automatikusan frissül`;
  return shown;
}

async function loadLogs() {
  const data = await get("/api/logs?limit=1000");
  const lines = data.lines || [];
  logView.lines = lines;
  renderLogView();
  fillLog(document.getElementById("mixerLogBox"), lines.slice(-60), "Még nincs esemény.");
}

function resetLogFilters() {
  logView.level = "all";
  logView.query = "";
  logView.range = 0;
  logView.hideNoise = false;
  document.getElementById("logSearch").value = "";
  document.getElementById("logRange").value = "0";
  document.getElementById("logHideNoise").checked = false;
  syncLogLevelButtons();
  renderLogView();
}

function syncLogLevelButtons() {
  document.querySelectorAll("#logLevels .log-level").forEach((b) => {
    const active = b.dataset.level === logView.level;
    b.classList.toggle("is-active", active);
    b.setAttribute("aria-pressed", String(active));
  });
}

document.getElementById("logSearch").addEventListener("input", (e) => {
  logView.query = e.target.value;
  renderLogView();
});
document.getElementById("logLevels").addEventListener("click", (e) => {
  const btn = e.target.closest(".log-level");
  if (!btn) return;
  logView.level = btn.dataset.level;
  syncLogLevelButtons();
  renderLogView();
});
document.getElementById("logRange").addEventListener("change", (e) => {
  logView.range = Number(e.target.value);
  renderLogView();
});
document.getElementById("logHideNoise").addEventListener("change", (e) => {
  logView.hideNoise = e.target.checked;
  renderLogView();
});
document.getElementById("logClear").addEventListener("click", resetLogFilters);
document.getElementById("logCopy").addEventListener("click", async (e) => {
  const entries = filterLogEntries(logView.lines.map(parseLogLine), logView);
  const btn = e.currentTarget;
  try {
    await navigator.clipboard.writeText(entries.map((x) => x.raw).join("\n"));
    btn.textContent = "Másolva";
  } catch {
    btn.textContent = "Nem sikerült";
  }
  setTimeout(() => { btn.textContent = "Másolás"; }, 1500);
});

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
  if (!epoch) return "–";
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
  document.getElementById("deviceFormTitle").textContent = "Eszköz hozzáadása";
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
  document.getElementById("deviceFormTitle").textContent = "Eszköz szerkesztése";
  document.getElementById("deviceEditHint").textContent = device.name;
}

function showToken(deviceName, token, hint) {
  document.getElementById("tokenDeviceName").textContent = "Új token: " + deviceName;
  const hintEl = document.getElementById("tokenHint");
  hintEl.textContent = hint || "";
  hintEl.hidden = !hint;
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
  if (!devices.length) emptyRow(body, 7, "Még nincs felvett eszköz. Add hozzá lent, vagy hagyd jóvá egy jelentkező eszközt.");
  devices.forEach((device, index) => {
    const tr = document.createElement("tr");
    const cell = (text, label) => {
      const td = document.createElement("td");
      td.textContent = text;
      if (label) td.dataset.label = label;
      tr.appendChild(td);
      return td;
    };
    cell(device.name).className = "cell-name";
    cell(device.ip_address, "IP-cím").className = "num";
    cell("", "Állapot").appendChild(statusBadge(DEVICE_STATUS_TEXT[device.status] || device.status, DEVICE_STATUS_TONE[device.status]));
    const buffer = bufferCell(device.buffer);
    buffer.dataset.label = "Buffer";
    tr.appendChild(buffer);
    const soon = device.expiring_soon && device.status !== "expired";
    const expires = cell(formatDeviceTime(device.token_expires_at) + (soon ? " (hamarosan lejár)" : ""), "Token lejár");
    expires.className = device.expiring_soon ? "num expiring-soon" : "num";
    cell(formatDeviceTime(device.last_seen), "Utolsó kapcsolat").className = "num";
    const td = document.createElement("td");
    td.className = "cell-actions";
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.appendChild(deviceActionButton("Napló", "btn-sm", () => showDeviceLog(device)));
    actions.appendChild(deviceActionButton("Szerkesztés", "btn-sm", () => startEditDevice(device)));
    actions.appendChild(deviceActionButton(device.enabled ? "Letiltás" : "Engedélyezés", "btn-sm", () => toggleDevice(device)));
    actions.appendChild(deviceActionButton("Új token", "btn-sm", () => rotateDevice(device)));
    actions.appendChild(deviceActionButton("Törlés", "btn-sm btn-danger", () => deleteDevice(device)));
    td.appendChild(actions);
    tr.appendChild(td);
    body.appendChild(tr);
  });
  fillLogSources(devices);
}

// Az ESP utolso buffer-jelentese: toltottseg-sav + szam; ha regi a jelentes, halvany
const BUFFER_STALE_SECONDS = 30;

function bufferCell(buffer) {
  const td = document.createElement("td");
  if (!buffer || typeof buffer.fill_ms !== "number") {
    td.textContent = "–";
    return td;
  }
  const capacity = typeof buffer.capacity_ms === "number" && buffer.capacity_ms > 0 ? buffer.capacity_ms : null;
  const wrap = document.createElement("div");
  const age = Date.now() / 1000 - (buffer.received_at || 0);
  wrap.className = "buffer-state" + (age > BUFFER_STALE_SECONDS ? " stale" : "");
  wrap.title = "Utolsó jelentés: " + formatDeviceTime(buffer.received_at);
  if (capacity) {
    const bar = document.createElement("span");
    bar.className = "buffer-bar";
    const fill = document.createElement("span");
    const ratio = Math.max(0, Math.min(1, buffer.fill_ms / capacity));
    fill.className = "buffer-fill" + (ratio < 0.2 ? " low" : "");
    fill.style.width = Math.round(ratio * 100) + "%";
    bar.appendChild(fill);
    wrap.appendChild(bar);
  }
  const text = document.createElement("span");
  text.className = "num";
  text.textContent = capacity ? `${buffer.fill_ms}/${capacity} ms` : `${buffer.fill_ms} ms`;
  wrap.appendChild(text);
  const problems = [];
  if (buffer.underruns > 0) problems.push(`${buffer.underruns} underrun`);
  if (buffer.drops > 0) problems.push(`${buffer.drops} drop`);
  if (problems.length) {
    const warn = document.createElement("span");
    warn.className = "buffer-warn";
    warn.textContent = problems.join(", ");
    wrap.appendChild(warn);
  }
  td.appendChild(wrap);
  return td;
}

// Az eszkoznaplo forrasa: "" = a kozos szerver-naplo [device:] sorai, kulonben egy eszkoz sajat naploja
let logSourceKey = null;
let logTypeKey = null;

function fillLogSources(devices) {
  const select = document.getElementById("deviceLogSourceSelect");
  const key = devices.map((d) => d.id + ":" + d.name).join("|");
  if (key === logSourceKey) return; // ne zarja be a nyitott listat minden frissitesnel
  logSourceKey = key;
  const previous = select.value;
  select.innerHTML = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "Szerver napló (összes eszköz)";
  select.appendChild(all);
  devices.forEach((d) => {
    const option = document.createElement("option");
    option.value = d.id;
    option.textContent = d.name + " – ESP napló";
    select.appendChild(option);
  });
  select.value = devices.some((d) => d.id === previous) ? previous : "";
}

function showDeviceLog(device) {
  document.getElementById("deviceLogSourceSelect").value = device.id;
  document.getElementById("deviceLogTypeSelect").value = "";
  loadDeviceLog();
}

const LOG_PLAIN_KEYS = new Set(["type", "msg", "ts", "source"]);

function formatEspEvent(event) {
  const extra = Object.keys(event)
    .filter((k) => !LOG_PLAIN_KEYS.has(k))
    .map((k) => `${k}=${event[k]}`)
    .join(" ");
  const parts = [formatDeviceTime(event.ts), `[${event.source === "server" ? "szerver" : "esp"}]`, event.type];
  if (event.msg) parts.push("– " + event.msg);
  if (extra) parts.push(extra);
  return parts.join(" ");
}

function fillLogTypes(events) {
  const select = document.getElementById("deviceLogTypeSelect");
  const types = [...new Set(events.map((e) => e.type))].sort();
  const key = types.join("|");
  select.hidden = false;
  if (key === logTypeKey) return;
  logTypeKey = key;
  const previous = select.value;
  select.innerHTML = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "Minden típus";
  select.appendChild(all);
  types.forEach((type) => {
    const option = document.createElement("option");
    option.value = type;
    option.textContent = type;
    select.appendChild(option);
  });
  select.value = types.includes(previous) ? previous : "";
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
    const cell = (text) => {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
      return td;
    };
    cell(entry.uuid);
    cell(entry.ip_address).className = "num";
    cell(entry.version || "–");
    cell(formatDeviceTime(entry.last_seen)).className = "num";
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
  const deviceId = document.getElementById("deviceLogSourceSelect").value;
  if (deviceId) {
    const data = await get(`/api/devices/${encodeURIComponent(deviceId)}/log?limit=500`);
    const events = (data && data.events) || [];
    fillLogTypes(events);
    const type = document.getElementById("deviceLogTypeSelect").value;
    const shown = type ? events.filter((e) => e.type === type) : events;
    fillLog(document.getElementById("deviceLogBox"), shown.slice(-200).map(formatEspEvent), "Még nincs esemény ettől az eszköztől.");
    return;
  }
  document.getElementById("deviceLogTypeSelect").hidden = true;
  const data = await get("/api/logs?limit=1000");
  const lines = ((data && data.lines) || []).filter((line) => line.includes("[device:"));
  fillLog(document.getElementById("deviceLogBox"), lines.slice(-200), "Még nincs eszközesemény a naplóban.");
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

document.getElementById("deviceLogSourceSelect").addEventListener("change", () => {
  document.getElementById("deviceLogTypeSelect").value = "";
  logTypeKey = null;
  loadDeviceLog();
});
document.getElementById("deviceLogTypeSelect").addEventListener("change", loadDeviceLog);

// ---------- Mikrofongépek (Eszközök fül) ----------

const REMOTE_MIC_STATE = {
  off: ["off", "Ki"],
  connecting: ["off", "Kapcsolódás…"],
  on_air: ["ok", "Adásban"],
  no_audio: ["warn", "Nincs hang"],
  unreachable: ["warn", "Nem elérhető"],
  auth_failed: ["warn", "Hibás token"],
  mic_error: ["warn", "Mikrofonhiba"],
  protocol_error: ["warn", "Eltérő verzió"],
};
const REMOTE_MIC_CONNECTED = ["on_air", "no_audio"];
const REMOTE_TOKEN_HINT = "A mikrofongépen futtasd: python -m micserver --set-token";

let editingRemoteMicId = null;

function remoteMicFields() {
  const fields = {
    name: document.getElementById("remoteMicName").value.trim(),
    host: document.getElementById("remoteMicHost").value.trim(),
  };
  const port = document.getElementById("remoteMicPort").value.trim();
  if (port) fields.port = Number(port);
  return fields;
}

function resetRemoteMicForm() {
  editingRemoteMicId = null;
  for (const id of ["remoteMicName", "remoteMicHost", "remoteMicPort"]) {
    document.getElementById(id).value = "";
  }
  document.getElementById("btnRemoteMicSave").textContent = "Hozzáadás";
  document.getElementById("btnRemoteMicCancel").style.display = "none";
  document.getElementById("remoteMicEditHint").textContent = "";
}

function startEditRemoteMic(source) {
  editingRemoteMicId = source.id;
  document.getElementById("remoteMicName").value = source.name;
  document.getElementById("remoteMicHost").value = source.host;
  document.getElementById("remoteMicPort").value = source.port;
  document.getElementById("btnRemoteMicSave").textContent = "Mentés";
  document.getElementById("btnRemoteMicCancel").style.display = "";
  document.getElementById("remoteMicEditHint").textContent = "Szerkesztés: " + source.name;
}

async function refreshRemoteMics() {
  await loadRemoteMics();
  await pollMixer();
}

async function rotateRemoteMic(source) {
  const result = await post(`/api/remote-mics/${source.id}/rotate`);
  if (!result || result.error) return showError((result && result.error) || "Ismeretlen hiba");
  showAlert("Token lecserélve", source.name, true);
  await refreshRemoteMics();
}

async function reissueRemoteMic(source) {
  const ok = await modal.confirm(
    `Új tokent kap: ${source.name}. A futó kapcsolat megszakad, és a gépen is be kell állítani az új tokent.`,
    { title: "Új token kiadása", okText: "Új token", danger: true }
  );
  if (!ok) return;
  const result = await post(`/api/remote-mics/${source.id}/reissue`);
  if (!result || result.error) return showError((result && result.error) || "Ismeretlen hiba");
  showToken(source.name, result.token, REMOTE_TOKEN_HINT);
  await refreshRemoteMics();
}

async function deleteRemoteMic(source) {
  const ok = await modal.confirm(`Törlöd a mikrofongépet: ${source.name}? A futó kapcsolata megszakad.`, {
    title: "Mikrofongép törlése",
    okText: "Törlés",
    danger: true,
  });
  if (!ok) return;
  const result = await api(`/api/remote-mics/${source.id}`, { method: "DELETE" });
  if (!result || result.error) return showError((result && result.error) || "Ismeretlen hiba");
  if (editingRemoteMicId === source.id) resetRemoteMicForm();
  await refreshRemoteMics();
}

function renderRemoteMics(sources) {
  const body = document.getElementById("remoteMicsBody");
  body.innerHTML = "";
  if (!sources.length) emptyRow(body, 6, "Még nincs felvett mikrofongép. Add hozzá lent a gép címével.");
  for (const source of sources) {
    const tr = document.createElement("tr");
    const cell = (text, label) => {
      const td = document.createElement("td");
      td.textContent = text;
      if (label) td.dataset.label = label;
      tr.appendChild(td);
      return td;
    };
    const [tone, text] = REMOTE_MIC_STATE[source.state] || ["off", source.state];
    cell(source.name).className = "cell-name";
    cell(`${source.host}:${source.port}`, "Cím").className = "num";
    cell("", "Állapot").appendChild(statusBadge(text, tone));
    cell(formatDeviceTime(source.rotated_at), "Utolsó tokencsere").className = "num";
    cell(formatDeviceTime(source.last_seen), "Utolsó kapcsolat").className = "num";
    const td = document.createElement("td");
    td.className = "cell-actions";
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.appendChild(deviceActionButton("Szerkesztés", "btn-sm", () => startEditRemoteMic(source)));
    const rotate = deviceActionButton("Token forgatása", "btn-sm", () => rotateRemoteMic(source));
    rotate.disabled = !REMOTE_MIC_CONNECTED.includes(source.state);
    actions.appendChild(rotate);
    actions.appendChild(deviceActionButton("Új token", "btn-sm", () => reissueRemoteMic(source)));
    actions.appendChild(deviceActionButton("Törlés", "btn-sm btn-danger", () => deleteRemoteMic(source)));
    td.appendChild(actions);
    tr.appendChild(td);
    body.appendChild(tr);
  }
}

async function loadRemoteMics() {
  const data = await get("/api/remote-mics");
  renderRemoteMics((data && data.sources) || []);
}

document.getElementById("btnRemoteMicSave").addEventListener("click", async () => {
  const fields = remoteMicFields();
  const result = editingRemoteMicId
    ? await api(`/api/remote-mics/${editingRemoteMicId}`, { method: "PATCH", body: JSON.stringify(fields) })
    : await post("/api/remote-mics", fields);
  if (!result || result.error) return showError((result && result.error) || "Ismeretlen hiba");
  const created = !editingRemoteMicId;
  resetRemoteMicForm();
  if (created) showToken(result.source.name, result.token, REMOTE_TOKEN_HINT);
  await refreshRemoteMics();
});

document.getElementById("btnRemoteMicCancel").addEventListener("click", resetRemoteMicForm);

document.querySelector('.nav-item[data-tab="devices"]').addEventListener("click", () => {
  loadDevices();
  loadDeviceLog();
  loadRemoteMics();
});

setInterval(() => {
  if (isPageActive("devices")) {
    loadDevices();
    loadDeviceLog();
    loadRemoteMics();
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
  { key: "bell", slider: "mixerVolBell", label: "mixerVolBellVal" },
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

function renderMeter(meterId, level) {
  const db = level > 0 ? 20 * Math.log10(level) : -Infinity;
  const shown = Math.max(0, Math.min(1, (db - METER_FLOOR_DB) / -METER_FLOOR_DB));
  const fill = document.getElementById(meterId + "Fill");
  fill.style.height = (shown * 100).toFixed(1) + "%";
  fill.classList.toggle("hot", level >= 0.95);
  const meter = document.getElementById(meterId);
  meter.setAttribute("aria-valuenow", (shown * 100).toFixed(0));
  meter.setAttribute("aria-valuetext", Number.isFinite(db) ? Math.round(db) + " dBFS" : "nincs jel");
}

// Hálózati mikrofon sávok: az id-lista változásakor épülnek újra, különben helyben frissülnek,
// mert a gyakori lekérdezés húzás közben elrontaná a csúszkát.
const remoteStrips = new Map();
let remoteStripIds = "";

function createRemoteStrip(item) {
  const root = document.getElementById("remoteStripTemplate").content.firstElementChild.cloneNode(true);
  root.dataset.remoteId = item.id;
  const refs = {};
  for (const role of ["title", "state", "onair", "meter", "fill", "fader", "value", "host", "power", "powerLabel"]) {
    refs[role] = root.querySelector(`[data-role="${role}"]`);
  }
  const meterId = "meterRemote_" + item.id;
  refs.meter.id = meterId;
  refs.fill.id = meterId + "Fill";
  refs.title.id = "stripRemoteTitle_" + item.id;
  root.setAttribute("aria-labelledby", refs.title.id);
  refs.fader.id = "mixerVolRemote_" + item.id;
  refs.value.setAttribute("for", refs.fader.id);

  const strip = { root, refs, meterId, debounce: null };
  refs.fader.addEventListener("input", (e) => {
    const value = Number(e.target.value);
    refs.value.textContent = value + "%";
    clearTimeout(strip.debounce);
    strip.debounce = setTimeout(async () => {
      strip.debounce = null;
      const result = await post(`/api/remote-mics/${item.id}/volume`, { volume: value / 100 });
      if (result && result.error) await showError(result.error);
    }, 150);
  });
  refs.power.addEventListener("change", async (e) => {
    const result = await post(`/api/remote-mics/${item.id}/live`, { live: e.target.checked });
    if (result && result.error) await showError(result.error);
    e.target.blur(); // a fókusz elengedése után a lekérdezés a valós állapotot mutatja
    await pollMixer();
  });
  return strip;
}

function updateRemoteStrip(strip, item) {
  const { root, refs, meterId } = strip;
  const [tone, text] = REMOTE_MIC_STATE[item.state] || ["off", item.state];
  const onAir = item.state === "on_air";
  refs.title.textContent = item.name;
  refs.host.textContent = item.host;
  refs.meter.setAttribute("aria-label", item.name + " jelszintje");
  refs.fader.setAttribute("aria-label", item.name + " hangereje");
  root.classList.toggle("is-onair", onAir);
  refs.onair.hidden = !onAir;
  refs.state.hidden = onAir;
  refs.state.dataset.tone = tone;
  refs.state.textContent = text;
  if (document.activeElement !== refs.power) refs.power.checked = item.live;
  refs.powerLabel.textContent = item.live ? "Adás leállítása" : "Adás indítása";
  renderMeter(meterId, item.level || 0);
  if (document.activeElement !== refs.fader && !strip.debounce) {
    refs.fader.value = Math.round(item.volume * 100);
    setSliderFill(refs.fader);
  }
  refs.value.textContent = refs.fader.value + "%";
}

function renderRemoteStrips(items) {
  const ids = items.map((item) => item.id).join("|");
  if (ids !== remoteStripIds) {
    remoteStripIds = ids;
    const host = document.getElementById("mixerStrips");
    const master = document.getElementById("stripMaster");
    for (const strip of remoteStrips.values()) {
      clearTimeout(strip.debounce);
      host.removeChild(strip.root);
    }
    remoteStrips.clear();
    for (const item of items) {
      const strip = createRemoteStrip(item);
      remoteStrips.set(item.id, strip);
      host.insertBefore(strip.root, master);
    }
    host.style.setProperty("--strip-count", String(4 + items.length));
    host.classList.toggle("has-remote", items.length > 0);
  }
  for (const item of items) updateRemoteStrip(remoteStrips.get(item.id), item);
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
  const remoteMics = Array.isArray(data.remote_mics) ? data.remote_mics : [];
  document.getElementById("onAirBadge").hidden = !data.mic_live && !remoteMics.some((item) => item.state === "on_air");
  renderRemoteStrips(remoteMics);

  const flags = data.flags || [];

  // Csatornameroik: forrasonkent a sajat fader utan; a master a kimenet
  const levels = data.levels || {};
  renderMeter("meterBell", levels.bell || 0);
  renderMeter("meterMusic", levels.music || 0);
  renderMeter("meterMic", levels.mic || 0);

  // Csengo csatorna: 0%-on figyelmeztet, mert akkor az utemezett csengetes sem hallhato
  const bellOn = data.running && flags.includes("BELL");
  const bellMuted = data.volume && data.volume.bell === 0;
  if (bellOn) setStatus("stripBellState", "ok", "Szól");
  else if (bellMuted) setStatus("stripBellState", "warn", "Némítva");
  else setStatus("stripBellState", "off", "Csend");
  document.getElementById("bellMutedWarning").hidden = !bellMuted;
  document.getElementById("stripBell").classList.toggle("is-muted", !!bellMuted);

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
  renderMeter("meter", data.level || 0);

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
    loadRemoteMics,
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
