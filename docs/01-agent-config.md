# Agent Build Sheet — Shift Handover Intelligence

Everything in this document is copy-paste ready. Fields are given verbatim, character counts checked against Agent Studio's limits.

**Target:** `shobana.freshservice.com` → left rail robot icon → AI Agent Studio

---

## 1. Create the agent

`/b/ai-studio/ai-agents` → **Create new Agent** → **Start from scratch**

| Field | Value |
|---|---|
| Name | `Shift Handover Agent` |
| Avatar | any preset |
| Primary language | English |

---

## 2. Build → Instructions

### 2a. Define your business context *(limit 500)*

```
Kestrel Components is a tier-1 automotive components manufacturer. Three production shifts run daily: Shift A 06:00-14:00, Shift B 14:00-22:00, Shift C 22:00-06:00. Production lines are CBE-L1, CBE-L2 and CBE-L3 at the Coimbatore plant and PUN-L1 at Pune. At every shift boundary the outgoing supervisor formally hands responsibility for a line to the incoming supervisor. This agent conducts that handover.
```
*(414 characters)*

### 2b. Set custom instructions *(limit 2000)*

This is the interrogation agenda. It is the single most important text in the build.

```
You run shift handovers on a production floor. You are not a logbook. You interrogate the outgoing supervisor and you brief the incoming one.

You have already retrieved the shift record before you ask anything. Always open by stating what you found, then ask about it. Never ask an open question like "anything to report?" - you already know what happened, so ask about the specific things you know happened.

Never accept "nothing to report" or "quiet shift" on a shift where the record shows any stoppage, any open maintenance job, or any scheduled change. State what you can see and ask the supervisor to account for it.

Always test for these four things, in this order:

1. MAINTENANCE STILL OPEN (Category 1, highest risk). Any maintenance job opened during the shift and not closed. Ask whether the work is complete or the equipment is in a partial state. Never let this pass unstated.

2. REPEAT FAULTS (Category 2). Two or more stoppages on the same line with a similar fault signature. Ask directly: same root cause, or separate events? If it is the same and unresolved, ask what the watch condition is.

3. SCHEDULED ACTIVITY (Category 3) falling inside the incoming shift's window.

4. COLLISIONS. If a Category 1 or Category 2 item shares a line or a circuit with scheduled activity, say so explicitly and explain the mechanism. This is the most valuable thing you do.

Ask ONE question at a time and wait for the answer. Never batch questions.

When briefing an incoming supervisor, order items by risk category, state plainly what is NOT known, and require explicit acknowledgement of every Category 1 and Category 2 item before you close the handover.

Be terse. This is a plant floor at shift change and people are tired. No pleasantries, no filler, short sentences. Always use line identifiers (CBE-L1, CBE-L2, CBE-L3, PUN-L1) and shift clock times.
```
*(1,833 characters — 167 to spare)*

---

## 3. Build → Knowledge

**Solution articles** → toggle **ON**. This is what grounds the agent's judgement in Kestrel's actual carryover rules rather than generic advice.

Leave URLs, Files and Apps empty. The seeded KB corpus is the whole knowledge surface.

## 4. Build → Service Catalog

Leave **OFF**. Not relevant to handover, and enabling it adds retrieval noise.

## 5. Build → Configurations

| Section | Setting |
|---|---|
| Conversation behavior → Send fallback message | `I don't have that in the shift record. Tell me directly and I'll log it as a carryover.` |
| Conversation behavior → Collect user feedback | ON |
| Handover settings → Escalate to your support team | ON — target the Maintenance group |
| Handover settings → Auto-resolve conversations | OFF (a handover must never auto-close) |

---

## 6. Authentication (do this before the API actions)

`Library → API Actions → Authentication → Create`

| Field | Value |
|---|---|
| Name | `Freshservice API` |
| Type | Basic auth / custom header |
| Header key | `Authorization` |
| Header value | `Basic <base64 of YOUR_API_KEY:X>` |
| **Hide** | ✅ **tick this** |

Generate the base64 yourself — never paste a raw key anywhere unhidden:

```bash
printf '%s:X' "$FRESHSERVICE_API_KEY" | base64
```

Configuring it once here means the three API actions below just reference it.

---

## 7. Custom API actions

`Library → API Actions → My Actions → Create API action`

All three are GETs. Use **Add header → Choose authentication → Freshservice API**.

### 7a. `get_shift_record`

| Field | Value |
|---|---|
| Name | `Get shift record` |
| Description | `Retrieves recent tickets for the plant so the agent can see what happened during a shift. Each ticket subject begins with the shift clock time in square brackets and the line identifier, for example [19:45] CBE-L3. Use this at the start of every handover.` |
| API type | `GET` |
| API URL | `https://shobana.freshservice.com/api/v2/tickets?per_page=50&order_by=created_at&order_type=desc&include=tags` |

**Define outputs:** `tickets` (array). Key fields the agent needs: `subject`, `status`, `priority`, `tags`, `id`.

### 7b. `get_scheduled_changes`

| Field | Value |
|---|---|
| Name | `Get scheduled changes` |
| Description | `Retrieves planned changes and changeovers with their scheduled start times. Use this to find activity falling inside the incoming shift window, and to detect collisions with unresolved faults or open maintenance on the same line.` |
| API type | `GET` |
| API URL | `https://shobana.freshservice.com/api/v2/changes?per_page=25` |

**Define outputs:** `changes` → `subject`, `planned_start_date`, `planned_end_date`, `risk`, `impact`, `id`.

### 7c. `get_open_problems`

| Field | Value |
|---|---|
| Name | `Get open problems` |
| Description | `Retrieves open problem records describing known recurring faults. Use this to tell whether a stoppage during the shift is an instance of a known unresolved problem rather than a one-off.` |
| API type | `GET` |
| API URL | `https://shobana.freshservice.com/api/v2/problems?per_page=25` |

**Define outputs:** `problems` → `subject`, `description_text`, `status`, `id`.

> **On the descriptions.** Agent Studio shows these to the LLM to decide *when* to call each action. They are prompts, not documentation. The phrasing above is deliberately instructional.

---

## 8. The four workflows

`Library → Workflows → Create new workflow`. Build them in this order.

---

### WF1 — Start Handover

**Trigger** *(paste verbatim)*

```
"I'm handing over"
"handing over"
"end of shift"
"shift handover"
"closing out shift B"
"start handover for line 3"
"I'm going off shift"
```

**Nodes**

| # | Node | Config |
|---|---|---|
| 1 | **Collect info** | `line_id` · Text · required · *"The production line being handed over, e.g. CBE-L3"*<br>`outgoing_shift` · Text · required · *"Which shift is ending — A, B or C"* |
| 2 | **API Action** | `get_shift_record` → map nothing in; **Map collected outputs to properties** → store as `shift_tickets` |
| 3 | **API Action** | `get_scheduled_changes` → store as `scheduled_changes` |
| 4 | **API Action** | `get_open_problems` → store as `open_problems` |
| 5 | **Custom response** | **Let the AI Agent generate and send a response** |

**Response guidance for node 5**

```
Summarise what you found for this line in this shift, in under 80 words: how many stoppages, whether any maintenance job is still open, and whether any change is scheduled in the incoming window.

Then ask ONE question, and pick it by this priority: if any maintenance job is still open ask about that first; otherwise if two or more stoppages share a fault signature ask whether they are the same root cause; otherwise ask about the scheduled change.

Do not ask more than one question. Do not list everything you found.
```

---

### WF2 — Interrogate Carryovers

**Trigger**

```
"same issue"
"same root cause"
"separate events"
"still in progress"
"not finished"
"it's ongoing"
"they're coming back"
"run but watch it"
"it should be fine"
```

**Nodes**

| # | Node | Config |
|---|---|---|
| 1 | **Collect info** | `item_status` · Text · required · *"Whether the item is closed or carried over"*<br>`watch_condition` · Text · optional · *"Any condition the incoming shift must watch for"* |
| 2 | **Condition paths** | Path A: response indicates *maintenance incomplete* → Category 1<br>Path B: response indicates *repeat fault, same cause* → Category 2<br>Else Path → Category 4 |
| 3 | **API Action** *(Path A and B only)* | `get_scheduled_changes` → re-check for collision |
| 4 | **Custom response** | AI-generated |

**Response guidance for node 4**

```
Confirm how you have classified the item, using the category number and name from the carryover policy.

Then check for a collision: if this item is on the same line as a change scheduled in the incoming shift window, state the collision explicitly and explain the mechanism in one sentence, citing the relevant procedure.

Then ask the next outstanding question, or if there are none, say the handover is ready to commit and list the carryovers in category order.
```

---

### WF3 — Commit Handover  *(as built, 25 Aug 2026)*

**Trigger** — natural-language intent, not a keyword list. Agent Studio's trigger field takes prose and the LLM matches against it:

```
The OUTGOING shift supervisor has finished answering questions about the shift and is now confirming that the handover should be committed and recorded. Examples: "confirm", "yes commit", "publish it", "that's everything", "done, log it", "commit the handover", "go ahead and create it". Only trigger this when a handover conversation is already in progress and the supervisor is approving the creation of the handover record. Do NOT trigger this when someone is starting a handover or asking for a briefing.
```

The last sentence is load-bearing. Without it WF3 competes with WF1 and WF4 for the same conversational turn.

**Nodes**

| # | Node | Config |
|---|---|---|
| 1 | **Collect info** | `line_id` · required · *"The production line being handed over, for example CBE-L3. If the supervisor already stated it earlier in this conversation, use that value and do not ask again."*<br>`incoming_shift` · required · *"Which shift is taking over - A, B or C..."*<br>`carryover_summary` · required · *"Every carryover item discussed in this handover, written as one line each in category order... Compose this from the conversation so far. Do not ask the supervisor to retype it."* |
| 2 | **API Action** | Custom action **Create handover record** (below). All three collected inputs mapped 1:1. **Advanced → Ask for confirmation: ON.** |
| 3 | **Custom response** | AI-generated, instruction below |

**Response guidance for node 3** *(308 chars, limit 500)*

```
Confirm the handover record has been created and give its ticket reference. List the carryovers in category order, one line each. Then state plainly that this handover is OPEN, not complete: it closes only when the incoming supervisor acknowledges every Category 1 and Category 2 item. Be terse, no pleasantries.
```

#### The `Create handover record` API action

We did **not** use the first-party *Freshservice for AI Agents* app. Installing it demands a Freshservice API key typed into an install form, and the three existing GET actions already authenticate with a hidden `Authorization` header. Consistency beat convenience.

| Field | Value |
|---|---|
| Name | `Create handover record` |
| Description | `Creates the formal HANDOVER record in Freshservice for a production line at the end of a shift, listing every carryover item in category order. Use this only after the outgoing supervisor has explicitly confirmed that the handover should be committed. This is a record of accountability, so never call it without that confirmation.` |
| Inputs | `line_id`, `incoming_shift`, `carryover_summary` — all required, all Text |
| API type | `POST`, payload type `JSON` |
| API URL | `https://shobana.freshservice.com/api/v2/tickets` |
| Headers | `Content-Type: application/json`; `Authorization: Basic <base64 of KEY:X>` with **Hide** ticked |

**Payload** — `line_id`, `incoming_shift` and `carryover_summary` are inserted as property chips via the editor's *Insert properties* control, not typed as text:

```json
{
  "subject": "HANDOVER - «line_id» - Shift handover to Shift «incoming_shift»",
  "description": "«carryover_summary»",
  "email": "anitha.krishnan@kestrel-demo.io",
  "priority": 3,
  "status": 2,
  "tags": ["handover"]
}
```

`anitha.krishnan@kestrel-demo.io` is Kestrel's Shift B supervisor at Coimbatore — the right requester for the demo scenario.

> **Two traps in this editor, both cost us time.**
> 1. The payload field is a **validating JSON editor**. Inserting a property chip while the JSON is incomplete silently collapses the whole body to `{}`. Type the complete, valid JSON first with placeholder words (`LINEID`, `SHIFTID`, `SUMMARY`), then double-click each placeholder and replace it with a chip.
> 2. The property picker only lists inputs declared under **Inputs to be used**. Declare them before you touch the body.

---

### WF4 — Brief Me

**Trigger**

```
"brief me"
"what do I need to know"
"starting my shift"
"anything from last shift"
"what happened last shift"
"take over line 3"
"I'm taking over"
```

**Nodes**

| # | Node | Config |
|---|---|---|
| 1 | **Collect info** | `line_id` · Text · required<br>`incoming_shift` · Text · required |
| 2 | **API Action** | `get_shift_record` → find the most recent `HANDOVER —` ticket for this line |
| 3 | **API Action** | `get_scheduled_changes` |
| 4 | **Custom response** | AI-generated — the briefing |
| 5 | **Collect info** | `acknowledgement` · Text · required · *"Explicit acknowledgement of each Category 1 and 2 item"* |
| 6 | **API Action** | **Add Ticket Note** (built-in) on the handover ticket, recording the acknowledgement and time |
| 7 | **Custom response** | Confirm the handover is closed with acknowledgement |

**Response guidance for node 4**

```
Deliver the briefing in risk order, highest first, numbered. For each item give one line of what it is and one line of what it means for this shift.

State plainly anything that is NOT known — for example equipment left in a partial state whose condition has not been confirmed.

If a carryover collides with a scheduled activity in this shift, that is item one regardless of anything else.

End by naming which items require acknowledgement before you can close the handover. Under 150 words.
```

---

## 9. Deploy

`Deploy → Support Portal → enable`. Set the agent **Active**.

---

## 10. Post-build test script

Run these in the portal preview before recording. Every one must behave.

| # | Input | Must do |
|---|---|---|
| 1 | `handing over, CBE-L3, shift B` | Open with what it found, ask about the **open maintenance job** first |
| 2 | `still in progress, they're back at 6` | Classify Category 1, flag the **02:00 changeover collision** |
| 3 | `same one, the interlock keeps dropping out` | Classify Category 2, ask for the watch condition |
| 4 | `run but watch it` | Say the handover is ready, list carryovers in category order |
| 5 | `confirm` | Ask for confirmation, then create the HANDOVER ticket |
| 6 | `brief me, CBE-L3, shift C` | Briefing in risk order, collision as item 1 |
| 7 | `acknowledged` | Add the note, close the handover |
| 8 | `quiet shift, nothing to report` | **Must refuse** and cite what it can see |

Test 8 is the one to check hardest. If the agent accepts "nothing to report", the Instructions in §2b are not landing and the whole premise fails.

---

## 11. Known build risks

- **[Guessing]** Agent Studio may batch questions despite the one-at-a-time instruction. If it does, tighten node 5's response guidance rather than the global Instructions.
- The `include=tags` parameter is not honoured on every plan. The corpus puts shift time and line ID in the ticket **subject** precisely so retrieval works without tags.
- Trigger collision between WF1 and WF4 is the likeliest failure. WF1 is *ending* a shift, WF4 is *starting* one. If routing misfires, add `"I am leaving"` to WF1 and `"I am arriving"` to WF4.
