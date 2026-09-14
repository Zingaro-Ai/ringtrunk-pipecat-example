// Run against the local preview with Chrome's debug port at 9331.
// node tests/browser-check.mjs
import assert from "node:assert/strict";
import fs from "node:fs/promises";

const artifacts = "/tmp/ringtrunk-playground-browser";
await fs.mkdir(artifacts, { recursive: true });
for (const name of await fs.readdir(artifacts)) {
  if (/^ringtrunk-(pipecat|livekit-agents)( \(\d+\))?\.zip$/.test(name)) await fs.rm(`${artifacts}/${name}`);
}
const target = await fetch("http://127.0.0.1:9331/json/new?about:blank", { method: "PUT" }).then((r) => r.json());
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let id = 0;
const pending = new Map();
const errors = [];
const requests = [];
let failConfigOnce = false;

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const messageId = ++id;
    pending.set(messageId, { resolve, reject });
    ws.send(JSON.stringify({ id: messageId, method, params }));
  });
}
ws.onmessage = async ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) {
    const task = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) task.reject(new Error(JSON.stringify(message.error)));
    else task.resolve(message.result);
  } else if (message.method === "Runtime.exceptionThrown") errors.push(message.params.exceptionDetails);
  else if (message.method === "Network.requestWillBeSent") requests.push(message.params.request.url);
  else if (message.method === "Fetch.requestPaused") {
    if (failConfigOnce && message.params.request.url.endsWith("/api/config")) {
      failConfigOnce = false;
      await send("Fetch.fulfillRequest", { requestId: message.params.requestId, responseCode: 503, body: "" });
    } else await send("Fetch.continueRequest", { requestId: message.params.requestId });
  }
};
const evaluate = async (expression) => {
  const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true, userGesture: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
};
const waitFor = async (expression) => {
  for (let tries = 0; tries < 150; tries++) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out: ${expression}`);
};
const click = (selector) => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
async function screenshot(name, width, height, full = false) {
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 740 });
  await evaluate("window.scrollTo(0,0)");
  await new Promise((resolve) => setTimeout(resolve, 150));
  const result = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: full,
    ...(full ? { clip: { x: 0, y: 0, width, height: await evaluate("document.documentElement.scrollHeight"), scale: 1 } } : {}) });
  await fs.writeFile(`${artifacts}/${name}.png`, Buffer.from(result.data, "base64"));
}

try {
  await send("Page.enable");
  await send("Page.bringToFront");
  await send("Emulation.setFocusEmulationEnabled", { enabled: true });
  await send("Runtime.enable");
  await send("Network.enable");
  await send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: artifacts });
  await send("Browser.grantPermissions", { origin: "http://127.0.0.1:4173", permissions: ["clipboardReadWrite", "clipboardSanitizedWrite"] });
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: "http://127.0.0.1:4173" });
  await waitFor("!document.getElementById('download-zip').disabled");
  await evaluate("document.fonts.ready");
  assert.equal(await evaluate("document.title"), "Voice Agent Playground · RingTrunk");
  assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false);
  assert.equal(await evaluate("document.querySelectorAll('[contenteditable=true]').length"), 0);
  assert.equal(await evaluate("document.querySelectorAll('textarea').length"), 1);
  assert.equal(await evaluate("document.querySelector('a[href^=\"tel:\"]')"), null);
  assert.equal(await evaluate("document.querySelector('.call-button').disabled"), true);
  assert.match(await evaluate("document.getElementById('source-code').textContent"), /async def run_bot/);
  await screenshot("desktop", 1440, 1100, true);
  await screenshot("desktop-first-screen", 1440, 1100);

  const fixedPrompt = await evaluate("document.getElementById('prompt').value");
  assert.equal(await evaluate("document.getElementById('prompt').readOnly"), true);
  assert.equal(await evaluate("document.getElementById('reset-prompt')"), null);
  await click("#view-config");
  assert.equal(await evaluate("JSON.parse(document.getElementById('source-code').textContent).system_prompt"), fixedPrompt);
  await click("#copy-code");
  await waitFor("document.getElementById('toast').textContent.includes('copied')");
  assert.equal(JSON.parse(await evaluate("navigator.clipboard.readText()" )).system_prompt, fixedPrompt);
  await click("[data-framework='livekit-agents']");
  await waitFor("document.getElementById('filename').textContent === 'agent.py' && !document.getElementById('download-zip').disabled");
  assert.equal(await evaluate("document.getElementById('prompt').value"), fixedPrompt);
  assert.match(await evaluate("document.getElementById('source-code').textContent"), /AgentSession/);
  await click("#download-zip");
  await waitFor("document.getElementById('toast').textContent.includes('ZIP downloaded')");
  await click("#outbound-tab");
  assert.equal(await evaluate("document.getElementById('outbound-panel').hidden"), false);
  assert.equal(await evaluate("document.getElementById('inbound-panel').hidden"), true);
  assert.equal(await evaluate("document.querySelector('#outbound-panel button').disabled"), true);
  await evaluate("document.getElementById('callback-phone').value='112'; document.getElementById('callback-phone').dispatchEvent(new Event('input'));");
  assert.equal(await evaluate("document.getElementById('callback-validation').hidden"), false);
  await evaluate("document.getElementById('callback-phone').value=''; document.getElementById('callback-phone').dispatchEvent(new Event('input'));");
  await screenshot("livekit-desktop", 1440, 1100, true);
  await click("#inbound-tab");
  assert.match(await evaluate("document.getElementById('demo-number').textContent"), /\+91 [0-9]{5} [0-9]{5}/);
  await click("[data-file='call.py']");
  assert.match(await evaluate("document.getElementById('source-code').textContent"), /wait_until_answered=True/);

  for (const width of [390, 768, 980, 320]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height: 844, deviceScaleFactor: 1, mobile: width < 740 });
    assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false, `Overflow at ${width}px`);
    assert.equal(await evaluate("document.getElementById('mobile-file-select').hidden"), false);
    await evaluate("document.getElementById('mobile-file-select').value='.env.example'; document.getElementById('mobile-file-select').dispatchEvent(new Event('change'));");
    assert.equal(await evaluate("document.getElementById('filename').textContent"), ".env.example");
    assert.match(await evaluate("document.getElementById('source-code').innerText"), /LIVEKIT_API_KEY=\n/);
  }
  await click("[data-framework='pipecat']");
  await waitFor("document.getElementById('filename').textContent === 'bot.py'");
  await screenshot("mobile", 390, 844, true);
  await screenshot("mobile-first-screen", 390, 844);
  await click("#download-zip");
  await waitFor("document.getElementById('toast').textContent.includes('Pipecat ZIP downloaded')");

  // A failed initial load is recoverable without a page reload.
  failConfigOnce = true;
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/config" }] });
  await send("Page.reload");
  await waitFor("!document.getElementById('load-error').hidden");
  await click("#retry-load");
  await waitFor("!document.getElementById('download-zip').disabled");
  assert.equal(await evaluate("document.getElementById('load-error').hidden"), true);
  await send("Fetch.disable");
  assert.equal(errors.length, 0, JSON.stringify(errors));
  assert.equal(requests.filter((url) => !url.startsWith("http://127.0.0.1:4173") && !url.startsWith("blob:")).length, 0, "Unexpected external request");
  for (const name of ["ringtrunk-pipecat.zip", "ringtrunk-livekit-agents.zip"]) {
    const data = await fs.readFile(`${artifacts}/${name}`);
    assert.equal(data.subarray(0, 2).toString(), "PK");
  }
  console.log("Browser checks passed: both frameworks, read-only source, fixed prompt, call-direction tabs, disconnected-call controls, number display, clipboard, ZIP downloads, responsive layouts (320/390/768/980/1440), error recovery, no external requests or runtime errors.");
  console.log(`Screenshots and browser downloads: ${artifacts}`);
} finally {
  await fetch(`http://127.0.0.1:9331/json/close/${target.id}`);
  ws.close();
}
