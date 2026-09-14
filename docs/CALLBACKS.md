# Verified callbacks in the local preview

Choose Pipecat or LiveKit Agents, open **Call my phone**, verify your own Indian mobile
by SMS, then press **Call my phone**. Verification is a separate step from dialing.
The selected agent starts after the phone answers. The page shows preparing, calling,
answered, connected and terminal states, and provides **End call**.

## Runtime configuration

Use the [phone verification configuration](PHONE-VERIFICATION.md) and set
`RINGTRUNK_CALL_CONFIG` to a private JSON file outside the checkout. Its schema is:

```json
{
  "environment": {
    "LIVEKIT_URL": "wss://YOUR_PROJECT.livekit.cloud",
    "LIVEKIT_API_KEY": "YOUR_KEY",
    "LIVEKIT_API_SECRET": "YOUR_SECRET",
    "DEEPGRAM_API_KEY": "YOUR_KEY",
    "OPENAI_API_KEY": "YOUR_KEY",
    "CARTESIA_API_KEY": "YOUR_KEY",
    "CARTESIA_VOICE_ID": "YOUR_VOICE_ID"
  },
  "outbound_trunk_id": "YOUR_DEDICATED_LIVEKIT_OUTBOUND_TRUNK",
  "caller_id": "+91YOUR_NUMBER",
  "state_dir": "/absolute/private/callback-state",
  "hash_key": "A_STABLE_RANDOM_SECRET_AT_LEAST_32_CHARACTERS_LONG",
  "agents": {
    "pipecat": {
      "python": "/absolute/pipecat-venv/bin/python",
      "directory": "/absolute/extracted/ringtrunk-pipecat"
    },
    "livekit-agents": {
      "python": "/absolute/livekit-venv/bin/python",
      "directory": "/absolute/extracted/ringtrunk-livekit-agents"
    }
  }
}
```

Extract the current ZIPs and install each one's requirements in a separate Python environment.
The service runs those files with their fixed `agent-config.json`; it accepts no executable
paths, prompt, phone number or source code from a visitor. Keep the configuration file mode
`0600` and its parent directory `0700`. Preserve `hash_key` across restarts so admission
history and receipts remain valid. Never include this file in a ZIP or Git commit.

The dedicated outbound LiveKit trunk must list only the configured demo number, in ten-digit
form, and use that number's own RingTrunk trunk credentials. Provisioning is an explicit
operator action; starting the preview verifies the resource and does not create or modify it.
Do not reuse another customer's trunk or a production platform trunk.
Set `destination_country` to `in` on the dedicated outbound trunk so domestic calls originate
through LiveKit's India region; see [telephony region pinning](https://docs.livekit.io/telephony/features/region-pinning/).

Start the preview with the configuration path alongside the Firebase/display variables:

```bash
RINGTRUNK_CALL_CONFIG=/absolute/private/callback-runtime.json \
RINGTRUNK_FIREBASE_CONFIG=/absolute/private/firebase-client.json \
uv run --frozen uvicorn playground.app:app --host 127.0.0.1 --port 4173
```

This starts the native worker on the developer's machine, registered with the dedicated name
`ringtrunk-playground-livekit`. Pipecat imports in a separate process before dialing and joins
after answer. Both connect to the configured LiveKit Cloud project. No agent is deployed to
Cloud or to a production VM by this command.

Both examples disable smart turn detector models. Pipecat uses
`SpeechTimeoutUserTurnStopStrategy`; LiveKit Agents explicitly uses VAD turn detection.
Basic speech/silence detection remains enabled so callers can speak without manual turn controls.
After changing example source, extract fresh ZIPs into the configured agent directories and
restart the local preview after active calls finish. The worker imports those extracted files;
editing the source viewer alone does not update an already running agent process.

## Authorization, limits and cleanup

- `POST /api/calls`: Firebase phone bearer token, plus `{framework, request_id}` only.
  The request ID is a UUID v4. Duplicate retries return the original call receipt.
- `GET /api/calls/{call_id}`: `X-Call-Token` authorizes status for exactly that call.
  `POST /api/calls/{call_id}/cancel` uses the same receipt to stop that call. The receipt
  cannot authorize another outbound call.
- At most three active callbacks across both frameworks, with one dial start per second.
  Each verified phone/UID gets three attempts per rolling 24 hours and a one-minute gap.
- Ringing is limited to 45 seconds and SIP duration to two minutes. Dispatch waits for SIP
  answer. Busy, unreachable and unanswered results do not claim a connected conversation.
- The complete dial API operation has a 55-second deadline. SDK regional request replay is
  disabled, so a timed-out dial cannot automatically place another call in a different region.
- SQLite transactions preserve admission and idempotency across restarts. Only keyed hashes
  of the identity and phone are stored, along with random IDs, framework, times and status.
  Firebase tokens, plaintext phone numbers, audio and transcripts are not stored by this app.
  Firebase and the telephony providers retain their own account/call records.
- A process lock permits one callback server per journal. On restart, only recorded unfinished
  demo rooms are cleaned up. Cancellation, caller hangup and shutdown also clean up owned rooms.
  The SIP duration bound remains in effect if local cleanup fails; further calls fail closed.

## Validation

The backend tests exercise authorization, request overrides, atomic concurrency, limits,
idempotency, scoped status receipts, answer gating, unanswered calls and cancellation.
Browser tests intercept SMS/call APIs and cover retrying an interrupted request without
duplicating a dial, double clicks, status changes and ending a call.

Live preflight checks verified the dedicated trunk, native worker registration, Pipecat
process readiness, model responses, synthesized speech and transcription. On September 14,
2026, the user then confirmed that callbacks from both frameworks arrived and each agent
greeted and responded in conversation. Shared-number inbound selection, load testing and
public deployment remain separate work.

Reference: [LiveKit outbound calls](https://docs.livekit.io/telephony/making-calls/outbound-calls/).
