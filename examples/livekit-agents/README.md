# RingTrunk + LiveKit Agents + LiveKit Cloud

A phone agent with inbound and outbound examples, built with the native LiveKit Agents SDK. RingTrunk sends
the phone call to LiveKit Cloud; an explicit dispatch rule starts `ringtrunk-livekit-demo`
in the caller's individual room. Deepgram Nova-3 recognizes speech, OpenAI GPT-4o-mini
generates text, and Cartesia Sonic-3 speaks it. The greeting identifies the assistant as AI.

The fixed playground prompt is in `agent-config.json` and is loaded as JSON data. The outbound CLI is in `call.py`. There is no email integration. Agent-session recording is explicitly off;
review your providers' separate operational logging and retention settings.

Smart turn detection is disabled with `TurnHandlingOptions(turn_detection="vad")`;
speech/silence endpointing still triggers automatic replies.

## 1. Install and configure

Use Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Inside the extracted `ringtrunk-livekit-agents` directory:

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with **your own** LiveKit Cloud URL, API key and secret, Deepgram, OpenAI and
Cartesia keys. Choose a Cartesia voice available to your account. Keep `.env` private.
The code loads only the file beside `config.py`; shell environment values take precedence.
Do not paste keys into the browser playground.

## 2. Configure the inbound trunk

Attach a number to your RingTrunk trunk. In `livekit/inbound-trunk.json`, replace the four
number placeholders with your number in the shown formats (10 digits, `+91` prefix,
`0` prefix, and `91` prefix). Use the trusted source IP shown in your RingTrunk portal
for `allowed_addresses`. Keep that allowlist narrow. Do not add digest-auth fields.

Authenticate the [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/) to your project:

```bash
lk cloud auth
lk sip inbound create livekit/inbound-trunk.json
```

Copy the returned **LiveKit inbound trunk ID** into `trunk_ids` in
`livekit/dispatch-rule.json`. This is different from a RingTrunk trunk ID.
The agent name `ringtrunk-livekit-demo` and prefix `ringtrunk-livekit-` must match `agent.py`.

```bash
lk sip dispatch create livekit/dispatch-rule.json
```

Use a dedicated demo trunk/number and one matching rule. Do not attach both downloadable
examples' rules to the same inbound trunk at once. Each incoming call creates its own room
and dispatches this named agent. No webhook server is needed for the native SDK example.

## 3. Set the RingTrunk origination URI

Find the SIP endpoint in your LiveKit Cloud project. Set the RingTrunk trunk's origination
URI to that endpoint with `:5060;transport=tcp`. Use the actual SIP endpoint, not
`LIVEKIT_URL` or a guessed hostname based on the project name.

## 4. Start the agent worker

```bash
uv run --no-project python agent.py download-files
uv run --no-project python agent.py dev
```

The development worker connects to your configured LiveKit Cloud project. It must remain
running to answer calls. The named dispatch rule selects this worker when a caller joins.
Only SIP participants in this example's room prefix are accepted.

## 5. Call your RingTrunk number

Use your phone to call the number. You should hear the AI greeting and be able to speak
with the agent using the example prompt. Hang up to end the session. The demo also
disconnects the phone leg at 120 seconds; edit `max_call_seconds` for 30–600 seconds.

Test greeting, two-way audio, interruption, hangup cleanup and the duration limit using
your own project and phone line. Local SDK checks do not establish a working carrier route.
Before a public demo, set channel/concurrency limits and provider spending limits appropriate
to your usage. Speech and LiveKit usage are billed to your accounts.

## Outbound: call your own phone

The browser demo's code and prompt are fixed. In your downloaded copy, `call.py` lets
an operator explicitly request a call using their own credentials. It is not a public
callback endpoint and its confirmation flag is not phone-ownership verification.

1. Edit `livekit/outbound-trunk.json`: use your RingTrunk trunk ID as the username,
   its SIP password, and your attached RingTrunk number in 10-digit form. Keep TCP.
2. Run `lk sip outbound create livekit/outbound-trunk.json` in your LiveKit project.
3. Set `OUTBOUND_TRUNK_ID` in `.env` to the returned LiveKit ID, and `CALLER_ID` to your
   own RingTrunk number. Keep the JSON containing your SIP password private too.
4. For LiveKit Agents, start `agent.py dev` first. Pipecat's outbound CLI starts its own
   pipeline; its webhook intentionally ignores the outbound room prefix.
5. Run the following command, replacing the placeholder with your own mobile number:

```bash
uv run --no-project python call.py +91XXXXXXXXXX --confirm-own-number
```

The script waits for an answered call before starting the agent. Ringing is bounded to
45 seconds, and the connected call uses the configured duration limit. It removes its
room on failure, hangup, or interruption. Native LiveKit also times out if its named
worker fails to join. Only Indian mobile destinations are accepted; calls to the configured
caller ID itself are rejected.

A public callback service must verify phone ownership on the server, bind the callback
recipient to that verified identity, and enforce shared channel capacity, per-phone limits,
and spending limits. Never expose this CLI as an unauthenticated web endpoint.
See [LiveKit outbound calls](https://docs.livekit.io/telephony/making-calls/outbound-calls/).

## References

- [RingTrunk documentation](https://ringtrunk.com/docs)
- [LiveKit agent dispatch](https://docs.livekit.io/agents/server/agent-dispatch/)
- [LiveKit inbound dispatch rules](https://docs.livekit.io/telephony/accepting-calls/dispatch-rule/)
- [OpenAI GPT-4o-mini](https://developers.openai.com/api/docs/models/gpt-4o-mini)

MIT licensed; see `LICENSE`.
