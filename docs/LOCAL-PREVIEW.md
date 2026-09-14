# Local review — 14 September 2026

The user requested local review before deployment. This branch adds the playground in the
existing RingTrunk public-example repository, independently of the RingTrunk portal and
production voice-agent repositories. The user-authorized number allocation is recorded below.
No website/agent deployment, DNS change or email was performed. A dedicated
outbound trunk was created in the requested LiveKit Cloud project, and the user confirmed
receipt of callbacks and two-way conversation with both Pipecat and LiveKit Agents.
The user subsequently authorized committing and pushing the playground branch, with
deployment planned for September 15 in a separate session. See the [handoff](HANDOFF.md).

## Implemented

- Responsive RingTrunk UI with two framework choices and a source-file explorer.
- Read-only source and fixed prompt. The download API rejects client prompt/code overrides.
- Real ZIP downloads with the fixed prompt, inbound/outbound trunk templates, and `call.py`.
- Standalone Pipecat 1.9.0 and LiveKit Agents 1.8.1 templates using LiveKit Cloud.
- Verified Pipecat webhook admission: SIP participants only, room prefix scope, duplicate
  suppression, bounded request size, concurrent-call cap and shutdown cancellation.
- Named native SDK dispatch with SIP-only participant selection and room prefix scope.
- Two-minute default session bound and caller removal on completion in both examples.
- At the user's request, smart turn detector models are explicitly disabled: Pipecat uses
  `SpeechTimeoutUserTurnStopStrategy` and LiveKit Agents uses `turn_detection="vad"`.
  Basic speech/silence detection remains enabled for automatic replies on phone calls.
- Inbound/outbound call tabs with a runtime-configured demo number and three allocated
  channels. Firebase SMS verification and outbound callback dispatch are connected locally.
- Firebase Phone Authentication with explicit SMS consent, reCAPTCHA, a resend countdown,
  wrong/expired code handling, and a verified-number state that expires after ten minutes.
- Backend verification checks Google's signature, the configured project, phone sign-in,
  authentication age, and the current account's phone, disabled status and revocation time.
  The proof endpoint accepts no caller-supplied destination, UID or prompt.
- Outbound operator CLI waits for answer, bounds ringing and call duration, cleans up rooms,
  and starts the correct framework. Pipecat webhooks ignore the CLI's outbound room prefix.
- The callback endpoint derives the recipient from a freshly verified Firebase identity,
  accepts only a framework and idempotency key, and starts the selected fixed agent after answer.
- A private SQLite journal preserves duplicate suppression, three concurrent calls, three
  attempts per mobile per day, one-minute cooldowns and call status across local restarts.
  Status and cancellation use a receipt scoped to one call. No visitor code execution exists.

## Validation performed

- 11 API/archive tests: allowed file list, source/download agreement, compile every Python
  file, JSON parse, rejected prompt/code overrides, fixed prompt agreement, malformed
  input, size limits, traversal rejection, local host restriction and no connected credentials.
- 7 Pipecat offline checks against the installed 1.9.0 SDK, including actual pipeline/provider
  construction, valid/invalid signed webhooks, wrong-room and web participant rejection,
  duplicate/replayed joins, concurrency admission, stale callers and cleanup.
- 9 native LiveKit offline checks against an isolated 1.8.1 SDK environment: actual provider
  construction, wrong room, missing SIP caller, hangup cleanup and duration cleanup.
- Browser interactions in Chrome at 320, 390, 768, 980 and 1440 px: both frameworks,
  file selection, fixed prompt, call-direction tabs, disconnected controls, number rendering,
  clipboard, actual ZIP downloads, initial-load failure recovery,
  no page overflow, no unexpected external requests and no JavaScript runtime errors.
- Python syntax checks, scoped Ruff checks, and Git whitespace checks.
- Runtime constructor checks verify that Pipecat does not invoke its default Smart Turn
  factory and that LiveKit resolves the configured turn mode to VAD.
- Nine additional backend tests exercise signed phone proofs, forged/expired credentials,
  wrong issuers/projects/providers, disabled/deleted/revoked accounts, outages and rate limits.
- OTP browser checks cover consent, resend cooldown, invalid code, backend outage/retry,
  expiry and mobile layout with intercepted SMS/provider responses. They also cover explicit
  callback requests, network retry idempotency, double clicks, status polling and cancellation.
- Fourteen callback backend tests cover atomic concurrent admission, durable idempotency,
  phone-based limits across identities, private status receipts, destination/prompt rejection,
  answer-gated dispatch, unanswered calls, total dial timeout and scoped cancellation/cleanup.
- Real Firebase configuration and reCAPTCHA initialization were checked in the local SMS
  browser window without sending an automated SMS. The user then confirmed that an actual
  SMS arrived and the code successfully verified on September 14, 2026.

SDK checks use dummy credentials and mocked transport/API operations. Separately, live
preflight checks confirmed model responses, synthesized voice audio and transcription,
native worker registration and Pipecat process readiness. The user's first callback reached
the phone and the carrier recorded an answered call. It was delayed before reaching the
carrier; SDK regional retries were disabled, a total 55-second dial deadline was added,
and the dedicated demo trunk was pinned to India. Subsequent Pipecat and LiveKit Agents
callbacks were both answered, and the user confirmed each agent greeted and responded in
conversation. Detailed interruption-quality and load testing remain separate. No deployed
availability is claimed.

After smart turn detector models were disabled in both examples and local workers were
restarted, the user requested another LiveKit Agents call. It was answered, the agent
connected, and the call completed. No additional spoken-conversation confirmation was
recorded for that final call; the earlier confirmations above predate the turn-setting change.

## Before connecting a public demo

After local review, implement the shared public number's inbound framework selection and
verify both call directions and audio. The prompt and code remain fixed. Callback sessions
now run locally through the dedicated LiveKit outbound trunk; their results reflect actual
SIP answer and participant state.

The standalone ZIPs use one framework per inbound trunk/dispatch rule. Running both rules
against the same trunk is not the shared-number demo implementation. A public session
broker/routing flow, shared inbound/outbound call limits, and complete phone/audio testing remain
necessary before publishing the connected service at playground.ringtrunk.com.

The Pipecat example's room claims are single-process memory. A multi-instance deployment
needs shared atomic admission. Review the preview-only host and indexing policies when
preparing a separate production configuration.

## Channel allocation status

The selected number is allocated to the requested RingTrunk account with three channels and
a separate demo trunk. The user explicitly authorized removing a different account's number
and reusing capacity from the confirmed testing account. Those releases include one live test
number and four retired test-number reservations. The resulting approved allocations fit the
recorded ten-channel carrier pool; unrelated number entitlements were preserved.

Ownership, routing, trunk permissions and channel limits were verified after the changes.
The dedicated outbound LiveKit connection is configured and a callback reached the user's
phone. Number allocation alone does not enable call controls; the private callback runtime
must also be configured. Actual numbers, account details and credentials
stay outside this repository and its downloadable examples.

For the verified local preview, set `RINGTRUNK_DEMO_ALLOCATION_STATUS=allocated` alongside
`RINGTRUNK_DEMO_NUMBER`. These display settings do not provision telephony resources.

## SMS verification configuration

The RingTrunk Auth project was linked to the existing paid billing account, Phone sign-in
was enabled, and SMS delivery was restricted to India. Existing sign-in providers were
preserved. The public Firebase app configuration is kept outside the repository; no private
Admin credential was copied into the preview. Phone verification creates an Auth identity,
but does not create a RingTrunk membership or shared customer record.

See [phone verification setup](PHONE-VERIFICATION.md) for configuration and local browser
launch instructions. No website deployment or DNS change was performed.
