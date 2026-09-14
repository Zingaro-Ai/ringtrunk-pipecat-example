// Browser interaction checks with intercepted Firebase/SMS responses. No SMS is sent.
import assert from "node:assert/strict";
import fs from "node:fs/promises";

const debug = "http://127.0.0.1:9331";
const target = await fetch(`${debug}/json/new?about:blank`, { method: "PUT" }).then(r => r.json());
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let sequence = 0;
const pending = new Map();
const errors = [];
const proofRequests = [];
const callRequests = [];
let polls = 0;
const fixtureConfig = await fetch("http://127.0.0.1:4173/api/config").then(r => r.json());
fixtureConfig.outbound_demo_ready = true;
const fixtureCall = { call_id: "fixture-call", call_token: "fixture-receipt", framework: "pipecat",
  phase: "ringing", answered: false, done: false, message: "Calling your verified mobile. Please answer." };
let proofAttempts = 0;
const phone = "+91" + "9".repeat(10);
const mockSDK = `
export async function sendCode() { window.fixtureSends=(window.fixtureSends||0)+1; }
export async function confirmCode(code) {
  if(code !== '123456') throw {code:'auth/invalid-verification-code'};
  return 'browser.fixture.token';
}
export async function resetVerification() { window.fixtureReset=true; }
`;
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});
ws.onmessage = async ({ data }) => {
  const message = JSON.parse(data);
  if (message.id) {
    const p = pending.get(message.id);
    pending.delete(message.id);
    message.error ? p.reject(new Error(JSON.stringify(message.error))) : p.resolve(message.result);
  } else if (message.method === "Runtime.exceptionThrown") {
    errors.push(message.params.exceptionDetails.text);
  } else if (message.method === "Fetch.requestPaused") {
    const { requestId, request } = message.params;
    if (request.url.endsWith("/api/config")) {
      await send("Fetch.fulfillRequest", { requestId, responseCode: 200,
        responseHeaders: [{ name: "Content-Type", value: "application/json" }],
        body: Buffer.from(JSON.stringify(fixtureConfig)).toString("base64") });
    } else if (request.url.endsWith("/api/calls")) {
      callRequests.push(request);
      if (callRequests.length === 1) await send("Fetch.failRequest", { requestId, errorReason: "ConnectionClosed" });
      else await send("Fetch.fulfillRequest", { requestId, responseCode: 202,
        responseHeaders: [{ name: "Content-Type", value: "application/json" }],
        body: Buffer.from(JSON.stringify(fixtureCall)).toString("base64") });
    } else if (request.url.includes("/api/calls/")) {
      assert.equal(Object.entries(request.headers).find(([k]) => k.toLowerCase() === "x-call-token")[1], "fixture-receipt");
      const result = request.url.endsWith("/cancel")
        ? { ...fixtureCall, phase: "cancelled", done: true, message: "Demo call cancelled." }
        : ++polls > 1 ? { ...fixtureCall, phase: "connected", answered: true, message: "Your demo agent is on the call." } : fixtureCall;
      await send("Fetch.fulfillRequest", { requestId, responseCode: 200,
        responseHeaders: [{ name: "Content-Type", value: "application/json" }],
        body: Buffer.from(JSON.stringify(result)).toString("base64") });
    } else if (request.url.endsWith("/static/vendor/firebase-phone.js")) {
      await send("Fetch.fulfillRequest", { requestId, responseCode: 200,
        responseHeaders: [{ name: "Content-Type", value: "text/javascript" }],
        body: Buffer.from(mockSDK).toString("base64") });
    } else if (request.url.endsWith("/api/phone/verify")) {
      proofRequests.push(request);
      proofAttempts++;
      const result = proofAttempts === 1
        ? { detail: "Verification is temporarily unavailable. Please retry." }
        : { verified: true, phone_number: phone, expires_at: Math.floor(Date.now() / 1000) + 600,
          outbound_demo_ready: true };
      await send("Fetch.fulfillRequest", { requestId, responseCode: proofAttempts === 1 ? 503 : 200,
        responseHeaders: [{ name: "Content-Type", value: "application/json" }],
        body: Buffer.from(JSON.stringify(result)).toString("base64") });
    } else await send("Fetch.continueRequest", { requestId });
  }
};
const evaluate = async expression => {
  const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
  return r.result.value;
};
const waitFor = async expression => {
  for (let i = 0; i < 100; i++) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out: ${expression}`);
};
const click = id => evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);
const fill = (id, value) => evaluate(`document.getElementById(${JSON.stringify(id)}).value=${JSON.stringify(value)};document.getElementById(${JSON.stringify(id)}).dispatchEvent(new Event('input'));`);

try {
  await send("Runtime.enable");
  await send("Network.setCacheDisabled", { cacheDisabled: true });
  await send("Fetch.enable", { patterns: [
    { urlPattern: "*/static/vendor/firebase-phone.js" }, { urlPattern: "*/api/phone/verify" },
    { urlPattern: "*/api/config" }, { urlPattern: "*/api/calls*" },
  ] });
  await send("Page.navigate", { url: "http://127.0.0.1:4173" });
  await waitFor("!document.getElementById('download-zip').disabled");
  await click("outbound-tab");
  assert.equal(await evaluate("document.getElementById('send-code').disabled"), true);
  await fill("callback-phone", phone.slice(3));
  assert.equal(await evaluate("document.getElementById('send-code').disabled"), true);
  await click("phone-consent");
  assert.equal(await evaluate("document.getElementById('send-code').disabled"), false);
  await click("send-code");
  await waitFor("!document.getElementById('phone-code-form').hidden");
  assert.equal(await evaluate("document.getElementById('callback-phone').readOnly"), true);
  assert.equal(await evaluate("document.getElementById('send-code').disabled"), true);
  await fill("phone-code", "123");
  assert.equal(await evaluate("document.getElementById('verify-code').disabled"), true);
  await fill("phone-code", "111111");
  await click("verify-code");
  await waitFor("document.getElementById('phone-status').textContent.includes('incorrect')");
  assert.equal(proofRequests.length, 0);
  await fill("phone-code", "123456");
  await click("verify-code");
  await waitFor("document.getElementById('phone-status').textContent.includes('temporarily unavailable')");
  await click("verify-code");
  await waitFor("!document.getElementById('phone-verified').hidden");
  assert.equal(await evaluate("document.getElementById('verified-number').textContent"), phone);
  assert.equal(await evaluate("document.getElementById('request-callback').disabled"), false);
  assert.equal(callRequests.length, 0, "SMS verification alone must not dial");
  await click("request-callback");
  await waitFor("document.getElementById('request-callback').textContent === 'Retry call request'");
  await click("request-callback");
  await waitFor("!document.getElementById('cancel-callback').hidden");
  assert.equal(await evaluate("document.getElementById('request-callback').disabled"), true);
  assert.equal(await evaluate("document.querySelector('[data-framework=\"livekit-agents\"]').disabled"), true);
  await click("request-callback");
  assert.equal(callRequests.length, 2, "Double clicking must not request a second call");
  assert.equal(callRequests[0].postData, callRequests[1].postData, "Retries reuse the request ID");
  const callBody = JSON.parse(callRequests[1].postData);
  assert.deepEqual(Object.keys(callBody).sort(), ["framework", "request_id"]);
  assert.match(callBody.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  await waitFor("document.getElementById('call-status').textContent.includes('on the call')");
  await click("cancel-callback");
  await waitFor("document.getElementById('call-status').textContent.includes('cancelled')");
  assert.equal(await evaluate("document.getElementById('cancel-callback').hidden"), true);
  assert.equal(await evaluate("window.fixtureSends"), 1);
  for (const r of proofRequests) {
    assert.equal(r.postData, "{}");
    assert.equal(Object.entries(r.headers).find(([k]) => k.toLowerCase() === "authorization")[1], "Bearer browser.fixture.token");
  }
  for (const width of [1440, 390, 320]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height: 1000, deviceScaleFactor: 1, mobile: width < 700 });
    assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false);
  }
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  await evaluate("document.getElementById('outbound-panel').scrollIntoView({block:'center'})");
  const shot = await send("Page.captureScreenshot", { format: "png" });
  await fs.writeFile("/tmp/ringtrunk-playground-browser/phone-verified.png", Buffer.from(shot.data, "base64"));
  await evaluate("const realNow=Date.now;Date.now=()=>realNow()+601000");
  await waitFor("document.getElementById('phone-verified').hidden");
  assert.equal(await evaluate("document.getElementById('callback-phone').readOnly"), false);
  assert.equal(await evaluate("window.fixtureReset"), true);
  assert.match(await evaluate("document.getElementById('phone-status').textContent"), /expired/);
  assert.deepEqual(errors, []);
  console.log("Phone and callback UI checks passed: SMS consent, invalid code, outage/retry, verified number, expiry, explicit call action, idempotent network retry, double-click protection, ringing/answer/cancel, fixed destination, mobile widths. All SMS and call responses were mocked.");
} finally {
  ws.close();
  await fetch(`${debug}/json/close/${target.id}`);
}
