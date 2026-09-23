# RingTrunk Pipecat example

A [Pipecat](https://github.com/pipecat-ai/pipecat) voice agent that answers and places calls on an
Indian phone number from [RingTrunk](https://ringtrunk.com), with **LiveKit Cloud** carrying the
phone call into the room where the bot runs.

RingTrunk is Twilio for Indian phone numbers: you get a 10-digit Indian number and a SIP trunk,
and you point the platform you already use at it. No SDK, nothing to rewrite. Full docs:
[ringtrunk.com/docs](https://ringtrunk.com/docs).

## This example uses LiveKit Cloud

Pipecat does not speak SIP, so something has to turn the phone call into audio the bot can hear.
In this example that is **LiveKit Cloud's SIP service**: RingTrunk delivers the call to your
LiveKit Cloud project, LiveKit puts the caller in a room, and the Pipecat bot joins that room with
Pipecat's LiveKit transport. You need a LiveKit Cloud project for it to work.

Self-hosted LiveKit with the SIP service enabled works the same way; only the address in step 1
changes. See [Connect self-hosted LiveKit](https://ringtrunk.com/docs/livekit-self-hosted).

```text
Inbound:   caller's phone → RingTrunk number → LiveKit Cloud SIP → room "call-…" ← Pipecat bot joins
Outbound:  place_call.py → LiveKit Cloud SIP → RingTrunk trunk → the phone you dialled
```

## What is in here

| Path | What it does |
|---|---|
| `livekit/outbound-trunk.json` | LiveKit outbound trunk: your agent places calls, authenticating to RingTrunk |
| `livekit/inbound-trunk.json` | LiveKit inbound trunk: RingTrunk delivers calls to you, trusted by source IP |
| `livekit/dispatch-rule.json` | Puts each caller in their own room (`call-…`) |
| `livekit/place_call.py` | Place an outbound call from a room with the LiveKit API |
| `pipecat/bot.py` | The Pipecat pipeline that joins the room and talks to the caller |
| `pipecat/server.py` | Receives LiveKit's `participant_joined` webhook and starts the bot |

## What you need

- **A RingTrunk trunk with a number attached.** Request access at [ringtrunk.com](https://ringtrunk.com).
  You need the trunk id (`TR…`, also the SIP username), the SIP password shown when the trunk was
  created, and the 10-digit number.
- **A LiveKit Cloud project**, with the `lk` CLI signed in to it (`lk cloud auth`) and the
  project's URL, API key and API secret.
- **Keys for the bot's services:** Deepgram (speech to text), OpenAI (the language model) and
  Cartesia (text to speech). Swap any of them for another Pipecat service if you prefer.
- **A host LiveKit can reach** for the webhook, running Python 3.10 or newer.

## 1. Connect LiveKit Cloud to your RingTrunk trunk

A RingTrunk trunk carries both directions. LiveKit models that as an **outbound trunk** (you
call out, authenticating to RingTrunk) and an **inbound trunk** plus a **dispatch rule** (RingTrunk
delivers calls into a room).

```bash
cd livekit
# fill in your trunk id, password and number first
lk sip outbound create outbound-trunk.json
lk sip inbound create inbound-trunk.json
lk sip dispatch create dispatch-rule.json
```

Three things people get wrong, all covered in the files:

- **Pin the transport to TCP** on the outbound trunk. Left on automatic, LiveKit may ignore the
  SRV port and try 5060, which will not answer you.
- **No `auth_username`/`auth_password` on the inbound trunk.** RingTrunk's INVITE carries no
  credentials, exactly like Twilio Elastic SIP Trunking. Credentials there make every inbound
  call fail with 401. Trust the source IP shown on your trunk's Origination tab instead.
- **List the number in every form** the To header may take: `9XXXXXXXXX`, `+919XXXXXXXXX`,
  `09XXXXXXXXX`, `919XXXXXXXXX`.

Then, in the RingTrunk portal, set the trunk's origination URI to your LiveKit Cloud project's SIP
endpoint:

```text
<project-id>.sip.livekit.cloud:5060;transport=tcp
```

Use `<project-id>.india.sip.livekit.cloud` to keep every room in Mumbai.

## 2. Answer calls with the Pipecat bot

`pipecat/server.py` listens for LiveKit's `participant_joined` webhook and starts
`pipecat/bot.py` for the room the caller landed in.

```bash
cd pipecat
pip install -r requirements.txt
cp .env.example .env   # LiveKit Cloud URL and keys, Deepgram, OpenAI, Cartesia
uvicorn server:app --port 8080
```

In the LiveKit Cloud dashboard, point the project's webhook at
`http://<your-host>:8080/livekit-webhook`, dial your Indian number, and the bot answers.

## 3. Place a call

```bash
lk sip participant create \
  --trunk <YOUR_OUTBOUND_TRUNK_ID> \
  --call +91XXXXXXXXXX \
  --number 9XXXXXXXXX \
  --room my-room
```

or `python livekit/place_call.py +91XXXXXXXXXX`. `--number` is the caller ID and must be one of
the numbers attached to your RingTrunk trunk, or the call is rejected with `403 cli_not_allowed`.
Destinations are Indian mobile numbers only: 10 digits starting 6, 7, 8 or 9.

## Rules every call must follow

- Caller ID must be one of your own attached numbers (`403 cli_not_allowed` otherwise). This is
  an Indian regulatory requirement, enforced on every call.
- Destinations are Indian mobile numbers only (`403 destination_not_supported` otherwise).
- Codec is G.711 A-law (PCMA), 20 ms. Nothing else is negotiated.

Every SIP response and what to change is listed at
[ringtrunk.com/docs/troubleshooting](https://ringtrunk.com/docs/troubleshooting).

## More

- [Give a Pipecat voice agent an Indian phone number](https://ringtrunk.com/docs/pipecat), the
  guide this repository follows
- [Connect LiveKit Cloud to an Indian phone number](https://ringtrunk.com/docs/livekit-cloud)
- [RingTrunk documentation](https://ringtrunk.com/docs)

## License

MIT. Copyright (c) 2026 Zingaro AI Private Limited.
