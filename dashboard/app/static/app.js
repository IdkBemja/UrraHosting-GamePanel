const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

function authHeaders(extra) {
  return Object.assign({ "X-CSRFToken": csrfToken }, extra || {});
}

async function apiFetch(path, options) {
  const opts = options || {};
  opts.headers = authHeaders(opts.headers);
  const response = await fetch(path, opts);
  let data = null;
  try {
    data = await response.json();
  } catch (err) {
    data = null;
  }
  return { ok: response.ok, status: response.status, data };
}

/* ---------------------------------------------------------------------- */
/* Tabs                                                                    */
/* ---------------------------------------------------------------------- */

const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
const tabPanels = new Map(
  Array.from(document.querySelectorAll(".tab-panel")).map((panel) => [panel.id, panel])
);
const tabLoaders = {};
const loadedTabs = new Set();

function activateTab(name, { focus = false } = {}) {
  for (const btn of tabButtons) {
    const isActive = btn.dataset.tab === name;
    btn.setAttribute("aria-selected", String(isActive));
    btn.tabIndex = isActive ? 0 : -1;
    if (isActive && focus) btn.focus();
  }
  for (const [id, panel] of tabPanels) {
    const isActive = id === `tab-${name}`;
    panel.hidden = !isActive;
    panel.classList.toggle("is-hidden", !isActive);
  }
  if (!loadedTabs.has(name) && tabLoaders[name]) {
    loadedTabs.add(name);
    tabLoaders[name]();
  } else if (tabLoaders[name] && (name === "activity" || name === "software" || name === "settings" || name === "backups")) {
    tabLoaders[name]();
  }
}

for (const btn of tabButtons) {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
}

document.querySelector(".tab-bar")?.addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const currentIndex = tabButtons.findIndex((b) => b.getAttribute("aria-selected") === "true");
  let nextIndex = currentIndex;
  if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabButtons.length) % tabButtons.length;
  if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabButtons.length;
  if (event.key === "Home") nextIndex = 0;
  if (event.key === "End") nextIndex = tabButtons.length - 1;
  event.preventDefault();
  activateTab(tabButtons[nextIndex].dataset.tab, { focus: true });
});

/* ---------------------------------------------------------------------- */
/* Confirm dialog                                                          */
/* ---------------------------------------------------------------------- */

const confirmDialog = document.getElementById("confirmDialog");
const confirmDialogTitle = document.getElementById("confirmDialogTitle");
const confirmDialogBody = document.getElementById("confirmDialogBody");
const confirmDialogDanger = document.getElementById("confirmDialogDanger");
const confirmDialogAccept = document.getElementById("confirmDialogAccept");
const confirmDialogCancel = document.getElementById("confirmDialogCancel");
let confirmResolver = null;
let confirmTrigger = null;

function askConfirm(title, message, dangerText) {
  confirmDialogTitle.textContent = title;
  confirmDialogBody.textContent = message;
  if (dangerText) {
    confirmDialogDanger.textContent = dangerText;
    confirmDialogDanger.classList.remove("is-hidden");
  } else {
    confirmDialogDanger.textContent = "";
    confirmDialogDanger.classList.add("is-hidden");
  }
  confirmDialog.classList.remove("is-hidden");
  confirmTrigger = document.activeElement;
  confirmDialogAccept.focus();
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

function closeConfirm(result) {
  confirmDialog.classList.add("is-hidden");
  if (confirmResolver) confirmResolver(result);
  confirmResolver = null;
  if (confirmTrigger && typeof confirmTrigger.focus === "function") confirmTrigger.focus();
}

confirmDialogAccept.addEventListener("click", () => closeConfirm(true));
confirmDialogCancel.addEventListener("click", () => closeConfirm(false));
confirmDialog.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeConfirm(false);
});

/* ---------------------------------------------------------------------- */
/* Overview / status                                                       */
/* ---------------------------------------------------------------------- */

const statusBadge = document.getElementById("statusBadge");
const containerStatus = document.getElementById("containerStatus");
const playersOnline = document.getElementById("playersOnline");
const uptimeValue = document.getElementById("uptimeValue");
const actionFeedback = document.getElementById("actionFeedback");
const commandForm = document.getElementById("commandForm");
const commandInput = document.getElementById("commandInput");
const logsBox = document.getElementById("logsBox");
const clearLogs = document.getElementById("clearLogs");
const copyLogsBtn = document.getElementById("copyLogs");
const logsSearch = document.getElementById("logsSearch");
const autoscrollToggle = document.getElementById("autoscrollToggle");

let uptimeSeconds = null;

function formatDuration(totalSeconds) {
  if (totalSeconds === null || Number.isNaN(totalSeconds)) return "--:--:--";
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const secs = String(seconds % 60).padStart(2, "0");
  return `${hours}:${minutes}:${secs}`;
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function renderUptime() {
  if (uptimeValue) uptimeValue.textContent = formatDuration(uptimeSeconds);
}

setInterval(() => {
  if (uptimeSeconds !== null) {
    uptimeSeconds += 1;
    renderUptime();
  }
}, 1000);

async function fetchStatus() {
  const { ok, data } = await apiFetch("/api/status");
  if (!ok || !data) return;

  if (statusBadge) {
    statusBadge.textContent = data.running ? "En linea" : "Desconectado";
    statusBadge.classList.toggle("online", Boolean(data.running));
    statusBadge.classList.toggle("offline", !data.running);
  }
  if (containerStatus) containerStatus.textContent = data.container_status || "unknown";
  if (playersOnline) {
    playersOnline.textContent =
      data.players_online !== null && data.players_max !== null
        ? `${data.players_online} / ${data.players_max}`
        : "- / -";
  }
  uptimeSeconds = data.running ? data.uptime_seconds : null;
  renderUptime();
}

async function loadOverview() {
  const { ok, data } = await apiFetch("/api/overview");
  if (!ok || !data) return;

  setText("ovGameFamily", data.game_family);
  setText("ovGameEdition", data.game_edition || "-");
  setText("ovServerSoftware", data.game_software);
  setText("ovGameVersion", data.game_version);
  if (data.connection) {
    const endpoint = data.connection.kind === "domain" ? `${data.connection.address}:${data.connection.port}` : data.connection.address;
    setText("ovConnectAddress", endpoint);
  }
  setText("ovCpuLimit", data.resources ? `${data.resources.game_cpu_limit} vCPU` : "-");
  setText("ovMemLimit", data.resources ? data.resources.game_memory_limit : "-");
  if (data.storage) {
    const used = formatBytes(data.storage.usage_bytes);
    const quota = data.storage.quota_bytes ? formatBytes(data.storage.quota_bytes) : "sin limite";
    setText("ovStorage", `${used} / ${quota}`);
  }
}

/* ---------------------------------------------------------------------- */
/* Live resource meters (CPU / RAM / storage)                              */
/* ---------------------------------------------------------------------- */

const resourceMeters = {
  cpu: { fill: document.getElementById("meterCpuFill"), value: document.getElementById("meterCpuValue"), status: document.getElementById("meterCpuStatus") },
  ram: { fill: document.getElementById("meterRamFill"), value: document.getElementById("meterRamValue"), status: document.getElementById("meterRamStatus") },
  storage: { fill: document.getElementById("meterStorageFill"), value: document.getElementById("meterStorageValue"), status: document.getElementById("meterStorageStatus") },
};

// Thresholds match the meter's fill/status color steps (good/warning/
// critical) - never the only signal: the status span always carries the
// word too, since warning/critical tones alone aren't reliably
// distinguishable for every reader.
function levelFor(ratio) {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return { cls: "idle", label: "Sin datos" };
  if (ratio >= 0.9) return { cls: "critical", label: "Critico" };
  if (ratio >= 0.7) return { cls: "warning", label: "Alto" };
  return { cls: "good", label: "Normal" };
}

function setMeter(meter, ratio, valueText) {
  if (!meter.fill) return;
  const level = levelFor(ratio);
  const width = ratio === null || ratio === undefined || Number.isNaN(ratio) ? 0 : Math.min(100, Math.max(0, ratio * 100));
  meter.fill.className = `meter-fill level-${level.cls}`;
  meter.fill.style.width = `${width}%`;
  meter.value.textContent = valueText;
  meter.status.className = `meter-status level-${level.cls}`;
  meter.status.textContent = ratio === null || ratio === undefined || Number.isNaN(ratio) ? level.label : `${level.label} (${Math.round(ratio * 100)}%)`;
}

async function loadResources() {
  const { ok, data } = await apiFetch("/api/resources");
  if (!ok || !data) return;

  if (!data.running) {
    setMeter(resourceMeters.cpu, null, "Servidor detenido");
    setMeter(resourceMeters.ram, null, "Servidor detenido");
  } else {
    // cpu_percent is 100 per fully-saturated core (docker stats' own
    // convention), so a 2-vCPU limit tops out at 200%, not 100% - divide by
    // limit*100 to get the 0-1 ratio the meter needs.
    const cpuLimitPercent = data.cpu_limit_vcpu ? data.cpu_limit_vcpu * 100 : null;
    const cpuRatio = cpuLimitPercent && typeof data.cpu_percent === "number" ? data.cpu_percent / cpuLimitPercent : null;
    const cpuText = typeof data.cpu_percent === "number" ? `${data.cpu_percent.toFixed(1)}% / ${data.cpu_limit_vcpu} vCPU` : "-";
    setMeter(resourceMeters.cpu, cpuRatio, cpuText);

    const ramRatio = data.memory_limit_bytes && typeof data.memory_usage_bytes === "number" ? data.memory_usage_bytes / data.memory_limit_bytes : null;
    const ramText = typeof data.memory_usage_bytes === "number" ? `${formatBytes(data.memory_usage_bytes)} / ${formatBytes(data.memory_limit_bytes)}` : "-";
    setMeter(resourceMeters.ram, ramRatio, ramText);
  }

  const storageRatio = data.storage_quota_bytes ? data.storage_usage_bytes / data.storage_quota_bytes : null;
  const storageLimitText = data.storage_quota_bytes ? formatBytes(data.storage_quota_bytes) : "sin limite";
  setMeter(resourceMeters.storage, storageRatio, `${formatBytes(data.storage_usage_bytes)} / ${storageLimitText}`);
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value === null || value === undefined || value === "" ? "-" : String(value);
}

async function performAction(action) {
  if (actionFeedback) {
    actionFeedback.textContent = `Ejecutando accion: ${action}...`;
    actionFeedback.classList.remove("error", "ok");
  }

  if (action === "stop" || action === "restart") {
    const confirmed = await askConfirm(
      action === "stop" ? "Detener servidor" : "Reiniciar servidor",
      action === "stop"
        ? "El servidor guardara el mundo y se detendra. Los jugadores conectados seran desconectados."
        : "El servidor se detendra y volvera a iniciar. Los jugadores conectados seran desconectados brevemente."
    );
    if (!confirmed) {
      if (actionFeedback) actionFeedback.textContent = "";
      return;
    }
  }

  const { ok, data } = await apiFetch("/api/container", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });

  if (actionFeedback) {
    actionFeedback.textContent = ok ? "Accion completada" : (data && data.error) || "Error al ejecutar la accion";
    actionFeedback.classList.toggle("ok", ok);
    actionFeedback.classList.toggle("error", !ok);
  }
  await fetchStatus();
  await loadOverview();
}

for (const button of document.querySelectorAll("[data-action]")) {
  button.addEventListener("click", () => performAction(button.dataset.action));
}

/* ---------------------------------------------------------------------- */
/* Console                                                                 */
/* ---------------------------------------------------------------------- */

async function sendCommand(event) {
  event.preventDefault();
  const command = commandInput.value.trim();
  if (!command) return;

  appendLogLine(`[cmd] ${command}`);
  const { ok, data } = await apiFetch("/api/command", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command }),
  });
  if (data && data.submitted) {
    appendLogLine("[control] comando enviado (sin respuesta directa para este juego)");
  } else {
    appendLogLine(`[control] ${(data && (data.output || data.error)) || "Sin salida"}`);
  }
  if (!ok) appendLogLine("[control] la respuesta anterior indica un error");
  commandInput.value = "";
  commandInput.focus();
}

function appendLogLine(line) {
  const atBottom = logsBox.scrollHeight - logsBox.scrollTop - logsBox.clientHeight < 30;
  const lineEl = document.createElement("div");
  lineEl.textContent = line;
  lineEl.dataset.logLine = "1";
  logsBox.appendChild(lineEl);
  applySearchFilter();
  if (autoscrollToggle.checked && atBottom) {
    logsBox.scrollTop = logsBox.scrollHeight;
  }
}

function applySearchFilter() {
  const term = logsSearch.value.trim().toLowerCase();
  for (const lineEl of logsBox.querySelectorAll("[data-log-line]")) {
    const text = lineEl.textContent;
    const matches = !term || text.toLowerCase().includes(term);
    lineEl.style.display = matches ? "" : "none";
  }
}

logsSearch?.addEventListener("input", applySearchFilter);

copyLogsBtn?.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(logsBox.textContent);
    copyLogsBtn.textContent = "Copiado!";
    setTimeout(() => (copyLogsBtn.textContent = "Copiar"), 1500);
  } catch (err) {
    copyLogsBtn.textContent = "No se pudo copiar";
    setTimeout(() => (copyLogsBtn.textContent = "Copiar"), 1500);
  }
});

function initLogsStream() {
  const source = new EventSource("/api/logs/stream");
  source.onmessage = (event) => appendLogLine(event.data);
  source.onerror = () => {
    appendLogLine("[stream] reconectando logs...");
    source.close();
    setTimeout(initLogsStream, 2500);
  };
}

if (commandForm) commandForm.addEventListener("submit", sendCommand);
if (clearLogs && logsBox) {
  clearLogs.addEventListener("click", () => {
    logsBox.textContent = "";
  });
}

/* ---------------------------------------------------------------------- */
/* Files                                                                    */
/* ---------------------------------------------------------------------- */

const fileCategory = document.getElementById("fileCategory");
const filePathBreadcrumb = document.getElementById("filePathBreadcrumb");
const fileQuota = document.getElementById("fileQuota");
const fileTableBody = document.getElementById("fileTableBody");
const filesEmptyState = document.getElementById("filesEmptyState");
const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const uploadProgressWrap = document.getElementById("uploadProgressWrap");
const uploadProgress = document.getElementById("uploadProgress");
const uploadProgressLabel = document.getElementById("uploadProgressLabel");
const filesFeedback = document.getElementById("filesFeedback");
const fileShortcuts = document.getElementById("fileShortcuts");

let currentFilePath = "";
let fileCategoriesLoaded = false;

async function ensureFileCategories() {
  if (fileCategoriesLoaded) return;
  const { ok, data } = await apiFetch("/api/files");
  if (!ok || !data) return;
  fileCategory.innerHTML = "";
  for (const category of data.categories) {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    fileCategory.appendChild(option);
  }
  fileCategoriesLoaded = true;
}

// `data.shortcuts` (see files.py/storage.py's shadowed_categories()) lists
// category names hidden from this exact listing because they'd otherwise
// collide, by name only, with a disconnected directory here (e.g. "mods"
// while browsing game/ - a DIFFERENT folder from the real "mods" category,
// see storage.py's docstring for why). Rather than just leaving admins to
// wonder where mods/plugins actually live, offer a one-click jump straight
// to the real category.
function renderFileShortcuts(shortcuts) {
  fileShortcuts.innerHTML = "";
  fileShortcuts.classList.toggle("is-hidden", shortcuts.length === 0);
  if (!shortcuts.length) return;

  const label = document.createElement("span");
  label.className = "file-shortcuts-label";
  label.textContent = shortcuts.length > 1 ? "Ir directamente a:" : "Ir directamente a la categoria:";
  fileShortcuts.appendChild(label);

  for (const name of shortcuts) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "file-shortcut-pill";
    button.innerHTML = `<i class="bi bi-box-arrow-up-right"></i> ${name}`;
    button.addEventListener("click", () => {
      fileCategory.value = name;
      currentFilePath = "";
      loadFiles();
    });
    fileShortcuts.appendChild(button);
  }
}

async function loadFiles() {
  await ensureFileCategories();
  const category = fileCategory.value;
  if (!category) return;
  const params = new URLSearchParams({ path: currentFilePath });
  const { ok, data } = await apiFetch(`/api/files/${category}?${params.toString()}`);
  if (!ok || !data) return;

  filePathBreadcrumb.textContent = `/${category}/${currentFilePath}`;
  fileQuota.textContent =
    data.quota_bytes != null ? `${formatBytes(data.usage_bytes)} / ${formatBytes(data.quota_bytes)}` : "";

  renderFileShortcuts(data.shortcuts || []);

  fileTableBody.innerHTML = "";
  filesEmptyState.classList.toggle("is-hidden", data.entries.length > 0);

  if (currentFilePath) {
    const upRow = document.createElement("tr");
    const upCell = document.createElement("td");
    upCell.colSpan = 4;
    const upLink = document.createElement("button");
    upLink.className = "link-like";
    upLink.type = "button";
    upLink.textContent = ".. (subir un nivel)";
    upLink.addEventListener("click", () => {
      const parts = currentFilePath.split("/").filter(Boolean);
      parts.pop();
      currentFilePath = parts.join("/");
      loadFiles();
    });
    upCell.appendChild(upLink);
    upRow.appendChild(upCell);
    fileTableBody.appendChild(upRow);
  }

  for (const entry of data.entries) {
    const row = document.createElement("tr");

    const nameCell = document.createElement("td");
    if (entry.is_dir) {
      const link = document.createElement("button");
      link.className = "link-like";
      link.type = "button";
      link.textContent = `${entry.name}/`;
      link.addEventListener("click", () => {
        currentFilePath = entry.path;
        loadFiles();
      });
      nameCell.appendChild(link);
    } else {
      nameCell.textContent = entry.name;
    }
    row.appendChild(nameCell);

    const sizeCell = document.createElement("td");
    sizeCell.textContent = entry.is_dir ? "-" : formatBytes(entry.size);
    row.appendChild(sizeCell);

    const modifiedCell = document.createElement("td");
    modifiedCell.textContent = new Date(entry.modified_at * 1000).toLocaleString();
    row.appendChild(modifiedCell);

    const actionsCell = document.createElement("td");
    actionsCell.className = "row-actions";
    if (!entry.is_dir) {
      const downloadLink = document.createElement("a");
      downloadLink.className = "btn ghost";
      downloadLink.href = `/api/files/${category}/download?${new URLSearchParams({ path: entry.path })}`;
      downloadLink.textContent = "Descargar";
      actionsCell.appendChild(downloadLink);
    }
    if (entry.editable) {
      const editBtn = document.createElement("button");
      editBtn.className = "btn ghost";
      editBtn.type = "button";
      editBtn.textContent = "Editar";
      editBtn.addEventListener("click", () => openFileEditor(category, entry.path, entry.name));
      actionsCell.appendChild(editBtn);
    }
    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn ghost";
    deleteBtn.type = "button";
    deleteBtn.textContent = "Eliminar";
    deleteBtn.addEventListener("click", () => deleteFile(category, entry.path, entry.name));
    actionsCell.appendChild(deleteBtn);
    row.appendChild(actionsCell);

    fileTableBody.appendChild(row);
  }
}

async function deleteFile(category, path, name) {
  const confirmed = await askConfirm("Eliminar archivo", `Se eliminara "${name}" de forma permanente. ¿Continuar?`);
  if (!confirmed) return;
  const { ok, data } = await apiFetch(`/api/files/${category}/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!ok) {
    window.alert((data && data.error) || "No se pudo eliminar el archivo");
  }
  loadFiles();
}

/* ---------------------------------------------------------------------- */
/* Inline text editor (mods/plugins/game/config only - see entry.editable, */
/* computed server-side by InstanceStorage.is_editable_path())             */
/* ---------------------------------------------------------------------- */

const fileEditorModal = document.getElementById("fileEditorModal");
const fileEditorTitle = document.getElementById("fileEditorTitle");
const fileEditorPath = document.getElementById("fileEditorPath");
const fileEditorTextarea = document.getElementById("fileEditorTextarea");
const fileEditorFeedback = document.getElementById("fileEditorFeedback");
const fileEditorCancel = document.getElementById("fileEditorCancel");
const fileEditorSave = document.getElementById("fileEditorSave");

let fileEditorContext = null; // { category, path } of the file currently open, or null

async function openFileEditor(category, path, name) {
  fileEditorContext = { category, path };
  fileEditorTitle.textContent = `Editar ${name}`;
  fileEditorPath.textContent = `/${category}/${path}`;
  fileEditorTextarea.value = "";
  fileEditorTextarea.disabled = true;
  fileEditorFeedback.textContent = "Cargando...";
  fileEditorFeedback.classList.remove("error", "ok");
  fileEditorModal.classList.remove("is-hidden");

  const { ok, data } = await apiFetch(`/api/files/${category}/content?${new URLSearchParams({ path })}`);
  fileEditorTextarea.disabled = false;
  if (!ok || !data) {
    fileEditorFeedback.textContent = (data && data.error) || "No se pudo cargar el archivo";
    fileEditorFeedback.classList.add("error");
    return;
  }
  fileEditorTextarea.value = data.content;
  fileEditorFeedback.textContent = "";
  fileEditorTextarea.focus();
}

function closeFileEditor() {
  fileEditorModal.classList.add("is-hidden");
  fileEditorContext = null;
}

fileEditorCancel?.addEventListener("click", closeFileEditor);
fileEditorModal?.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeFileEditor();
});

fileEditorSave?.addEventListener("click", async () => {
  if (!fileEditorContext) return;
  fileEditorSave.disabled = true;
  fileEditorFeedback.textContent = "Guardando...";
  fileEditorFeedback.classList.remove("error", "ok");

  const { ok, data } = await apiFetch(`/api/files/${fileEditorContext.category}/content`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: fileEditorContext.path, content: fileEditorTextarea.value }),
  });

  fileEditorSave.disabled = false;
  if (!ok) {
    fileEditorFeedback.textContent = (data && data.error) || "No se pudo guardar el archivo";
    fileEditorFeedback.classList.add("error");
    return;
  }
  fileEditorFeedback.textContent = "Guardado correctamente.";
  fileEditorFeedback.classList.add("ok");
  loadFiles();
});

// Resolves once this ONE file is done (uploaded, skipped by the user on an
// overwrite prompt, or failed) - never rejects, so uploadFiles() below can
// always finish a whole batch instead of aborting it on the first error.
function uploadOneFile(category, file) {
  return new Promise((resolve) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("path", currentFilePath);

    const doUpload = (overwrite) => {
      if (overwrite) formData.set("overwrite", "true");
      uploadProgress.value = 0;

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/files/${category}/upload`);
      xhr.setRequestHeader("X-CSRFToken", csrfToken);
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) uploadProgress.value = Math.round((event.loaded / event.total) * 100);
      });
      xhr.onload = async () => {
        // The backend always returns JSON on /api/* now (see main.py's
        // global error handlers) - a parse failure here means something
        // outside Flask entirely (a proxy, a truncated response).
        let payload = {};
        try {
          payload = JSON.parse(xhr.responseText);
        } catch (err) {
          payload = {};
        }
        if (xhr.status === 400 && /ya existe/i.test(payload.error || "")) {
          const confirmed = await askConfirm("Sobrescribir archivo", `"${file.name}" ya existe. ¿Sobrescribir?`);
          if (confirmed) {
            doUpload(true);
            return;
          }
          resolve({ name: file.name, ok: false, skipped: true });
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve({ name: file.name, ok: true });
        } else {
          resolve({ name: file.name, ok: false, error: payload.error || `El servidor respondio ${xhr.status}` });
        }
      };
      xhr.onerror = () => resolve({ name: file.name, ok: false, error: "Error de red durante la subida" });
      xhr.send(formData);
    };

    doUpload(false);
  });
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length || !fileCategory) return;
  const category = fileCategory.value;

  uploadProgressWrap.classList.remove("is-hidden");
  filesFeedback.textContent = "";
  filesFeedback.classList.remove("error", "ok");

  // Sequential, not parallel: keeps the single progress bar meaningful
  // (one file's real upload progress at a time instead of N interleaved
  // ones) and avoids hammering the server with a burst of large
  // concurrent uploads for a big mod pack.
  const results = [];
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    uploadProgressLabel.textContent = files.length > 1 ? `Subiendo ${file.name} (${i + 1}/${files.length})...` : `Subiendo ${file.name}...`;
    results.push(await uploadOneFile(category, file));
  }
  uploadProgressWrap.classList.add("is-hidden");

  const succeeded = results.filter((r) => r.ok);
  const failed = results.filter((r) => !r.ok && !r.skipped);

  if (failed.length) {
    const summary =
      files.length === 1
        ? failed[0].error
        : `${succeeded.length} de ${files.length} archivo(s) subidos. Fallaron: ${failed.map((r) => `${r.name} (${r.error})`).join(", ")}`;
    filesFeedback.textContent = summary;
    filesFeedback.classList.add("error");
  } else if (succeeded.length) {
    filesFeedback.textContent = files.length > 1 ? `${succeeded.length} archivo(s) subidos correctamente.` : "Archivo subido correctamente.";
    filesFeedback.classList.add("ok");
  }

  loadFiles();
}

fileCategory?.addEventListener("change", () => {
  currentFilePath = "";
  loadFiles();
});

fileInput?.addEventListener("change", () => {
  uploadFiles(fileInput.files);
  fileInput.value = "";
});

dropZone?.addEventListener("click", () => fileInput.click());
dropZone?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});
dropZone?.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("is-dragover");
});
dropZone?.addEventListener("dragleave", () => dropZone.classList.remove("is-dragover"));
dropZone?.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("is-dragover");
  uploadFiles(event.dataTransfer.files);
});

/* ---------------------------------------------------------------------- */
/* Software / catalog: Juego -> Edicion -> Software -> Version wizard       */
/* ---------------------------------------------------------------------- */

const catalogSearch = document.getElementById("catalogSearch");
const catalogChannel = document.getElementById("catalogChannel");
const installForm = document.getElementById("installForm");
const catalogVersion = document.getElementById("catalogVersion");
const catalogVersionFilter = document.getElementById("catalogVersionFilter");
const catalogVersionHint = document.getElementById("catalogVersionHint");
let allCatalogVersions = [];
const catalogRestart = document.getElementById("catalogRestart");
const catalogBackup = document.getElementById("catalogBackup");
const installButton = document.getElementById("installButton");
const rollbackButton = document.getElementById("rollbackButton");
const installFeedback = document.getElementById("installFeedback");
const gameCards = document.getElementById("gameCards");
const editionWrap = document.getElementById("editionWrap");
const editionCards = document.getElementById("editionCards");
const softwareCards = document.getElementById("softwareCards");

let catalogTree = [];
let currentGameFamily = null;
let currentGameEdition = null;
let selectedGame = null;
let selectedEdition = null;
let selectedSoftware = null;

function gameEntry(family) {
  return catalogTree.find((g) => g.family === family) || null;
}

function editionEntry(family, edition) {
  const game = gameEntry(family);
  if (!game) return null;
  return game.editions.find((e) => e.edition === (edition || "")) || null;
}

// Official icons for each selectable game/software, vendored locally under
// static/icons/ (the dashboard's CSP is img-src 'self' only - no external
// CDNs - so these are downloaded once and served like any other static
// asset, never hotlinked). Keyed the same way as the backend's adapter_id
// (plan.md section 4: "minecraft-java/vanilla", "terraria/vanilla", ...) so
// "vanilla" the Minecraft distribution and "vanilla" the Terraria one never
// collide. Bukkit has no actively maintained distinct site of its own
// (dev.bukkit.org now just redirects into CurseForge) so it reuses
// Spigot's icon - same project family, not a placeholder.
// app.js is loaded from /static/app.js, but relative URLs inside it resolve
// against the current PAGE's URL (/dashboard), not the script's own path -
// so this needs to be an absolute /static/... prefix, matching Flask's
// default static url path (same one url_for('static', ...) produces in the
// templates).
const STATIC_ICON_BASE = "/static/";
const GAME_ICON_PATHS = {
  minecraft: "icons/minecraft.png",
  terraria: "icons/terraria.ico",
};
const SOFTWARE_ICON_PATHS = {
  "minecraft-java/vanilla": "icons/minecraft.png",
  "minecraft-java/paper": "icons/paper.ico",
  "minecraft-java/purpur": "icons/purpur.ico",
  "minecraft-java/spigot": "icons/spigot.ico",
  "minecraft-java/bukkit": "icons/spigot.ico",
  "minecraft-java/fabric": "icons/fabric.png",
  "minecraft-java/forge": "icons/forge.ico",
  "minecraft-java/neoforge": "icons/neoforge.ico",
  "minecraft-bedrock/bedrock": "icons/minecraft.png",
  "terraria/vanilla": "icons/terraria.ico",
  "terraria/tmodloader": "icons/tmodloader.png",
};

function adapterKey(family, edition, software) {
  const prefix = edition ? `${family}-${edition}` : family;
  return `${prefix}/${software}`;
}

function renderCardGrid(container, items, selectedValue, valueKey, labelKey, onSelect, iconResolver) {
  container.innerHTML = "";
  for (const item of items) {
    const card = document.createElement("button");
    card.className = "software-card";
    card.type = "button";
    card.dataset.value = item[valueKey];
    card.setAttribute("role", "option");
    const selected = item[valueKey] === selectedValue;
    card.setAttribute("aria-selected", String(selected));
    card.classList.toggle("is-selected", selected);

    const iconPath = iconResolver ? iconResolver(item) : null;
    const iconHtml = iconPath
      ? `<img class="software-card-icon" src="${STATIC_ICON_BASE}${iconPath}" alt="" width="32" height="32" loading="lazy">`
      : `<i class="bi bi-box-fill"></i>`;
    card.innerHTML = `${iconHtml}<strong>${item[labelKey]}</strong>`;
    card.addEventListener("click", () => onSelect(item));
    container.appendChild(card);
  }
}

function resetInstallStep() {
  installForm.classList.add("is-hidden");
  installFeedback.textContent = "Elige juego, edicion y software, luego pulsa buscar.";
  installFeedback.classList.remove("error", "ok");
}

function selectGame(family) {
  selectedGame = family;
  const game = gameEntry(family);
  renderCardGrid(
    gameCards, catalogTree, selectedGame, "family", "label",
    (item) => selectGame(item.family),
    (item) => GAME_ICON_PATHS[item.family] || null
  );

  const editions = game ? game.editions : [];
  const skipEditionStep = editions.length === 1 && editions[0].edition === "";
  editionWrap.classList.toggle("is-hidden", skipEditionStep);
  selectEdition(skipEditionStep ? "" : editions[0]?.edition ?? "");
}

function selectEdition(edition) {
  selectedEdition = edition;
  const game = gameEntry(selectedGame);
  const editions = game ? game.editions : [];
  renderCardGrid(
    editionCards, editions, selectedEdition, "edition", "label",
    (item) => selectEdition(item.edition),
    (item) => GAME_ICON_PATHS[selectedGame] || null
  );

  const entry = editionEntry(selectedGame, selectedEdition);
  const options = entry ? entry.software.map((s) => ({ software: s, label: s })) : [];
  selectSoftware(options[0]?.software ?? null, options);
}

function selectSoftware(software, options) {
  selectedSoftware = software;
  renderCardGrid(
    softwareCards, options, selectedSoftware, "software", "label",
    (item) => selectSoftware(item.software, options),
    (item) => SOFTWARE_ICON_PATHS[adapterKey(selectedGame, selectedEdition, item.software)] || null
  );
  resetInstallStep();
}

async function loadCatalogTree() {
  // /api/config is identity-only (no docker status, no storage usage scan)
  // - /api/overview recomputes disk usage by walking every file under
  // game/ on every call, which can take a long time for software that
  // ships thousands of small files (Bedrock, tModLoader). The Software tab
  // only ever needs to know which game/edition/software is current.
  const [{ data: gamesData }, { data: configData }] = await Promise.all([apiFetch("/api/catalog/games"), apiFetch("/api/config")]);
  catalogTree = (gamesData && gamesData.games) || [];
  if (configData) {
    currentGameFamily = configData.game_family;
    currentGameEdition = configData.game_edition || "";
    setText("swCurrent", `${configData.game_family}${configData.game_edition ? "/" + configData.game_edition : ""} - ${configData.game_software}`);
  }
  selectGame(currentGameFamily || (catalogTree[0] && catalogTree[0].family));
}

function renderCatalogVersionOptions(filterText) {
  const query = (filterText || "").trim().toLowerCase();
  const matches = query ? allCatalogVersions.filter((version) => version.toLowerCase().includes(query)) : allCatalogVersions;

  catalogVersion.innerHTML = "";
  for (const version of matches) {
    const option = document.createElement("option");
    option.value = version;
    option.textContent = version;
    catalogVersion.appendChild(option);
  }

  if (catalogVersionHint) {
    catalogVersionHint.textContent = query
      ? `${matches.length} de ${allCatalogVersions.length} version(es) coinciden con "${filterText.trim()}".`
      : "";
  }
}

async function loadCatalogVersions() {
  if (!catalogVersion || !selectedGame || !selectedSoftware) return;
  const editionSegment = selectedEdition || "-";
  const channel = catalogChannel.value;

  allCatalogVersions = [];
  catalogVersion.innerHTML = "";
  if (catalogVersionFilter) catalogVersionFilter.value = "";
  if (catalogVersionHint) catalogVersionHint.textContent = "";
  installFeedback.textContent = "Buscando versiones disponibles...";
  installFeedback.classList.remove("error", "ok");
  catalogSearch.disabled = true;
  const { ok, data } = await apiFetch(`/api/catalog/${selectedGame}/${editionSegment}/${selectedSoftware}/versions?channel=${channel}`);
  catalogSearch.disabled = false;
  if (!ok || !data) {
    installFeedback.textContent = (data && data.error) || "No se pudieron cargar las versiones.";
    installFeedback.classList.add("error");
    return;
  }
  allCatalogVersions = data.versions;
  renderCatalogVersionOptions("");
  installForm.classList.remove("is-hidden");
  installFeedback.textContent = `${data.versions.length} version(es) disponibles para ${selectedSoftware} (${channel}).`;
  installFeedback.classList.add("ok");
}

catalogVersionFilter?.addEventListener("input", () => renderCatalogVersionOptions(catalogVersionFilter.value));

/* ---------------------------------------------------------------------- */
/* Install progress modal                                                  */
/* ---------------------------------------------------------------------- */

const installProgressModal = document.getElementById("installProgressModal");
const installProgressTitle = document.getElementById("installProgressTitle");
const installProgressMessage = document.getElementById("installProgressMessage");
const installProgressBar = document.getElementById("installProgressBar");
const installProgressClose = document.getElementById("installProgressClose");

const INSTALL_PHASE_LABELS = {
  preparing: "Preparando instalacion...",
  resolving: "Buscando la descarga...",
  stopping: "Deteniendo el servidor actual...",
  downloading: "Descargando...",
  snapshotting: "Creando copia de seguridad...",
  extracting: "Extrayendo archivos...",
  merging: "Fusionando con la instalacion anterior...",
  placing: "Colocando archivos...",
  compiling: "Compilando con BuildTools...",
  running_installer: "Ejecutando el instalador oficial...",
  writing_config: "Guardando configuracion...",
  restarting: "Reiniciando la instancia...",
  rolling_back: "Restaurando la copia de seguridad...",
};

let installProgressTimer = null;

function setInstallProgressBar(current, total) {
  if (typeof current === "number" && typeof total === "number" && total > 0) {
    installProgressBar.classList.remove("is-indeterminate");
    installProgressBar.style.width = `${Math.min(100, Math.round((current / total) * 100))}%`;
  } else {
    installProgressBar.classList.add("is-indeterminate");
    installProgressBar.style.width = "";
  }
}

function showInstallProgressModal(title) {
  installProgressTitle.textContent = title;
  installProgressMessage.textContent = "Preparando instalacion...";
  installProgressClose.classList.add("is-hidden");
  setInstallProgressBar(null, null);
  installProgressModal.classList.remove("is-hidden");

  const poll = async () => {
    const { ok, data } = await apiFetch("/api/catalog/install/status");
    if (!ok || !data) return;
    const label = INSTALL_PHASE_LABELS[data.phase] || data.message || "Procesando...";
    installProgressMessage.textContent = data.message || label;
    setInstallProgressBar(data.current, data.total);
  };
  poll();
  installProgressTimer = window.setInterval(poll, 800);
}

function hideInstallProgressModal({ ok, message } = {}) {
  if (installProgressTimer) {
    window.clearInterval(installProgressTimer);
    installProgressTimer = null;
  }
  if (message) {
    installProgressTitle.textContent = ok ? "Instalacion completada" : "La instalacion fallo";
    installProgressMessage.textContent = message;
    setInstallProgressBar(ok ? 1 : null, ok ? 1 : null);
    installProgressClose.classList.remove("is-hidden");
  } else {
    installProgressModal.classList.add("is-hidden");
  }
}

installProgressClose?.addEventListener("click", () => installProgressModal.classList.add("is-hidden"));

/* ---------------------------------------------------------------------- */
/* Crash report modal                                                      */
/* ---------------------------------------------------------------------- */

// Shown instead of raw backend error text for the install/rollback flow
// (installer.py can surface real subprocess output/stack traces there,
// e.g. a NeoForge OutOfMemoryError trace - useful for support, not for an
// end user). Other feedback spots in this file (file manager, users,
// backups) keep their own short, actionable messages as-is; those are
// normal validation errors, not the kind of technical detail this dialog
// is for.
const GENERIC_ERROR_MESSAGE = "No se pudo completar la operacion. Descarga el registro o contacta a soporte si el problema persiste.";

const crashModal = document.getElementById("crashModal");
const crashModalDownload = document.getElementById("crashModalDownload");
const crashModalClose = document.getElementById("crashModalClose");
let crashModalTrigger = null;
let lastCrashLog = "";

function pad2(value) {
  return String(value).padStart(2, "0");
}

function crashLogFilename(now) {
  const stamp = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}_${pad2(now.getHours())}-${pad2(now.getMinutes())}-${pad2(now.getSeconds())}`;
  return `GamePanel_CrashReport_${stamp}.log`;
}

function showCrashModal(context, detail) {
  const now = new Date();
  lastCrashLog = [
    "UrraHosting GamePanel - Reporte de error",
    `Fecha: ${now.toISOString()}`,
    `Contexto: ${context}`,
    `URL: ${window.location.href}`,
    "",
    "Detalle:",
    detail || "(sin detalle adicional)",
  ].join("\n");
  lastCrashLog += `\n\n-- fin del reporte, filename sugerido: ${crashLogFilename(now)} --`;

  if (!crashModal) return;
  crashModalTrigger = document.activeElement;
  crashModal.classList.remove("is-hidden");
  crashModalClose?.focus();
}

function closeCrashModal() {
  crashModal?.classList.add("is-hidden");
  if (crashModalTrigger && typeof crashModalTrigger.focus === "function") crashModalTrigger.focus();
  crashModalTrigger = null;
}

crashModalDownload?.addEventListener("click", () => {
  const blob = new Blob([lastCrashLog], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = crashLogFilename(new Date());
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
});

crashModalClose?.addEventListener("click", closeCrashModal);
crashModal?.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeCrashModal();
});

async function loadInstallationDetails() {
  const { ok, data } = await apiFetch("/api/catalog/installation");
  if (!ok || !data) return;
  const installation = data.installation;
  setText("instServerSoftware", installation ? installation.game_software : "sin instalar aun");
  setText("instVersion", installation ? installation.game_version : "-");
  setText("instChannel", installation ? installation.channel : "-");
  setText("instChecksum", installation && installation.checksum ? installation.checksum.slice(0, 16) + "..." : "-");
  setText("instDate", installation ? installation.installed_at : "-");
}

catalogSearch?.addEventListener("click", loadCatalogVersions);

installButton?.addEventListener("click", async () => {
  const version = catalogVersion.value;
  if (!version || !selectedGame || !selectedSoftware) return;
  const channel = catalogChannel.value;
  const isReprovision = selectedGame !== currentGameFamily || (selectedEdition || "") !== (currentGameEdition || "");

  const backupText = catalogBackup.checked
    ? "Se creara una copia de seguridad previa (usa Revertir si algo falla). "
    : "No se creara ninguna copia de seguridad: los datos actuales se borraran de forma permanente y no podras revertir esta instalacion. ";
  const confirmed = await askConfirm(
    isReprovision ? "Reconfigurar el servidor" : "Instalar servidor",
    (isReprovision
      ? `Esto DETENDRA el servidor actual (${currentGameFamily}) y reconfigurara la instancia para ejecutar ${selectedGame}${selectedEdition ? "/" + selectedEdition : ""} (${selectedSoftware} ${version}, ${channel}). `
      : `Se descargara ${selectedSoftware} ${version} (${channel}) y reemplazara la instalacion activa con una instalacion limpia. `) +
      backupText +
      (catalogRestart.checked || isReprovision ? "El servidor se reiniciara automaticamente al terminar." : ""),
    "Los datos existentes se perderán."
  );
  if (!confirmed) return;

  installFeedback.textContent = "Instalando, esto puede tardar varios minutos...";
  installFeedback.classList.remove("error", "ok");
  installButton.disabled = true;
  showInstallProgressModal(isReprovision ? "Reconfigurando el servidor" : "Instalando servidor");

  const { ok, data } = await apiFetch("/api/catalog/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      game_family: selectedGame,
      game_edition: selectedEdition,
      software: selectedSoftware,
      version,
      channel,
      restart: catalogRestart.checked,
      backup: catalogBackup.checked,
    }),
  });

  installButton.disabled = false;
  const warnings = ok && data && data.warnings ? data.warnings : [];

  if (ok) {
    const message = warnings.length ? `Instalado correctamente. Advertencia: ${warnings.join(" ")}` : "Instalado correctamente.";
    installFeedback.textContent = message;
    installFeedback.classList.add("ok");
    installFeedback.classList.remove("error");
    hideInstallProgressModal({ ok: true, message });
    currentGameFamily = selectedGame;
    currentGameEdition = selectedEdition;
  } else {
    installFeedback.textContent = GENERIC_ERROR_MESSAGE;
    installFeedback.classList.add("error");
    installFeedback.classList.remove("ok");
    hideInstallProgressModal();
    showCrashModal(
      `Instalacion de software (${selectedGame}${selectedEdition ? "/" + selectedEdition : ""} - ${selectedSoftware} ${version}, canal ${channel})`,
      (data && data.error) || "No se pudo instalar"
    );
  }
  loadInstallationDetails();
  loadOverview();
});

rollbackButton?.addEventListener("click", async () => {
  const confirmed = await askConfirm("Revertir instalacion", "Se restaurara la copia previa a la ultima instalacion. Esta accion reemplazara el contenido actual de game/.");
  if (!confirmed) return;
  showInstallProgressModal("Revirtiendo instalacion");
  const { ok, data } = await apiFetch("/api/catalog/install/rollback", { method: "POST" });
  if (!ok) {
    installFeedback.textContent = GENERIC_ERROR_MESSAGE;
    installFeedback.classList.add("error");
    installFeedback.classList.remove("ok");
    hideInstallProgressModal();
    showCrashModal("Revertir instalacion", (data && data.error) || "No se pudo revertir");
    loadInstallationDetails();
    loadOverview();
    return;
  }
  const message = "Instalacion revertida.";
  installFeedback.textContent = message;
  installFeedback.classList.toggle("ok", ok);
  installFeedback.classList.toggle("error", !ok);
  hideInstallProgressModal({ ok, message });
  loadInstallationDetails();
  loadOverview();
});

/* ---------------------------------------------------------------------- */
/* Configuracion (curated safe settings)                                   */
/* ---------------------------------------------------------------------- */

const settingsForm = document.getElementById("settingsForm");
const settingsFeedback = document.getElementById("settingsFeedback");
const settingsRestartBanner = document.getElementById("settingsRestartBanner");
const settingsRestartNow = document.getElementById("settingsRestartNow");

// The backend (dashboard/app/blueprints/settings.py) decides exactly which
// fields apply to the current game/edition and their type/options/bounds -
// this only renders whatever schema it returns, so a new adapter or a new
// safe setting never needs a matching change here.
function buildSettingsField(field) {
  const wrap = document.createElement("div");
  wrap.className = field.type === "bool" ? "settings-field settings-field-bool" : "settings-field";
  const id = `setting-${field.key}`;

  let input;
  if (field.type === "enum") {
    input = document.createElement("select");
    for (const option of field.options) {
      const opt = document.createElement("option");
      opt.value = option;
      opt.textContent = option;
      opt.selected = option === field.value;
      input.appendChild(opt);
    }
  } else if (field.type === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(field.value);
  } else if (field.type === "int") {
    input = document.createElement("input");
    input.type = "number";
    if (typeof field.min === "number") input.min = String(field.min);
    if (typeof field.max === "number") input.max = String(field.max);
    input.value = field.value;
  } else {
    input = document.createElement("input");
    input.type = "text";
    if (field.max_length) input.maxLength = field.max_length;
    input.value = field.value;
  }
  input.id = id;
  input.dataset.key = field.key;
  input.dataset.type = field.type;

  const label = document.createElement("label");
  label.setAttribute("for", id);
  label.textContent = field.label;

  if (field.type === "bool") {
    wrap.append(input, label);
  } else {
    wrap.append(label, input);
  }
  return wrap;
}

async function loadSettings() {
  if (!settingsForm) return;
  settingsRestartBanner.classList.add("is-hidden");
  const { ok, data } = await apiFetch("/api/settings");
  if (!ok || !data) return;

  settingsForm.innerHTML = "";
  for (const field of data.settings) {
    settingsForm.appendChild(buildSettingsField(field));
  }

  if (data.settings.length) {
    const actions = document.createElement("div");
    actions.className = "settings-actions";
    const saveBtn = document.createElement("button");
    saveBtn.type = "submit";
    saveBtn.className = "btn";
    saveBtn.innerHTML = '<i class="bi bi-save2-fill"></i> Guardar cambios';
    actions.appendChild(saveBtn);
    settingsForm.appendChild(actions);
    settingsFeedback.textContent = "";
  } else {
    settingsFeedback.textContent = "Este juego no tiene ajustes editables por ahora.";
  }
  settingsFeedback.classList.remove("error", "ok");
}

settingsForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {};
  for (const input of settingsForm.querySelectorAll("[data-key]")) {
    const key = input.dataset.key;
    if (input.dataset.type === "bool") payload[key] = input.checked;
    else if (input.dataset.type === "int") payload[key] = Number(input.value);
    else payload[key] = input.value;
  }

  settingsFeedback.textContent = "Guardando...";
  settingsFeedback.classList.remove("error", "ok");
  settingsRestartBanner.classList.add("is-hidden");

  const { ok, data } = await apiFetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!ok) {
    settingsFeedback.textContent = (data && data.error) || "No se pudieron guardar los ajustes";
    settingsFeedback.classList.add("error");
    return;
  }
  settingsFeedback.textContent = "Ajustes guardados correctamente.";
  settingsFeedback.classList.add("ok");
  if (data.restart_required) settingsRestartBanner.classList.remove("is-hidden");
});

settingsRestartNow?.addEventListener("click", async () => {
  settingsRestartNow.disabled = true;
  await performAction("restart");
  settingsRestartBanner.classList.add("is-hidden");
  settingsRestartNow.disabled = false;
});

/* ---------------------------------------------------------------------- */
/* Backups                                                                  */
/* ---------------------------------------------------------------------- */

const createBackupButton = document.getElementById("createBackupButton");
const backupFeedback = document.getElementById("backupFeedback");
const backupsTableBody = document.getElementById("backupsTableBody");
const backupsEmptyState = document.getElementById("backupsEmptyState");

async function loadBackups() {
  const { ok, data } = await apiFetch("/api/backups");
  if (!ok || !data) return;
  backupsTableBody.innerHTML = "";
  backupsEmptyState.classList.toggle("is-hidden", data.backups.length > 0);

  for (const backup of data.backups) {
    const row = document.createElement("tr");

    const dateCell = document.createElement("td");
    dateCell.textContent = backup.created_at;
    row.appendChild(dateCell);

    const sizeCell = document.createElement("td");
    sizeCell.textContent = formatBytes(backup.size);
    row.appendChild(sizeCell);

    const actorCell = document.createElement("td");
    actorCell.textContent = backup.actor;
    row.appendChild(actorCell);

    const actionsCell = document.createElement("td");
    actionsCell.className = "row-actions";
    const downloadLink = document.createElement("a");
    downloadLink.className = "btn ghost";
    downloadLink.href = `/api/backups/${backup.id}/download`;
    downloadLink.textContent = "Descargar";
    actionsCell.appendChild(downloadLink);

    const restoreBtn = document.createElement("button");
    restoreBtn.className = "btn ghost";
    restoreBtn.type = "button";
    restoreBtn.textContent = "Restaurar";
    restoreBtn.addEventListener("click", () => restoreBackup(backup.id));
    actionsCell.appendChild(restoreBtn);

    row.appendChild(actionsCell);
    backupsTableBody.appendChild(row);
  }
}

async function restoreBackup(id) {
  const confirmed = await askConfirm(
    "Restaurar backup",
    "El servidor debe estar detenido. Se reemplazara todo el contenido actual de game/ por el del backup. ¿Continuar?"
  );
  if (!confirmed) return;
  const { ok, data } = await apiFetch(`/api/backups/${id}/restore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: true }),
  });
  backupFeedback.textContent = ok ? "Backup restaurado correctamente." : (data && data.error) || "No se pudo restaurar";
  backupFeedback.classList.toggle("ok", ok);
  backupFeedback.classList.toggle("error", !ok);
  loadOverview();
}

createBackupButton?.addEventListener("click", async () => {
  backupFeedback.textContent = "Creando backup...";
  backupFeedback.classList.remove("error", "ok");
  const { ok, data } = await apiFetch("/api/backups/create", { method: "POST" });
  backupFeedback.textContent = ok ? "Backup creado correctamente." : (data && data.error) || "No se pudo crear el backup";
  backupFeedback.classList.toggle("ok", ok);
  backupFeedback.classList.toggle("error", !ok);
  loadBackups();
});

/* ---------------------------------------------------------------------- */
/* Activity                                                                 */
/* ---------------------------------------------------------------------- */

async function loadActivity() {
  const { ok, data } = await apiFetch("/api/activity");
  if (!ok || !data) return;
  const list = document.getElementById("activityList");
  const emptyState = document.getElementById("activityEmptyState");
  list.innerHTML = "";
  emptyState.classList.toggle("is-hidden", data.entries.length > 0);

  for (const entry of data.entries) {
    const item = document.createElement("li");
    const time = document.createElement("time");
    time.textContent = entry.timestamp;
    const text = document.createElement("span");
    const detailText = entry.detail && Object.keys(entry.detail).length ? ` - ${JSON.stringify(entry.detail)}` : "";
    text.textContent = `${entry.action}${detailText}`;
    item.appendChild(time);
    item.appendChild(text);
    list.appendChild(item);
  }
}

/* ---------------------------------------------------------------------- */
/* Version check                                                          */
/* ---------------------------------------------------------------------- */

async function loadVersion() {
  const { ok, data } = await apiFetch("/api/version");
  if (!ok || !data) return;
  const versionElement = document.getElementById("version");
  if (versionElement) {
    versionElement.textContent = data.version;
  }
}

/* ---------------------------------------------------------------------- */
/* Users                                                                    */
/* ---------------------------------------------------------------------- */

const userForm = document.getElementById("userForm");
const usersList = document.getElementById("usersList");
const userFeedback = document.getElementById("userFeedback");

async function loadUsers() {
  if (!usersList) return;
  const { ok, data } = await apiFetch("/api/users");
  if (!ok || !data) return;
  usersList.innerHTML = "";
  for (const user of data.users) {
    const row = document.createElement("div");
    row.className = "user-row";
    const avatar = document.createElement("span");
    avatar.className = "user-avatar";
    avatar.textContent = user.username.slice(0, 1).toUpperCase();
    const info = document.createElement("div");
    info.className = "user-row-info";
    const name = document.createElement("strong");
    name.textContent = user.username;
    const meta = document.createElement("small");
    meta.textContent = user.active ? "Acceso activo" : "Acceso desactivado";
    info.append(name, meta);
    const role = document.createElement("span");
    role.className = "role-pill";
    role.textContent = user.role === "admin" ? "Administrador" : "Operador";
    const active = document.createElement("button");
    active.className = "btn ghost";
    active.type = "button";
    active.textContent = user.active ? "Desactivar" : "Activar";
    active.addEventListener("click", async () => {
      const confirmed = await askConfirm(user.active ? "Desactivar usuario" : "Activar usuario", `${user.active ? "Desactivaras" : "Activaras"} el acceso de ${user.username}.`);
      if (!confirmed) return;
      const response = await apiFetch(`/api/users/${encodeURIComponent(user.username)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: !user.active }) });
      if (!response.ok) window.alert(response.data?.error || "No se pudo actualizar el usuario");
      loadUsers();
    });
    row.append(avatar, info, role, active);
    usersList.appendChild(row);
  }
}

userForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = document.getElementById("newUsername").value.trim();
  const password = document.getElementById("newPassword").value;
  const role = document.getElementById("newUserRole").value;
  const { ok, data } = await apiFetch("/api/users", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password, role }) });
  userFeedback.textContent = ok ? "Usuario creado correctamente." : (data?.error || "No se pudo crear el usuario.");
  userFeedback.className = `feedback ${ok ? "ok" : "error"}`;
  if (ok) { userForm.reset(); loadUsers(); }
});

/* ---------------------------------------------------------------------- */
/* Wiring                                                                   */
/* ---------------------------------------------------------------------- */

tabLoaders.overview = () => {
  loadOverview();
  loadResources();
};
tabLoaders.files = loadFiles;
tabLoaders.software = () => {
  loadCatalogTree();
  loadInstallationDetails();
};
tabLoaders.settings = loadSettings;
tabLoaders.backups = loadBackups;
tabLoaders.activity = loadActivity;
tabLoaders.users = loadUsers;

fetchStatus();
loadOverview();
loadResources();
loadVersion();
setInterval(fetchStatus, 10000);
setInterval(() => {
  if (document.getElementById("tab-overview") && !document.getElementById("tab-overview").hidden) {
    loadOverview();
  }
}, 30000);
// Faster, separate interval: CPU/RAM are live metrics (docker stats), not
// config the admin just changed - a 30s wait to notice a spike defeats the
// point of a "real-time" meter. Only ticks while the tab is actually
// visible, same guard as the loadOverview interval above.
setInterval(() => {
  if (document.getElementById("tab-overview") && !document.getElementById("tab-overview").hidden) {
    loadResources();
  }
}, 4000);
initLogsStream();
