const $ = (id) => document.getElementById(id);

function requestId() {
  // getRandomValues also works on the authorized HTTP hostname used for local SMS.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function initializeCallback(config, getToken, getFramework) {
  let busy = false;
  let call = null;
  let pending = null;
  let timer;
  let retryAt = 0;

  const status = (message, error = false) => {
    $("call-status").textContent = message;
    $("call-status").classList.toggle("verification-error", error);
  };
  const render = () => {
    const active = call && !call.done;
    $("request-callback").disabled = !config.outbound_demo_ready || !getToken() || busy || active || Date.now() < retryAt;
    $("request-callback").textContent = busy ? "Requesting call…" : active ? "Call in progress"
      : pending ? "Retry call request" : config.outbound_demo_ready ? "Call my phone" : "Demo agent unavailable";
    $("cancel-callback").hidden = !active;
    $("cancel-callback").disabled = busy;
    document.querySelectorAll("[data-framework]").forEach((button) => { button.disabled = Boolean(busy || active || pending); });
  };

  const poll = async () => {
    if (!call || call.done) return;
    try {
      const response = await fetch(`/api/calls/${call.call_id}`, {
        headers: { "X-Call-Token": call.call_token }, signal: AbortSignal.timeout(10_000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Call status unavailable.");
      call = { ...call, ...result };
      status(result.message, result.phase === "failed");
    } catch {
      status("Checking your call status… Please avoid requesting another call.");
    }
    render();
    if (call && !call.done) timer = setTimeout(poll, 2000);
  };

  $("request-callback").addEventListener("click", async () => {
    if ($("request-callback").disabled) return;
    const token = getToken();
    if (!token) return;
    busy = true;
    // A network retry reuses the same request ID, so it cannot dial twice.
    pending ||= { framework: getFramework(), request_id: requestId() };
    status("Preparing your demo call…");
    render();
    try {
      const response = await fetch("/api/calls", {
        method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(pending), signal: AbortSignal.timeout(20_000),
      });
      const result = await response.json();
      if (!response.ok) {
        if (response.status < 500) pending = null;
        if (response.status === 429) retryAt = Date.now() + Number(response.headers.get("Retry-After") || 60) * 1000;
        throw new Error(result.detail || "Could not request the call.");
      }
      call = result;
      pending = null;
      clearTimeout(timer);
      status(result.message, result.phase === "failed");
      if (!call.done) timer = setTimeout(poll, 700);
    } catch (error) {
      status(error.name === "TimeoutError" || error instanceof TypeError
        ? "The response was interrupted. Retry this request to check it safely."
        : error.message, true);
    } finally {
      busy = false;
      render();
    }
  });

  $("cancel-callback").addEventListener("click", async () => {
    if (!call || call.done || busy) return;
    busy = true;
    status("Ending your demo call…");
    render();
    try {
      const response = await fetch(`/api/calls/${call.call_id}/cancel`, {
        method: "POST", headers: { "X-Call-Token": call.call_token },
        signal: AbortSignal.timeout(20_000),
      });
      if (!response.ok) throw new Error("Cancellation not confirmed");
      call = { ...call, ...await response.json() };
      status(call.message);
      clearTimeout(timer);
    } catch {
      status("Still checking the call. You can also hang up on your phone.");
    } finally {
      busy = false;
      render();
    }
  });
  setInterval(render, 1000);
  render();
  return { render };
}
