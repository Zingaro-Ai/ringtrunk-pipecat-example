# RingTrunk voice agent playground

Explore **Pipecat** and **LiveKit Agents**, both using **LiveKit Cloud**, with inbound and outbound
RingTrunk phone-agent examples. The playground has read-only code and a fixed prompt,
call-direction tabs, and ZIP downloads containing source, the demo prompt, credential placeholders,
and setup instructions.

**Current status: local review.** Firebase SMS verification and the verified-number callback
are connected locally. Choose an agent, verify your own mobile, then press **Call my phone**.
The local service dials through a dedicated LiveKit Cloud outbound trunk and starts the selected
agent after answer. The playground has not been deployed. Shared-number inbound
selection still needs implementation and testing. The user confirmed working callbacks and
two-way conversation with both frameworks during local review.

## Run the local preview

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uv sync --frozen
uv run --frozen uvicorn playground.app:app --host 127.0.0.1 --port 4173
```

Open **http://localhost:4173**. No API keys are needed to view the playground or download
examples. Fonts and assets are served locally. The server accepts local preview hosts and sends
`noindex` headers for this review build. The fixed prompt comes from the server. Download requests cannot override source or prompts.
Set the optional `RINGTRUNK_DEMO_NUMBER` process variable to display a selected E.164 demo number;
it is never inserted into example files or archives. This does not connect the phone line.

See [Firebase phone verification](docs/PHONE-VERIFICATION.md) to configure SMS and launch
the local SMS preview. Firebase's browser bundle is included; rebuild it after SDK changes
with `npm ci` and `npm run build:auth`.
See [local callbacks](docs/CALLBACKS.md) for the private runtime configuration and call limits.
See [the development handoff](docs/HANDOFF.md) for the saved state and next deployment steps,
and [AGENTS.md](AGENTS.md) for coding-agent guidance.

## What visitors can do

- Choose Pipecat or LiveKit Agents and inspect every file included in its ZIP.
- Read the fixed receptionist prompt and choose inbound or outbound call mode.
- Copy the displayed source. Agent code is read-only in the browser.
- Download a standalone example with the fixed prompt and an answer-aware outbound CLI.
- Verify an Indian mobile number using Firebase SMS, when verification is configured.
- Request a two-minute AI callback to that verified number, see its status and end the call.
- Follow the ZIP's README to connect their own RingTrunk number and LiveKit Cloud project.

The downloaded agents accept inbound SIP callers and outbound calls initiated by `call.py`. Both use Deepgram Nova-3, OpenAI
GPT-4o-mini, and Cartesia Sonic-3. Each caller gets an individual LiveKit room. Pipecat
starts from a signed SIP-join webhook; LiveKit Agents uses explicit named dispatch.
Smart turn detector models are disabled in both examples. Pipecat uses a fixed silence
timeout and LiveKit Agents uses VAD endpointing, so phone conversations still reply automatically.

| Path | Purpose |
| --- | --- |
| `playground/app.py` | Local server, allowlisted source manifest, ZIP builder |
| `playground/phone_auth.py` | Signed Firebase phone proofs and current-account verification |
| `playground/callbacks.py` | Verified callbacks, durable admission limits and room cleanup |
| `playground/agent_process.py` | Isolated local processes using the downloadable agents |
| `playground/static/` | Responsive UI, exact RingTrunk brand assets, local fonts |
| `examples/pipecat/` | Pipecat pipeline, webhook admission, inbound dispatch and README |
| `examples/livekit-agents/` | Native agent, named inbound dispatch and README |
| `examples/shared/` | Prompt loader, JSON config, credential and trunk templates |
| `tests/test_playground.py` | API, ZIP, prompt isolation and input-validation checks |
| `tests/test_phone_auth.py` | Signed, forged, expired and revoked phone-proof checks |
| `tests/test_callbacks.py` | Callback authorization, idempotency, limits and SIP lifecycle |
| `tests/check_runtime.py` | Offline checks using each example's installed SDK |
| `tests/browser-check.mjs` | Browser interactions, downloads, responsive and failure states |
| `tests/browser-phone-check.mjs` | OTP and callback flows with mocked SMS and calls |

`examples/` contains ZIP templates. Download a ZIP before running an agent: the builder
places shared files beside the framework's source so the result is self-contained. The
explicit manifest excludes real environment files, credentials and legacy outbound scripts.
Direct framework dependencies are pinned; `uv.lock` locks the local playground environment.

## Validation

```bash
uv run --frozen python -m unittest discover -s tests -p 'test_*.py' -v
uv run --frozen ruff check playground examples tests
uv run --frozen python -m compileall -q playground examples tests
git diff --check
```

For each downloaded ZIP, install its requirements in a separate virtual environment and
run the matching offline check using that environment's Python:

```bash
/path/to/pipecat/.venv/bin/python tests/check_runtime.py pipecat /path/to/ringtrunk-pipecat
/path/to/livekit/.venv/bin/python tests/check_runtime.py livekit-agents /path/to/ringtrunk-livekit-agents
```

The runtime checks use placeholder credentials and mock all call/API operations. They
exercise real SDK imports and provider construction, webhook signatures and duplicate
admission, room scoping, and agent cleanup. They do not establish carrier reachability,
two-way audio, interruption quality or live call behavior.

For browser checks, run the local server and a separate Chrome debug session:

```bash
google-chrome --headless=new --disable-dev-shm-usage --no-first-run --no-default-browser-check \
  --user-data-dir=/tmp/ringtrunk-playground-chrome --remote-debugging-port=9331 \
  --remote-debugging-address=127.0.0.1 about:blank
node tests/browser-check.mjs
# Requires Firebase public app configuration on the local server; SMS is mocked:
node tests/browser-phone-check.mjs
```

Browser checks require Node 22+ and save screenshots and downloaded archives in
`/tmp/ringtrunk-playground-browser`. See [local review notes](docs/LOCAL-PREVIEW.md) for
what was verified and what remains before a connected demo.

## Local callback and remaining public demo work

The selected demo number has three channels allocated to the requested RingTrunk account.
Firebase SMS verification is implemented. The backend derives the verified number from a
signed Firebase token and confirms the current account; it rejects caller-supplied destinations.
The call endpoint accepts only a framework and idempotency key. A private SQLite journal caps
the local service at three simultaneous calls, three requests per mobile per 24 hours and one
request per minute. Tokens and phone numbers are not written to that journal. Status polling
and cancellation use a receipt restricted to one call. The fixed examples run in separate
Python environments using private runtime credentials.

Inbound framework selection, shared inbound/outbound admission and inbound phone/audio validation
remain necessary before publishing. The local callback service deliberately permits one server
process per journal. Production hosting and shared admission across hosts need a separate review.
Deployment is deferred to the next user-directed session; see the [handoff](docs/HANDOFF.md).

## Earlier examples

The original `pipecat/` and `livekit/` folders are preserved. They include earlier inbound
and outbound examples, documented in [the original setup guide](docs/legacy-setup.md).
The new downloadable examples include a separate answer-aware `call.py`. The public callback
service adds verified-number authorization and limits; the operator CLI is for calls to a
number the operator owns and does not implement public phone verification.

## License

MIT, copyright 2026 Zingaro AI Private Limited. Local font licenses are included in
`playground/static/fonts/`. RingTrunk brand assets retain their existing ownership.
