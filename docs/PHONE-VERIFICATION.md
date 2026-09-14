# Firebase SMS verification

The outbound tab supports Firebase Phone Authentication: SMS consent, reCAPTCHA, code
entry, verification, resend cooldown and changing the phone number. No email is required.
The SDK is loaded only when the visitor requests a code. Firebase tokens stay in memory.

`POST /api/phone/verify` takes a Firebase ID token in `Authorization: Bearer …` and an empty
body. It verifies Google's signature, the project's audience and issuer, the phone sign-in
provider, an Indian mobile number, and authentication within the previous ten minutes.
It then checks the current Firebase account for deletion, disablement, changed phone numbers
or revoked sessions. The response contains only the verified phone number, expiry and call
readiness. Passing a phone number, UID or prompt in the request body is rejected.

`POST /api/calls` calls `verified_callback_identity(request)` again and dials only
`identity.phone_number`. A browser's verified label is not authorization. Verification itself
does not place a call: the visitor must then press **Call my phone**. See
[callback configuration and limits](CALLBACKS.md).

## Configure

1. Link the intended **Auth project** to billing. Billing on a separate Firestore project
   does not enable SMS for the Auth project.
2. Enable Phone sign-in in Firebase Authentication, allow India in the SMS region policy,
   and authorize the preview and eventual website domains. Preserve existing providers.
3. Store the web app's public Firebase configuration outside the repository:

   ```json
   {
     "apiKey": "YOUR_FIREBASE_WEB_API_KEY",
     "authDomain": "your-project.firebaseapp.com",
     "projectId": "your-project",
     "appId": "YOUR_FIREBASE_WEB_APP_ID"
   }
   ```

   Use the web app configuration, never a service-account JSON file. The backend needs no
   private Firebase credential. Restrict the app API key to the Firebase APIs it needs;
   account lookup must also be permitted from the server.
4. Start the local server with the public configuration path:

   ```bash
   RINGTRUNK_FIREBASE_CONFIG=/absolute/private/firebase-client.json \
   RINGTRUNK_PREVIEW_ORIGIN=http://playground-local.ringtrunk.com:4173 \
   uv run --frozen uvicorn playground.app:app --host 127.0.0.1 --port 4173
   ```

   Displaying the allocated demo number still uses the separate runtime variables described
   in the main README. Actual numbers and credentials are never included in ZIPs.

## Local SMS browser

Firebase's web phone-auth documentation excludes localhost as the hosted phone-auth
domain. The local review uses an authorized development hostname mapped to loopback inside
a dedicated Chrome profile. This requires no DNS change, operating-system host-file edit
or website deployment:

```bash
google-chrome --no-first-run --no-default-browser-check \
  --user-data-dir=/tmp/ringtrunk-playground-sms-chrome \
  --host-resolver-rules='MAP playground-local.ringtrunk.com 127.0.0.1' \
  --app=http://playground-local.ringtrunk.com:4173
```

The hostname works in that browser profile. Choose **Call my phone**, enter your own mobile
number, consent to the SMS and enter the received code in the page. Then press the enabled
**Call my phone** button to request the demo. Real SMS messages are sent only when requested.
Ordinary source viewing and ZIP downloads need no Firebase setup.

## Validation and limits

- Backend tests use locally signed RSA fixtures and mocked Google/Firebase responses.
  They do not register fictional phone numbers in the live Auth project.
- `node tests/browser-phone-check.mjs` intercepts SMS, verification and callback responses in
  a separate test tab. It does not send SMS, make phone calls or produce a Firebase identity.
- The live Firebase reCAPTCHA initialization was verified, and the user confirmed actual
  SMS receipt and successful verification during local review on September 14, 2026.
- Firebase enforces its SMS throttling; the UI adds a 60-second resend cooldown. The local
  backend also bounds proof-verification attempts. This single-process throttle is not a
  substitute for shared production admission and call limits.
- Verification expires ten minutes after phone sign-in. Refreshing the page or changing
  the number clears the in-memory flow. Verification creates no RingTrunk membership,
  email or Firestore customer record, and does not itself place a call.

Sources: [Firebase web phone auth](https://firebase.google.com/docs/auth/web/phone-auth),
[Google's Firebase token verifier](https://googleapis.dev/python/google-auth/latest/reference/google.oauth2.id_token.html),
[Firebase account lookup](https://firebase.google.com/docs/reference/rest/auth#section-get-account-info).
