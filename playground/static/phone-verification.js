const $ = (id) => document.getElementById(id);

const messages = {
  "auth/invalid-phone-number": "Enter a valid Indian mobile number.",
  "auth/invalid-verification-code": "That code is incorrect. Check the SMS and try again.",
  "auth/code-expired": "That code has expired. Request a new SMS code.",
  "auth/session-expired": "That code has expired. Request a new SMS code.",
  "auth/too-many-requests": "Too many attempts. Please wait before requesting another code.",
  "auth/quota-exceeded": "SMS verification is temporarily unavailable. Please try again later.",
  "auth/network-request-failed": "Could not reach verification. Check your connection and retry.",
  "auth/captcha-check-failed": "The security check expired. Please try sending the code again.",
  "auth/invalid-app-credential": "The security check could not finish. Please retry or refresh the page.",
  "auth/billing-not-enabled": "SMS verification is not available yet. Please try again later.",
  "auth/operation-not-allowed": "SMS verification is not available yet. Please try again later.",
  "auth/unauthorized-domain": "Open the local SMS preview address to verify your number.",
};

export function initializePhoneVerification(config, onChange = () => {}) {
  const settings = config.phone_verification;
  let sdkPromise;
  let busy = false;
  let phase = "phone";
  let sentNumber = "";
  let resendAt = 0;
  let verifiedUntil = 0;
  let requestId = 0;
  let verifiedToken = null;
  let timer;

  const phone = () => {
    const digits = $("callback-phone").value.replace(/[\s()-]/g, "");
    return /^[6-9]\d{9}$/.test(digits) ? `+91${digits}` : null;
  };
  const sdk = () => sdkPromise ||= import("/static/vendor/firebase-phone.js");
  const status = (message, error = false) => {
    $("phone-status").textContent = message;
    $("phone-status").classList.toggle("verification-error", error);
  };
  const stopTimer = () => { clearInterval(timer); timer = null; };
  const render = () => {
    const valid = phone();
    const seconds = Math.max(0, Math.ceil((resendAt - Date.now()) / 1000));
    $("send-code").disabled = !settings?.enabled || !valid || !$("phone-consent").checked || busy || seconds > 0 || phase === "verified";
    $("send-code").textContent = busy && phase === "phone" ? "Sending code…"
      : seconds > 0 && phase !== "verified" ? `Resend in ${seconds}s`
        : phase === "code" ? "Resend SMS code" : "Send verification code";
    $("send-code").hidden = phase === "verified";
    $("phone-code-form").hidden = phase !== "code";
    $("phone-verified").hidden = phase !== "verified";
    $("callback-phone").readOnly = busy || phase !== "phone";
    $("phone-consent").disabled = busy || phase !== "phone";
    $("verify-code").disabled = busy || !/^\d{6}$/.test($("phone-code").value);
    $("verify-code").textContent = busy && phase === "code" ? "Verifying…" : "Verify number";
    $("change-phone").hidden = phase === "phone";
    $("change-phone").disabled = busy;
    $("phone-code").disabled = busy;
    if (phase === "verified" && Date.now() >= verifiedUntil * 1000) {
      void reset("Verification expired. Send a new code to verify again.");
    }
    if (seconds === 0 && phase !== "verified") stopTimer();
    onChange();
  };
  const startTimer = () => {
    if (!timer) timer = setInterval(render, 1000);
  };
  const reset = async (message = "") => {
    requestId++;
    phase = "phone";
    busy = false;
    sentNumber = "";
    verifiedToken = null;
    verifiedUntil = 0;
    $("phone-code").value = "";
    $("verified-number").textContent = "";
    if (sdkPromise) {
      try { await (await sdkPromise).resetVerification(); } catch { /* No token is retained. */ }
    }
    status(message);
    render();
    $("callback-phone").focus();
  };

  $("callback-phone").addEventListener("input", () => {
    const valid = phone();
    const invalid = Boolean($("callback-phone").value && !valid);
    $("callback-validation").hidden = !invalid;
    $("callback-phone").setAttribute("aria-invalid", String(invalid));
    // Invalidate a proof if the input is changed programmatically as well.
    if (phase !== "phone" && valid !== sentNumber) void reset();
    render();
  });
  $("phone-consent").addEventListener("change", render);
  $("phone-code").addEventListener("input", render);
  $("change-phone").addEventListener("click", () => reset());
  $("send-code").addEventListener("click", async () => {
    if ($("send-code").disabled) return;
    const number = phone();
    const current = ++requestId;
    busy = true;
    const oldPhase = phase;
    status("Completing the security check and sending your SMS code…");
    render();
    try {
      await (await sdk()).sendCode(settings.firebase, number);
      if (current !== requestId) return;
      sentNumber = number;
      phase = "code";
      resendAt = Date.now() + settings.resend_after_seconds * 1000;
      $("phone-code").value = "";
      status("SMS sent. Enter the six-digit code from your message.");
      startTimer();
    } catch (error) {
      if (current !== requestId) return;
      phase = oldPhase === "verified" ? "phone" : oldPhase;
      status(messages[error?.code] || "Could not send the SMS. Please try again.", true);
      if (error?.code === "auth/too-many-requests") {
        resendAt = Date.now() + 60_000;
        startTimer();
      }
    } finally {
      if (current === requestId) {
        busy = false;
        render();
        if (phase === "code") $("phone-code").focus();
      }
    }
  });
  $("phone-code-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("verify-code").disabled || phase !== "code") return;
    const current = ++requestId;
    busy = true;
    status("Verifying your number…");
    render();
    try {
      const token = await (await sdk()).confirmCode($("phone-code").value);
      const response = await fetch("/api/phone/verify", {
        method: "POST", headers: { Authorization: `Bearer ${token}` }, body: "{}",
      });
      const result = await response.json();
      if (!response.ok) throw { serverMessage: result.detail };
      if (result.phone_number !== sentNumber || result.verified !== true) throw new Error("Phone proof mismatch");
      if (current !== requestId) return;
      verifiedToken = token;
      verifiedUntil = result.expires_at;
      phase = "verified";
      $("phone-code").value = "";
      $("verified-number").textContent = result.phone_number;
      status(config.outbound_demo_ready
        ? "Number verified. Press “Call my phone” below to start your demo."
        : "Number verified. The demo agent is not available yet.");
      startTimer();
    } catch (error) {
      if (current !== requestId) return;
      verifiedToken = null;
      status(error?.serverMessage || messages[error?.code] || "Could not verify the code. Please try again.", true);
    } finally {
      if (current === requestId) { busy = false; render(); }
    }
  });

  $("phone-note").textContent = settings?.enabled
    ? "SMS verifies your number. Press “Call my phone” afterward to request an AI demo call."
    : "Phone verification is not configured for this preview.";
  $("phone-preview-link").hidden = !settings?.preview_origin || location.origin === settings.preview_origin;
  if (settings?.preview_origin) $("phone-preview-link").href = settings.preview_origin;
  if (!settings?.enabled) status("Phone verification is not configured for this preview.");
  render();

  // The callback sends this bearer token; the backend derives its recipient again.
  return {
    tokenForCallback() {
      return phase === "verified" && verifiedUntil * 1000 > Date.now() ? verifiedToken : null;
    },
  };
}
