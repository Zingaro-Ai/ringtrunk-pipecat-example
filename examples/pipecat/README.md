# RingTrunk + Pipecat + LiveKit Cloud

A phone agent with inbound and outbound call examples. RingTrunk delivers a call to LiveKit Cloud;
a verified SIP participant webhook starts a Pipecat speech pipeline in that caller's room.
Speech recognition is Deepgram Nova-3, the text model is OpenAI GPT-4o-mini, and speech
synthesis is Cartesia Sonic-3. The greeting identifies the assistant as an AI demo.

The fixed playground prompt is in `agent-config.json`. It is loaded as JSON data, never executed
as Python. The outbound CLI is in `call.py`. There is no email integration or recording code.
Smart Turn is disabled. Speech/silence detection and a fixed timeout trigger automatic replies.
Your configured services may retain their own operational logs; review their settings.

## 1. Install and configure

Use Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Run these commands inside the extracted `ringtrunk-pipecat` directory:

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with **your own** LiveKit Cloud URL, API key and secret, plus Deepgram,
OpenAI and Cartesia keys. Choose a Cartesia voice available to your account. Keep `.env`
private. The code loads only the `.env` beside `config.py`; shell environment values win.
The browser playground never needs or receives these keys.

## 2. Configure the inbound trunk

Get a RingTrunk trunk with a number attached. In `livekit/inbound-trunk.json`:

- Replace the four number placeholders with your number in the formats shown:
  10 digits, `+91` plus 10 digits, `0` plus 10 digits, and `91` plus 10 digits.
- Replace the allowed source IP placeholder with the source address shown in your
  RingTrunk portal. Do not use a wildcard allowlist or add digest-auth fields here.

Authenticate the [LiveKit CLI](https://docs.livekit.io/intro/basics/cli/) to the intended
project and create the trunk:

```bash
lk cloud auth
lk sip inbound create livekit/inbound-trunk.json
```

Copy the **LiveKit inbound trunk ID returned by that command** into `trunk_ids` in
`livekit/dispatch-rule.json`. This is not your RingTrunk trunk ID.
Keep the `ringtrunk-pipecat-` room prefix; the webhook checks it.

```bash
lk sip dispatch create livekit/dispatch-rule.json
```

Use a dedicated demo trunk/number, with just one matching dispatch rule. Do not install
the Pipecat and native LiveKit example rules on the same inbound trunk simultaneously.
Switch its rule deliberately when changing frameworks. Each caller gets an individual room.
Pipecat does not use `room_config.agents`; its webhook starts the pipeline instead.

## 3. Point RingTrunk at LiveKit Cloud

Copy the **SIP endpoint** from your LiveKit Cloud project, then set your RingTrunk trunk's
origination URI to that endpoint with `:5060;transport=tcp`. The SIP endpoint is different
from `LIVEKIT_URL`; do not infer it from the project display name.

## 4. Start the webhook server

```bash
uv run --no-project uvicorn server:app --host 127.0.0.1 --port 8080 --workers 1
```

Expose `/livekit-webhook` through a secure HTTPS reverse proxy or development tunnel.
In LiveKit Cloud, add that public HTTPS URL as a webhook endpoint signed by the same
LiveKit API key configured in `.env`. A localhost URL cannot receive Cloud webhooks.
The endpoint verifies the signature and accepts only SIP participants in this example's
room prefix. Normal web participants do not start agents.

## 5. Call your number

Call your RingTrunk number from a phone. You should hear the AI demo greeting, then the
agent should respond using the example prompt. Hang up to finish. The example ends
the phone leg after 120 seconds by default; `max_call_seconds` allows 30–600 seconds.

Validate on your own project: greeting, two-way audio, caller interruption, caller hangup,
maximum-duration hangup, invalid webhook rejection, and duplicate webhook delivery.
Local code checks do not establish carrier connectivity or voice quality.

## Runtime limits

This small webhook server allows four concurrent agents and claims rooms in memory.
Use **one worker and one instance**. Multiple workers, rolling restarts, or replicas require
a shared atomic admission store to prevent duplicate agents. Add upstream call/channel
limits before exposing a public phone demo. On overload the webhook returns 503; this
example does not implement a spoken busy message. Provider usage is billed to your accounts.

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

- [RingTrunk Pipecat guide](https://ringtrunk.com/docs/pipecat)
- [Pipecat LiveKit transport](https://docs.pipecat.ai/api-reference/server/services/transport/livekit)
- [LiveKit inbound dispatch rules](https://docs.livekit.io/telephony/accepting-calls/dispatch-rule/)
- [OpenAI GPT-4o-mini](https://developers.openai.com/api/docs/models/gpt-4o-mini)

MIT licensed; see `LICENSE`.
