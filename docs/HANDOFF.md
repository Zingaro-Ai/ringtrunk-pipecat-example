# Playground handoff — September 14, 2026

The user requested committing and pushing `playground-local-preview`, updating the docs,
and deferring deployment until September 15. This is a local preview with connected
outbound demos. No website or agent service has been deployed for the playground.

## Saved implementation

| Area | State |
| --- | --- |
| Browser playground | Pipecat/LiveKit selection, read-only source and prompt, ZIP downloads, responsive RingTrunk UI |
| Phone verification | Firebase SMS/reCAPTCHA, recent signed phone identity and current-account verification |
| Outbound demo | Explicit callback to the verified mobile only; answer-aware agent start, status and cancellation |
| Limits | Three active callbacks, three attempts per phone/UID per rolling day, one-minute cooldown, two-minute call bound |
| Persistence | Private SQLite journal with keyed identity/phone hashes, durable request IDs and one process owner |
| Runtime | Separate local Python environments, Pipecat 1.9.0 and LiveKit Agents 1.8.1, both on LiveKit Cloud |
| Turn behavior | Smart turn detector models disabled; basic speech/silence detection enabled |
| Shared-number inbound demo | Pending routing/framework selection and real-call validation; UI reports it unavailable |

Both agents retain Deepgram Nova-3, OpenAI GPT-4o-mini and Cartesia Sonic-3. Pipecat uses
`SpeechTimeoutUserTurnStopStrategy`; native LiveKit uses
`TurnHandlingOptions(turn_detection="vad")`. Do not remove automatic endpointing or switch
to manual turns: a phone caller has no browser control to end each utterance.

The first callback was delayed before reaching the carrier. The corrected callback service
disables SDK regional replay of a dial request, applies a 55-second total dial deadline,
and uses an India-pinned dedicated outbound trunk. Preserve these choices when packaging
the runtime. The standalone outbound CLI also prevents regional dial replay when supported
by its installed API SDK.

## Evidence and its limits

- The user received and verified a real SMS in the local browser.
- The user confirmed that Pipecat and LiveKit Agents callbacks each arrived, greeted them,
  and responded in conversation.
- After the requested smart-turn change, another native LiveKit callback was answered,
  connected to its agent and completed. A further spoken-conversation confirmation was
  not recorded for this last call.
- Automated validation covers 34 backend tests, seven Pipecat runtime checks and nine
  native LiveKit runtime checks. The runtime checks use actual pinned SDK constructors
  with mocked call operations, including assertions that smart turn models are not selected.
- Browser checks cover both source viewers/downloads and responsive/error states, plus
  mocked OTP and callback requests, retries, duplicate clicks, status and cancellation.

Detailed results are in [LOCAL-PREVIEW.md](LOCAL-PREVIEW.md). Load behavior, public-host
operation, inbound routing and inbound audio are not established by these local tests.

## Resume local work

Start with `uv sync --frozen` and the [README](../README.md) preview command. Source browsing
and downloads work without credentials. Connected calls additionally need the private
Firebase configuration, callback configuration, existing journal/hash key and extracted
agent environments described in [CALLBACKS.md](CALLBACKS.md).

Those files are intentionally outside Git. A clone does not include an authenticated
runtime, call history or reusable phone proof. Recreate missing configuration from the
operator's credential store. Preserve the existing journal and hash key across restarts;
do not reset admission history to get another public test call. The phone proof lives in
browser memory and a refresh requires verification again.

Extract current ZIPs into the configured framework directories before starting workers.
The local supervisor imports those extracted files, so restarting without refreshing
them can run an older agent than the source viewer shows. Do not overwrite private
environment files during extraction.

## Next deployment session

1. Implement the shared demo number's inbound framework selection and admission across
   inbound/outbound calls. The standalone ZIPs each assume one framework per inbound
   trunk; attaching both dispatch rules to one trunk does not implement public selection.
2. Package the preview and both agent environments for the chosen host, with durable
   private configuration/state outside the checkout. The current callback service supports
   one process per journal; horizontal replicas need shared atomic admission first.
3. Prepare HTTPS and the production hostname/origin configuration. The current server
   allows only local preview hosts and always sends `noindex, nofollow`. Decide and test
   the public page's indexing policy as part of that production change.
4. Recheck the dedicated demo number/trunk and channel allocation against current provider
   state. Reuse the dedicated demo resources; keep unrelated customer and platform trunks
   intact. Starting the local preview only verifies its trunk; it does not provision one.
5. Validate fresh SMS verification, both frameworks in both call directions, speech/replies,
   hangup, unanswered calls, deadlines, duplicate requests, capacity refusal and restart
   cleanup on the intended host. Record actual results before claiming public readiness.
6. Deploy in the user-directed deployment session and verify the public origin. This commit
   does not change DNS, launch containers or deploy either framework to LiveKit Cloud.

Keep public examples generic. Actual number/account assignments, provider credentials and
machine-specific runtime paths belong in private operational notes, never in this handoff.
