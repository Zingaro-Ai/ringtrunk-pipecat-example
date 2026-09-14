# RingTrunk playground: coding-agent guide

## Start here

This is `Zingaro-Ai/ringtrunk-livekit-example`, the public playground and standalone
example repository. Check `git rev-parse --show-toplevel` and `git status --short --branch`
before editing. Other voice-agent and portal repositories in a surrounding workspace have
separate work and deployment processes.

Read [README.md](README.md), [the handoff](docs/HANDOFF.md),
[callback configuration](docs/CALLBACKS.md), and [local validation](docs/LOCAL-PREVIEW.md).
The original `pipecat/` and `livekit/` directories are legacy examples; the playground
serves the explicit `examples/` manifest in `playground/app.py`.

## Preserve these decisions

- The browser shows read-only code and a fixed prompt. Visitors may select a framework,
  verify their own phone, request a callback, and download the matching ZIP.
- Both frameworks use LiveKit Cloud. Preserve the pinned SDKs, model and voice settings
  during unrelated work. Keep smart turn detector models disabled in both examples;
  basic VAD/silence endpointing must still support automatic phone conversations.
- Callback destinations come only from a fresh, validated Firebase phone identity and
  a current-account check. Never add a visitor-supplied destination or a verification bypass.
  Status/cancellation receipts cannot authorize dialing.
- Preserve durable idempotency, phone/UID limits, answer-gated dispatch, the total dial
  deadline, disabled SDK regional dial replay, and cleanup scoped to owned demo rooms.
- One callback server owns each SQLite journal. Multiple processes/hosts and shared
  inbound/outbound admission require explicit implementation before enabling them.
- Keep credentials, actual phone numbers, account details, trunk IDs, infrastructure
  addresses and private runtime files out of this public repository and every ZIP.
  Use placeholders. Leave existing `.env*` files under the user's control.
- Source views, fixed prompts, ZIP contents and the extracted agent runtime must agree.
  Regenerate/extract downloads and restart local workers after relevant source changes,
  waiting for active calls to finish first.
- SMS verification sends an SMS only on the visitor's explicit request. No email
  notifications are part of this playground.
- Committing/pushing this branch does not deploy a service. As of September 14, 2026,
  deployment was deferred to a later session. Preserve local-only hosts and `noindex`
  until preparing the separate production configuration.

## Validation

Python 3.12+ with `uv`; preserve `uv.lock` during routine edits.

```bash
uv run --frozen python -m unittest discover -s tests -p 'test_*.py'
uv run --frozen ruff check playground examples tests
uv run --frozen python -m compileall -q playground examples tests
git diff --check
```

For changes to an agent, run `tests/check_runtime.py` against a newly extracted ZIP
using that framework's environment. Browser checks and their Chrome setup are documented
in the README. They mock SMS/calls; do not confuse them with real phone/audio validation.
Do not place additional calls or send SMS merely to prepare a commit.

Before a public push, inspect the staged files for secrets and deployment-specific data.
Use imperative commit subjects and preserve unrelated work. Record implemented, locally
tested, deployed and user-confirmed behavior separately in the handoff.
