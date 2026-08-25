# Shift Handover Intelligence — AI Actions App

A Freshworks AI Actions app that exposes two operations to Freshservice **AI Agent Studio**.

This is the Stage 2 component of the Shift Handover Intelligence project. The Stage 1 agent works entirely in Agent Studio using no-code Custom API actions; this app replaces the fragile parts of that with real server-side logic.

---

## What it does

| Action | Purpose |
|---|---|
| `build_shift_context` | One call that assembles the complete picture of a shift — tickets on the line, open maintenance, repeat fault signatures, changes scheduled in the incoming window, and any **collision** between them — and returns carryovers already classified by risk category. |
| `score_carryover_risk` | Classifies a list of carryover items into the four policy risk categories, ranks them, and flags which require explicit acknowledgement before the handover can close. |

---

## Why it exists

The Stage 1 agent calls three separate no-code API actions and asks the LLM to spot patterns across the raw JSON. That works for a demo and fails in production for three specific reasons, all of them platform limits rather than design choices:

1. **Agent Studio cannot iterate or group.** It *does* ship an Execute Function node with 14 pre-built Functions — arithmetic, `Compare Dates`, `Get Days Between`, string ops — so scalar transformation is available and claiming otherwise is wrong. What has no construct is walking an unbounded collection and aggregating over it. `findRepeatFaults()` groups N tickets by fault signature and sums downtime per group; `detectCollisions()` is an O(n×m) cross-product. Neither is expressible in the builder.
2. **Three sequential API actions is three chances to fail**, each configured per-account by hand, with no version control and no tests.
3. **Nothing in a no-code build is portable** — it lives in one account and cannot be shared, versioned or installed elsewhere.

This app moves all of it server-side. One call in, structured verdict out. The LLM is left doing what it is actually good at — conducting the conversation — instead of performing set operations on JSON in its head.

The clearest example is `quiet_shift_claim_valid`. That single boolean is computed from the record and lets the agent refuse *"quiet shift, nothing to report"* **with evidence**. No prompt engineering can make that reliable; a comparison in code makes it deterministic.

---

## Requirements

- Node.js **24.x**
- FDK **10.x**
- Freshservice **ITSM** or **ESM** (AI Actions are not supported on Freshservice MSP)

---

## Setup

```bash
npm install
fdk validate
```

Installation parameters (`config/iparams.json`), supplied at install time:

| Param | Notes |
|---|---|
| `freshservice_domain` | e.g. `acme.freshservice.com`, no scheme |
| `freshservice_api_key` | **secure** — an agent key with read access to tickets, changes, problems |
| `default_line_id` | optional fallback, e.g. `CBE-L3` |

## Local testing

```bash
fdk run
# open https://localhost:10001/web/test
# select "actions" from the dropdown, pick an action, click Simulate
```

Sample payloads live in `server/test_data/`.

## Unit tests

```bash
npm test          # vitest run --coverage
```

`fdk pack` refuses to build below 80% coverage on statements, branches, functions and lines. Current state:

```
File       | % Stmts | % Branch | % Funcs | % Lines
server.js  |   98.63 |   85.84  |   100   |  98.63     62 tests passing
```

## Publish

```bash
fdk validate
fdk pack
```

Upload the resulting archive in the Freshworks Developer Portal. When prompted **"Where can this app's actions be used?"** select **AI Agent Studio**.

> AI Actions apps can currently be published only as **Custom apps**. Publishing them as public Freshworks Marketplace apps is not supported at the time of writing.

Once installed, the two actions appear in Agent Studio under **Your apps** in any workflow's **API Action** node, with input mapping, a **Test API** button and output configuration.

---

## Structure

```
ai-actions-app/
├── manifest.json            platform 3.0, node 24, request templates registered
├── actions.json             the two action contracts
├── config/
│   ├── iparams.json         installation parameters
│   └── requests.json        request templates for tickets, changes, problems
├── server/
│   ├── server.js            action handlers + pure logic
│   └── test_data/           sample payloads for fdk run
├── tests/
│   └── server.test.js       62 unit tests
└── vitest.config.js         80% thresholds, matching the fdk pack gate
```

## Design notes

- **Action keys are case-sensitive** and must match the exported callback names in `server/server.js` exactly. A mismatch fails at `fdk validate` or at runtime.
- Every request template invoked is registered in `manifest.json → modules.common.requests`.
- The pure logic (parsing, grouping, window comparison, collision detection) is separated from the I/O so it can be unit tested without network access.
- `module.exports` at the foot of `server.js` is inert in production — the FDK reads the global `exports` assignment. It exists so vitest can exercise both the helpers and the handlers.
