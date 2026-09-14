"use strict";

const $ = (id) => document.getElementById(id);
const state = { framework: "pipecat", file: "bot.py", files: {}, cache: new Map(), config: null, callMode: "inbound", loadId: 0, loading: true, downloading: false };
let toastTimer;

function notify(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4300);
}

async function fetchJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error("Could not load the example.");
  return response.json();
}

function currentSource() {
  return state.files[state.file] || "";
}

function highlight(source, filename) {
  // Source and prompts always enter the DOM as text nodes, including HTML-like input.
  const python = filename.endsWith(".py");
  const json = filename.endsWith(".json");
  const regex = python
    ? /#[^\n]*|"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:from|import|as|async|await|def|return|if|else|elif|try|except|finally|with|for|in|not|and|or|is|None|True|False|class|raise|pass|yield|lambda)\b|\b\d+(?:\.\d+)?\b/g
    : json ? /"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|\b\d+\b/g : /#[^\n]*/g;
  const fragment = document.createDocumentFragment();
  let line = document.createElement("span");
  line.className = "code-line";
  fragment.append(line);
  const append = (text, kind = "") => {
    text.split("\n").forEach((part, index) => {
      if (index > 0) { line = document.createElement("span"); line.className = "code-line"; fragment.append(line); }
      const node = kind ? document.createElement("span") : document.createTextNode(part);
      if (kind) { node.className = kind; node.textContent = part; }
      line.append(node);
    });
  };
  let offset = 0;
  const trimmed = source.endsWith("\n") ? source.slice(0, -1) : source;
  for (const match of trimmed.matchAll(regex)) {
    append(trimmed.slice(offset, match.index));
    const token = match[0];
    const kind = token.startsWith("#") || token.startsWith('"""') || token.startsWith("'''") ? "token-comment"
      : /^["']/.test(token) ? (json && /^\s*:/.test(trimmed.slice(match.index + token.length)) ? "token-key" : "token-string")
      : /^\d/.test(token) ? "token-number" : "token-keyword";
    append(token, kind);
    offset = match.index + token.length;
  }
  append(trimmed.slice(offset));
  return fragment;
}

function renderSource(resetScroll = true) {
  const source = currentSource();
  $("source-code").replaceChildren(highlight(source, state.file));
  $("filename").textContent = state.file;
  $("line-count").textContent = `${source.trimEnd().split("\n").length} lines`;
  $("code-language").textContent = state.file.endsWith(".py") ? "PYTHON" : state.file.endsWith(".json") ? "JSON" : "TEXT";
  $("customized-badge").hidden = state.file !== "agent-config.json";
  $("copy-code").disabled = state.loading;
  if (resetScroll) { $("source-scroll").scrollTop = 0; $("source-scroll").scrollLeft = 0; }
  document.querySelectorAll(".file-list button").forEach((button) => {
    const selected = button.dataset.file === state.file;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  if ($("mobile-file-select")) $("mobile-file-select").value = state.file;
}

function selectFile(file) {
  if (!Object.hasOwn(state.files, file)) return;
  state.file = file;
  renderSource();
}

function renderFiles() {
  const list = $("file-list");
  list.replaceChildren();
  const select = document.createElement("select");
  select.id = "mobile-file-select";
  select.className = "mobile-file-select";
  select.setAttribute("aria-label", "Choose source file");
  const media = window.matchMedia("(max-width: 980px)");
  const names = Object.keys(state.files);
  for (const name of names) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.file = name;
    button.title = name;
    button.setAttribute("aria-label", `View ${name}`);
    const symbol = document.createElement("span");
    symbol.className = "file-symbol";
    symbol.setAttribute("aria-hidden", "true");
    symbol.textContent = name.endsWith(".py") ? "◇" : name.endsWith(".json") ? "{}" : "≡";
    button.append(symbol, document.createTextNode(name.replace("livekit/", "")));
    if (name.startsWith("livekit/")) button.classList.add("nested");
    button.addEventListener("click", () => selectFile(name));
    list.append(button);
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    select.append(option);
  }
  select.addEventListener("change", () => selectFile(select.value));
  $("mobile-file-select")?.remove();
  $("project-label").after(select);
  // One responsive file picker also supports keyboard navigation on small screens.
  const applyMedia = () => {
    select.hidden = !media.matches;
    $("project-label").hidden = media.matches;
    $("code-language").hidden = media.matches;
  };
  if (state.mediaCleanup) state.mediaCleanup();
  media.addEventListener("change", applyMedia);
  state.mediaCleanup = () => media.removeEventListener("change", applyMedia);
  applyMedia();
  $("file-count").textContent = names.length;
}

async function chooseFramework(framework) {
  if (!state.config?.frameworks.some((item) => item.id === framework)) return;
  const loadId = ++state.loadId;
  state.framework = framework;
  state.loading = true;
  $("code-window").setAttribute("aria-busy", "true");
  $("load-error").hidden = true;
  $("download-zip").disabled = true;
  $("copy-code").disabled = true;
  $("source-code").textContent = "Loading example…";
  $("file-list").replaceChildren();
  $("mobile-file-select")?.remove();
  document.querySelectorAll("[data-framework]").forEach((button) => {
    const selected = button.dataset.framework === framework;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  const metadata = state.config.frameworks.find((item) => item.id === framework);
  $("project-label").textContent = `ringtrunk-${framework}`;
  $("sdk-version").textContent = `${metadata.name} ${metadata.version}`;
  $("run-instructions").textContent = framework === "pipecat"
    ? "Start the Pipecat webhook server, connect it in LiveKit Cloud, and call your RingTrunk number."
    : "Start the named LiveKit agent worker. Your inbound dispatch rule sends each caller to a new agent session.";
  try {
    const result = state.cache.get(framework) || await fetchJSON(`/api/examples/${framework}`);
    state.cache.set(framework, result);
    if (loadId !== state.loadId) return;
    state.files = result.files;
    state.file = metadata.entrypoint;
    state.loading = false;
    renderFiles();
    renderSource();
    $("prompt").value = JSON.parse(state.files["agent-config.json"]).system_prompt;
    $("download-zip").disabled = state.downloading;
  } catch (_error) {
    if (loadId !== state.loadId) return;
    state.files = {};
    $("source-code").textContent = "Example unavailable. Use “Try again” above to reload.";
    $("load-error").hidden = false;
  } finally {
    if (loadId === state.loadId) $("code-window").setAttribute("aria-busy", "false");
  }
}

function renderCallMode() {
  const outbound = state.callMode === "outbound";
  $("inbound-panel").hidden = outbound;
  $("outbound-panel").hidden = !outbound;
  document.querySelectorAll("[data-call-mode]").forEach((button) => {
    const active = button.dataset.callMode === state.callMode;
    button.setAttribute("aria-selected", String(active));
    button.classList.toggle("active", active);
    button.tabIndex = active ? 0 : -1;
  });
}

function renderDemoConfig() {
  const number = state.config.demo_number;
  $("demo-number").textContent = number ? `${number.slice(0, 3)} ${number.slice(3, 8)} ${number.slice(8)}` : "Number pending selection";
  $("outbound-caller-id").textContent = number ? `${number.slice(0, 3)} ${number.slice(3, 8)} ${number.slice(8)}` : "the RingTrunk demo number";
  $("demo-allocation").textContent = state.config.allocation_status === "allocated"
    ? `${state.config.requested_channels} channels allocated · ${state.config.outbound_demo_ready ? "outbound demo ready" : "calling setup pending"}`
    : `${state.config.requested_channels} channels requested · allocation pending`;
  renderCallMode();
}

async function initialize() {
  try {
    state.config = await fetchJSON("/api/config");
    renderDemoConfig();
    if (!state.phoneVerification) {
      const { initializePhoneVerification } = await import("/static/phone-verification.js");
      state.phoneVerification = initializePhoneVerification(state.config, () => state.callback?.render());
      const { initializeCallback } = await import("/static/callback.js");
      state.callback = initializeCallback(state.config,
        () => state.phoneVerification.tokenForCallback(), () => state.framework);
    }
    await chooseFramework(state.framework);
  } catch (_error) {
    $("load-error").hidden = false;
    $("source-code").textContent = "Local server unavailable.";
  }
}

document.querySelectorAll("[data-framework]").forEach((button) => {
  button.addEventListener("click", () => { chooseFramework(button.dataset.framework); });
});
document.querySelectorAll("[data-call-mode]").forEach((button) => {
  button.addEventListener("click", () => { state.callMode = button.dataset.callMode; renderCallMode(); });
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    state.callMode = event.key === "Home" ? "inbound" : event.key === "End" ? "outbound" : state.callMode === "inbound" ? "outbound" : "inbound";
    renderCallMode();
    document.querySelector(`[data-call-mode="${state.callMode}"]`).focus();
  });
});
$("view-config").addEventListener("click", () => { selectFile("agent-config.json"); $("code-window").scrollIntoView({ block: "center" }); });
$("retry-load").addEventListener("click", () => state.config ? chooseFramework(state.framework) : initialize());
$("copy-code").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(currentSource()); notify(`${state.file} copied.`); }
  catch (_error) { notify("Clipboard unavailable. Select and copy the code from the viewer."); }
});
$("download-zip").addEventListener("click", async () => {
  if (state.loading || state.downloading) return;
  const framework = state.framework;
  state.downloading = true;
  $("download-zip").disabled = true;
  const label = $("download-zip").querySelector("span");
  label.textContent = "Preparing…";
  try {
    const response = await fetch(`/api/download/${framework}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    if (!response.ok) throw new Error("Download failed. Please try again.");
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `ringtrunk-${framework}.zip`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    notify(`${framework === "pipecat" ? "Pipecat" : "LiveKit Agents"} ZIP downloaded.`);
  } catch (error) { notify(error.message); }
  finally { state.downloading = false; label.textContent = "Download ZIP"; $("download-zip").disabled = state.loading; }
});

initialize();
