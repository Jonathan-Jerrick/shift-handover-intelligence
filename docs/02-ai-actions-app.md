# How the AI Actions App Works

**This document exists to be presented.** It explains the Stage 2 component in enough detail to demo and defend it, whether or not it is deployed at the time of the demo.

Source: `ai-actions-app/` — complete, 62 tests passing, 98.63% statement coverage.

---

## 1. The one-sentence version

> Stage 1 asks a language model to spot patterns across three separate blobs of JSON. Stage 2 does that in code, deterministically, and hands the model a verdict instead of raw data.

---

## 2. Why it is needed — one real limit, honestly stated

> **Correction, 25 August 2026.** An earlier draft of this document claimed Agent Studio "cannot do arithmetic" and "cannot compare dates." **That is false**, and it was corrected before submission. Agent Studio ships an **Execute Function** node and 14 pre-built Functions, including `Arithmetic Expression`, `Add Numbers`, `Compare Dates`, `Get Days Between`, `Add Time to Date`, `Format Date`, `Get Current Date`, `Concatenate`, `Split` and `Extract from List`. Anyone who knows the platform would have caught the overclaim, so the argument below is narrowed to what is actually true.

The Stage 1 agent works. It calls three no-code Custom API actions and lets the LLM reason across the results. Three things still justify moving that server-side.

**Limit 1 — Agent Studio cannot iterate or group. [Verified]**

This is the real one. The Functions library handles scalar transformation: take two numbers, add them; take two dates, compare them; take a list and pull the element at index 3. What it has no construct for is **iterating an unbounded collection and aggregating over it**.

The core operation in this product is:

> for every ticket on this line, extract the fault code, group by signature, count occurrences per signature, sum downtime per signature, and return only signatures seen more than once

There is no loop node, no map, no reduce, no group-by. `Extract from List` returns one element at a known index — it does not walk a list of unknown length. `findRepeatFaults()` in `server/server.js` is 25 lines of ordinary JavaScript and is simply not expressible in the builder.

The same applies to `detectCollisions()`, which is an O(n×m) cross-product of every unresolved carryover against every scheduled activity. `Compare Dates` can compare *one* pair. It cannot compare *every* carryover against *every* change.

**Limit 2 — Three sequential API actions is three failure points.** Each is hand-configured per account through a web form. No version control, no tests, no way to know one has drifted until a handover silently returns incomplete context. During this build one of the three failed silently with `access_denied` because of a malformed auth header, and the agent simply answered without that data. A packaged app fails loudly at install time instead.

**Limit 3 — nothing in the no-code build is portable.** This is the Track 2 argument. Three Custom API actions plus a Function chain live in one account and cannot be shared, versioned, reviewed or installed anywhere else. An AI Actions app is a distributable unit any Freshservice customer can install — which is precisely what "reusable skills" in the Track 2 brief means.

## 3. The two actions

### `build_shift_context`

**In**

| Parameter | Type | Notes |
|---|---|---|
| `line_id` | string | required — e.g. `CBE-L3` |
| `shift_window_hours` | integer | defaults to 8 |
| `incoming_shift_hours` | integer | how far ahead to look for scheduled activity, defaults to 8 |

**Out** — the fields that matter:

| Field | What it carries |
|---|---|
| `stoppage_count` | how many stoppages on this line this shift |
| `total_downtime_minutes` | summed across every stoppage — *aggregation over a collection, which the builder cannot express* |
| `repeat_faults[]` | fault signatures seen 2+ times, with occurrences, events and total downtime — **the group-by the builder has no construct for** |
| `open_maintenance[]` | maintenance jobs opened this shift and not closed |
| `scheduled_in_window[]` | changes whose planned start falls inside the incoming shift — a `Compare Dates` Function could do one pair; this filters the whole set |
| **`collisions[]`** | **the product** — see §4 |
| `carryovers[]` | everything above, already classified by risk category and sorted |
| `known_problems[]` | open problem records matching this line |
| **`quiet_shift_claim_valid`** | boolean — see §5 |

### `score_carryover_risk`

Takes a list of carryover items and the incoming shift letter. Returns them ranked by category, flags which require acknowledgement, and escalates when Category 1 or 2 items are being handed into **Shift C** — because the escalation matrix says the Maintenance Lead is on call rather than on site overnight, so the same item carries materially more risk at 22:00 than at 06:00.

---

## 4. The collision detector — the part to demo

This is the code that encodes the entire insight:

```js
function detectCollisions(openMaintenance, repeatFaults, scheduledInWindow, lineId) {
  if (!scheduledInWindow.length) return [];
  const collisions = [];

  scheduledInWindow.forEach((change) => {
    openMaintenance.forEach((job) => {
      collisions.push({
        severity: 'high',
        carryover_category: CATEGORY.MAINTENANCE_OPEN,
        carryover: job.subject,
        scheduled_activity: change.subject,
        starts_in_hours: change.hours_from_now,
        mechanism:
          'Equipment is in a partially worked state and has not been confirmed back to ' +
          'specification. The scheduled activity assumes the line is in a known good state.'
      });
    });

    repeatFaults.forEach((fault) => {
      collisions.push({
        severity: 'high',
        carryover_category: CATEGORY.RECURRING_FAULT,
        carryover: `Recurring fault ${fault.fault_code}, ${fault.occurrences} occurrences`,
        scheduled_activity: change.subject,
        starts_in_hours: change.hours_from_now,
        mechanism:
          'A tooling changeover power-cycles the safety interlock chain. An interlock that ' +
          'is intermittently dropping out during normal running will frequently fail to ' +
          're-establish after a power cycle, leaving the line unable to restart.'
      });
    });
  });

  return collisions;
}
```

Two things to point at when presenting this:

**It returns a `mechanism`, not a score.** Most risk tooling gives you a number and leaves you to work out why. This returns the causal chain in a sentence, which is what a supervisor at 22:00 can actually act on. An agent that says *"high risk: 8.4"* gets ignored. An agent that says *"the changeover power-cycles the interlock that failed twice tonight"* gets listened to.

**The cross-product is the point.** Every unresolved carryover is checked against every scheduled activity on the same line. That is an O(n×m) sweep — trivial in code, and not expressible in the builder because it needs nested iteration rather than a single comparison, and the exact operation no human performs at shift change because the outgoing supervisor is thinking about the past and the incoming one about the future.

---

## 5. `quiet_shift_claim_valid` — one boolean that carries the product

```js
quiet_shift_claim_valid:
  stoppages.length === 0 &&
  openMaintenance.length === 0 &&
  scheduledInWindow.length === 0
```

Five lines. It is the most important field in the response.

The failure mode named in the HSE literature is the free-text logbook that accepts *"quiet shift, nothing to report"* from a supervisor who left a safety valve removed. Piper Alpha's handover was "complete" in exactly that sense.

No amount of prompt engineering makes an LLM reliably refuse that claim, because refusing requires knowing the record contradicts it. **A comparison in code makes it deterministic.** The agent can now say:

> *"I can see two stoppages and an open maintenance job on this line this shift. Which of those are you calling quiet?"*

That is the difference between a chatbot with a good personality and a control.

---

## 6. Where it plugs into Agent Studio

```
FDK app  →  fdk validate  →  fdk pack  →  Developer Portal upload
         →  "Where can this app's actions be used?"  →  AI Agent Studio
         →  install as a Custom app
         →  actions appear under "Your apps" in any API Action node
```

Once installed, WF1 collapses from three API Action nodes to one:

**Before (Stage 1)**

```
Collect info → API Action (get_shift_record)
             → API Action (get_scheduled_changes)
             → API Action (get_open_problems)
             → Custom response  ← LLM reasons across three raw JSON blobs
```

**After (Stage 2)**

```
Collect info → API Action (build_shift_context)
             → Custom response  ← LLM receives a structured verdict
```

Four nodes become two. Three failure points become one. And the collision detection moves from *"hopefully the model notices"* to *"the function returns it or it doesn't."*

Note what this does **not** claim: you could rebuild parts of this with Execute Function nodes. What you could not rebuild is the grouping over an unbounded ticket list, the cross-product collision sweep, or the packaging that makes the whole thing installable elsewhere.

---

## 7. Constraints observed

Verified against the Freshworks App SDK v3.0 documentation:

| Requirement | How this app satisfies it |
|---|---|
| Action keys case-sensitive, must match exported callbacks | `build_shift_context` and `score_carryover_risk` match exactly in `actions.json` and `server/server.js` |
| Request templates registered in manifest | all three under `modules.common.requests` |
| Platform 3.0, Node 24.x, FDK 10.x | declared in `manifest.json → engines` |
| `renderData(null, data)` success / `renderData({status, message})` failure | both handlers, with try/catch |
| Secrets marked secure | `freshservice_api_key` has `"secure": true` |
| `fdk pack` coverage gate: 80% on all four metrics | 98.63% statements · 85.84% branches · 100% functions · 98.63% lines |
| `fdk-unit-test` script, `vitest.config.js`, `tests/` present | all three |
| Supported products | Freshservice ITSM and ESM. **Not** MSP. |
| Publishing scope | **Custom app only** — public Marketplace listing is not supported for AI Actions apps |

---

## 8. Testing it without deploying

```bash
conda activate freshworks
cd ai-actions-app

npm test        # 62 unit tests, coverage report

fdk run         # then https://localhost:10001/web/test
                # dropdown → "actions" → build_shift_context → Simulate
```

`server/test_data/build_shift_context.json` and `score_carryover_risk.json` hold ready payloads, so Simulate works with no typing.

**For the demo:** running `npm test` on camera and showing 62 green tests plus the coverage table is a fast, credible way to prove the Stage 2 component is real code rather than a slide. It takes four seconds.

---

## 9. What is deliberately not built yet

Honest scope boundaries, worth stating rather than hiding:

- **No embedding-based similarity.** Fault grouping is done by extracted fault code, which is exact-match. Real plants have faults described in prose with no code at all; that wants embeddings and is a Stage 2 stretch.
- **No anomaly baseline.** `build_shift_context` reports what happened this shift but does not compare it against a 30-day norm. *"Three stoppages is unusual for this line"* is a more valuable sentence than *"three stoppages"*, and it needs history the demo instance does not have.
- **No write actions.** The app reads and reasons; the handover record and acknowledgement are still written by Agent Studio's built-in ticket actions. Deliberate — read-only actions are much easier to trust on first install.
