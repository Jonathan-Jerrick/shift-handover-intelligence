#!/usr/bin/env python3
"""
seed_shift_handover.py — build the Kestrel Components demo corpus.

Creates the manufacturing world that the Shift Handover Intelligence agent
reasons over: two plants, four production lines, three shift crews, the
handover policy knowledge base, and one fully-populated Shift B window
containing three deliberate traps the agent must find.

    export FRESHSERVICE_DOMAIN=shobana.freshservice.com
    export FRESHSERVICE_API_KEY=<api key>

    python3 seed_shift_handover.py --dry-run
    python3 seed_shift_handover.py
    python3 seed_shift_handover.py --report

DESIGN NOTE — the corpus IS the demo script
-------------------------------------------
Every record exists to make one specific agent behaviour possible. The three
traps planted in the Shift B window are:

  TRAP 1  Two stoppages on CBE-L3 at 16:20 and 19:45, same fault signature
          (4021 interlock), logged separately by different operators.
          -> forces the agent to ask "same root cause, or separate events?"

  TRAP 2  A maintenance job opened at 20:10 on the CBE-L3 safety gate that is
          STILL OPEN at end of shift.
          -> maintenance-in-progress crossing a shift boundary is the highest
             risk carryover category in the handover policy. The agent must
             surface it as item one.

  TRAP 3  A tooling changeover scheduled for 02:00 tonight on CBE-L3, which
          power-cycles the very interlock circuit from TRAP 1 and touches the
          gate from TRAP 2.
          -> the collision is the insight. Neither the outgoing nor incoming
             supervisor has any reason to spot it; the agent does.

TIMESTAMP STRATEGY
------------------
Freshservice may silently ignore `created_at` on ticket create. Rather than
depend on it, every ticket carries its shift-clock time in the SUBJECT and in
the description, plus tags. The agent reads time from text, so the demo is
robust regardless of what the API does with created_at.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

DELAY = 0.35
TAG = "kestrel"
COMPANY = "Kestrel Components"
EMAIL_DOMAIN = "kestrel-demo.io"


def utcnow():
    return datetime.now(timezone.utc)


def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- HTTP

class FS:
    def __init__(self, domain, api_key, dry_run=False):
        self.base = f"https://{domain}/api/v2"
        token = base64.b64encode(f"{api_key}:X".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}",
                        "Content-Type": "application/json"}
        self.dry_run = dry_run
        self._fake = 900000

    def _call(self, method, path, payload=None):
        if self.dry_run and method != "GET":
            print(f"  [dry-run] {method} {path} {json.dumps(payload)[:100] if payload else ''}")
            self._fake += 1
            return {"_dry_run": True, "_id": self._fake}
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read().decode()
                time.sleep(DELAY)
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:250]
            if e.code == 429:
                print("  rate limited - sleeping 60s")
                time.sleep(60)
                return self._call(method, path, payload)
            print(f"  ! {method} {path} -> {e.code} {detail}")
            return None
        except Exception as e:                                    # noqa: BLE001
            print(f"  ! {method} {path} -> {e}")
            return None

    def get(self, p):
        return self._call("GET", p)

    def post(self, p, b):
        return self._call("POST", p, b)

    def nid(self, resp, key):
        if not resp:
            return None
        if resp.get("_dry_run"):
            return resp["_id"]
        return (resp.get(key) or {}).get("id")


# ---------------------------------------------------------------- corpus

LOCATIONS = [
    ("Coimbatore Plant", "Kestrel Components, SIDCO Industrial Estate, Coimbatore"),
    ("Pune Plant", "Kestrel Components, Chakan MIDC Phase II, Pune"),
]

DEPARTMENTS = [
    ("Production", "Line operations across all shifts"),
    ("Maintenance", "Preventive and breakdown maintenance, plant engineering"),
    ("Quality", "Inline inspection, NCR and audit"),
    ("EHS", "Environment, health and safety"),
    ("Plant IT", "MES, HMI and shop-floor systems"),
]

PEOPLE = [
    ("Ramesh", "Subramanian", "Shift Supervisor - Shift A", "Production", "Coimbatore Plant"),
    ("Anitha", "Krishnan", "Shift Supervisor - Shift B", "Production", "Coimbatore Plant"),
    ("Vikram", "Pillai", "Shift Supervisor - Shift C", "Production", "Coimbatore Plant"),
    ("Suresh", "Babu", "Maintenance Technician", "Maintenance", "Coimbatore Plant"),
    ("Deepak", "Nair", "Maintenance Lead", "Maintenance", "Coimbatore Plant"),
    ("Lakshmi", "Venkatesan", "Quality Engineer", "Quality", "Coimbatore Plant"),
    ("Prakash", "Iyer", "Line Operator - CBE-L3", "Production", "Coimbatore Plant"),
    ("Meena", "Raghunathan", "Line Operator - CBE-L3", "Production", "Coimbatore Plant"),
    ("Arjun", "Menon", "Shift Supervisor - Shift B", "Production", "Pune Plant"),
    ("Sanjay", "Deshpande", "Maintenance Lead", "Maintenance", "Pune Plant"),
]

# The policy corpus. This is what the agent's judgement is grounded on -
# without it the interrogation is generic, with it the agent cites rules.
KB_ARTICLES = [
    ("Shift handover procedure - Kestrel Components",
     "Every production shift must complete a formal handover before the outgoing supervisor "
     "leaves the floor. A handover is not complete when the shift ends; it is complete when the "
     "incoming supervisor has acknowledged every carryover item. Unacknowledged handovers are "
     "treated as an open finding at audit.<br><br>"
     "The handover must cover, at minimum: all line stoppages during the shift and whether each "
     "was resolved; any maintenance activity that is still in progress; any scheduled activity "
     "falling in the incoming shift's window; any quality hold or deviation raised; and any "
     "equipment running under a temporary workaround or watch condition.<br><br>"
     "A statement of 'nothing to report' is not acceptable on a shift where any line stoppage, "
     "maintenance job or scheduled change was recorded. The outgoing supervisor must state "
     "explicitly, for each such item, whether it is closed or carried over."),

    ("Carryover classification - what must be handed over",
     "Carryover items are classified into four risk categories. They must be handed over in this "
     "order of priority.<br><br>"
     "<b>Category 1 - Maintenance in progress (highest risk).</b> Any maintenance job opened and "
     "not closed before the shift boundary. Equipment in a partially worked state must never be "
     "assumed to be back to specification. Category 1 items require explicit acknowledgement from "
     "the incoming supervisor before the handover can be closed.<br><br>"
     "<b>Category 2 - Recurring or unresolved fault.</b> Any fault that has occurred more than "
     "once on the same line without a confirmed root cause. If the line is being run despite the "
     "fault, the outgoing supervisor must state the watch condition.<br><br>"
     "<b>Category 3 - Scheduled activity in the incoming window.</b> Any planned change, "
     "changeover, maintenance window or trial falling within the incoming shift.<br><br>"
     "<b>Category 4 - Informational.</b> Consumables, routine calibration due dates, "
     "housekeeping.<br><br>"
     "Where a Category 1 or 2 item shares a line or a circuit with a Category 3 item, the "
     "combination must be flagged as a collision and escalated to the Maintenance Lead."),

    ("Tooling changeover procedure - variant change",
     "A tooling variant changeover requires the line to be brought to a full stop, the safety "
     "circuit isolated, and control power cycled before tooling is exchanged. The power cycle "
     "re-initialises all safety interlocks on the line, including gate interlock switches.<br><br>"
     "Because the power cycle re-initialises the interlock chain, any line with an unresolved "
     "interlock fault must not enter a changeover without Maintenance sign-off. An interlock that "
     "is intermittently dropping out during normal running will frequently fail to re-establish "
     "after a power cycle, leaving the line unable to restart and the changeover window lost.<br><br>"
     "Typical changeover duration is 3 to 4 hours. A failed restart after changeover typically "
     "adds a further 2 to 6 hours depending on Maintenance availability."),

    ("Safety gate interlock - fault 4021 troubleshooting",
     "Fault 4021 (E-Stop Circuit Open) on CBE lines indicates the safety circuit has been broken. "
     "The most common cause is a safety gate not fully closed, which accounts for roughly three "
     "quarters of occurrences and is cleared by reseating the gate.<br><br>"
     "Where the gate is confirmed closed and the interlock LED remains unlit, the fault is in the "
     "interlock switch or its wiring, not the gate position. This requires Maintenance and cannot "
     "be cleared by the operator. The relevant switch on CBE-L3 gate 2 is S2-B, part number "
     "44-7781.<br><br>"
     "Repeated 4021 events on the same line within a short window indicate a degrading switch and "
     "must be raised as a problem record, not repeatedly closed as individual incidents."),

    ("Escalation matrix by shift and severity",
     "Shift A runs 06:00 to 14:00, Shift B 14:00 to 22:00, Shift C 22:00 to 06:00.<br><br>"
     "During Shift C, the Maintenance Lead is on call rather than on site; expected response is 45 "
     "minutes for a line-down event and next morning for anything else. This means a Category 1 "
     "carryover handed into Shift C carries materially more risk than the same item handed into "
     "Shift A or B, and the handover must state this explicitly.<br><br>"
     "Line-down events exceeding 30 minutes on any CBE line require notification to the Production "
     "Manager regardless of shift."),

    ("Line stoppage recording standard",
     "Every line stoppage must be recorded against the line identifier (CBE-L1, CBE-L2, CBE-L3, "
     "PUN-L1), with the shift clock time of the stoppage, the fault code displayed on the HMI if "
     "any, the duration in minutes, and the action taken.<br><br>"
     "Stoppages must be logged individually. Two stoppages from the same underlying cause must "
     "still be logged as two events, and the relationship recorded in the notes. Aggregating "
     "repeat stoppages into a single record hides recurrence and is the most common reason a "
     "degrading component is not detected before it fails outright."),
]

# ---- The Shift B window. (time, line, subject, description, status, priority, tags)
# status: 2 Open, 3 Pending, 4 Resolved, 5 Closed
SHIFT_B_TICKETS = [
    ("14:20", "CBE-L3", "Coolant level low at station 2 - topped up",
     "Routine check found coolant below minimum mark at station 2. Topped up from bulk. "
     "No stoppage. Logged for consumption tracking.",
     5, 1, ["routine", "consumable"]),

    ("15:05", "CBE-L2", "Vision system no-read, fault 8810",
     "Vision station rejecting good parts, fault 8810 no-read. Lens cleaned and reference image "
     "re-taught. Running normally since 15:22. 17 minutes lost.",
     5, 2, ["stoppage", "resolved"]),

    # ---------- TRAP 1a ----------
    ("16:20", "CBE-L3", "Line stopped - safety gate interlock fault 4021",
     "Line stopped without warning. HMI showing fault 4021, E-Stop circuit open. Safety gate 2 "
     "found closed on inspection. Reseated the gate and the fault cleared on reset. Line restarted "
     "16:32. 12 minutes lost. Reported by P. Iyer.",
     5, 2, ["stoppage", "resolved", "fault-4021"]),

    ("17:10", "CBE-L1", "Badge reader at Gate 4 not registering",
     "Operators unable to badge in at Gate 4. Plant IT reset the reader remotely. Working from "
     "17:25. No production impact.",
     5, 1, ["facilities", "resolved"]),

    ("18:00", "CBE-L3", "Torque wrench T-114 calibration due in 6 days",
     "Automated calibration reminder. T-114 on CBE-L3 station 4 due for calibration. Not yet "
     "actioned - needs booking with Quality.",
     2, 1, ["calibration", "open"]),

    ("18:40", "CBE-L2", "Pallet jam at outfeed conveyor",
     "Pallet skewed at outfeed causing jam. Cleared manually. Line restarted 18:49. 9 minutes lost.",
     5, 1, ["stoppage", "resolved"]),

    # ---------- TRAP 1b ----------
    ("19:45", "CBE-L3", "Line stopped again - interlock dropout, fault 4021",
     "Second stoppage this shift with the same signature. HMI fault 4021 again. Gate 2 was "
     "confirmed closed and undisturbed. Reset attempted twice before the circuit re-established. "
     "Line restarted 20:03. 18 minutes lost. Reported by M. Raghunathan - different operator to "
     "the 16:20 event, logged separately.",
     5, 3, ["stoppage", "resolved", "fault-4021"]),

    # ---------- TRAP 2 - the Category 1 carryover ----------
    ("20:10", "CBE-L3", "Maintenance job - safety gate S2-B interlock switch inspection",
     "Raised following the second 4021 event. Maintenance to inspect interlock switch S2-B on gate "
     "2, part 44-7781, for intermittent contact. S. Babu attended, opened the gate housing and "
     "began continuity checks. Work suspended at end of shift with the housing open and the switch "
     "partially disconnected. Technician returning 06:00 Shift A. GATE 2 IS NOT IN A KNOWN GOOD "
     "STATE.",
     2, 3, ["maintenance", "open", "carryover", "fault-4021"]),

    ("20:55", "CBE-L3", "Reject bin at station 4 approaching full",
     "Reject bin at 80% capacity. Needs emptying before end of next shift.",
     2, 1, ["housekeeping", "open"]),

    ("21:30", "CBE-L1", "Compressed air pressure dip observed at station 1",
     "Brief pressure dip to 5.2 bar observed twice this shift, recovered on its own both times. "
     "No stoppage. Flagged for monitoring - may indicate compressor cycling issue.",
     2, 2, ["monitoring", "open"]),

    ("21:50", "CBE-L3", "Unusual noise reported from conveyor drive",
     "Operator reported an intermittent knocking sound from the conveyor drive unit near the end "
     "of shift. Not investigated - reported at 21:50 with no time remaining in shift. No fault "
     "code, no stoppage.",
     2, 2, ["monitoring", "open", "carryover"]),
]

# Prior-weeks history that justifies the problem record and proves recurrence.
HISTORY_TICKETS = [
    (18, "CBE-L3", "Line stopped - fault 4021 interlock",
     "Fault 4021. Gate reseated, cleared on reset. 9 minutes lost.", ["stoppage", "fault-4021"]),
    (14, "CBE-L3", "Line stopped - fault 4021 interlock",
     "Fault 4021 during shift A. Gate 2 reseated. 7 minutes lost.", ["stoppage", "fault-4021"]),
    (9, "CBE-L3", "Line stopped - fault 4021 interlock",
     "Fault 4021, third occurrence this month. Gate reseated. 11 minutes lost. Operator noted the "
     "gate had not been touched prior to the fault.", ["stoppage", "fault-4021"]),
    (4, "CBE-L3", "Line stopped - fault 4021 interlock",
     "Fault 4021 again. Cleared after two reset attempts. 14 minutes lost. Recurrence noted to "
     "Maintenance verbally, no job raised.", ["stoppage", "fault-4021"]),
]


def seed_locations(fs):
    print("\n== Locations")
    out = {}
    existing = fs.get("/locations?per_page=100") or {}
    known = {l["name"]: l["id"] for l in existing.get("locations", [])}
    for name, addr in LOCATIONS:
        if name in known:
            out[name] = known[name]
            print(f"  = {name} (exists)")
            continue
        r = fs.post("/locations", {"name": name,
                                   "address": {"line1": addr, "country": "India"}})
        lid = fs.nid(r, "location")
        if lid:
            out[name] = lid
            print(f"  + {name}")
    return out


def seed_departments(fs):
    print("\n== Departments")
    out = {}
    existing = fs.get("/departments?per_page=100") or {}
    known = {d["name"]: d["id"] for d in existing.get("departments", [])}
    for name, desc in DEPARTMENTS:
        if name in known:
            out[name] = known[name]
            print(f"  = {name} (exists)")
            continue
        r = fs.post("/departments", {"name": name, "description": desc})
        did = fs.nid(r, "department")
        if did:
            out[name] = did
            print(f"  + {name}")
    return out


def seed_people(fs, departments, locations):
    print("\n== People")
    out = []
    for first, last, title, dept, loc in PEOPLE:
        email = f"{first.lower()}.{last.lower()}@{EMAIL_DOMAIN}"
        payload = {"first_name": first, "last_name": last, "primary_email": email,
                   "job_title": title}
        if departments.get(dept):
            payload["department_ids"] = [departments[dept]]
        if locations.get(loc):
            payload["location_id"] = locations[loc]
        r = fs.post("/requesters", payload)
        rid = fs.nid(r, "requester")
        if rid:
            out.append({"id": rid, "email": email, "name": f"{first} {last}", "role": title})
            print(f"  + {first} {last} - {title}")
        else:
            look = fs.get(f"/requesters?email={email}")
            if look and look.get("requesters"):
                out.append({"id": look["requesters"][0]["id"], "email": email,
                            "name": f"{first} {last}", "role": title})
                print(f"  = {first} {last} (exists)")
    return out


def seed_knowledge(fs):
    print("\n== Knowledge base - handover policy corpus")
    cat_id = None
    cats = fs.get("/solutions/categories?per_page=100") or {}
    for c in cats.get("categories", []):
        if c.get("name", "").startswith(COMPANY):
            cat_id = c["id"]
            print(f"  = category exists ({cat_id})")
            break
    if not cat_id:
        r = fs.post("/solutions/categories", {
            "name": f"{COMPANY} Plant Operations",
            "description": "Shift handover, carryover classification and line procedures"})
        cat_id = fs.nid(r, "category")
    if not cat_id:
        print("  ! no category; skipping articles")
        return
    print(f"  category id {cat_id}")

    folder_id = None
    folders = fs.get(f"/solutions/folders?category_id={cat_id}") or {}
    for f in folders.get("folders", []):
        if f.get("name") == "Shift Operations":
            folder_id = f["id"]
            break
    if not folder_id:
        r = fs.post("/solutions/folders", {
            "name": "Shift Operations", "category_id": cat_id, "visibility": 1,
            "description": "Handover procedure, carryover rules, line standards"})
        folder_id = fs.nid(r, "folder")
    if not folder_id:
        print("  ! no folder; skipping articles")
        return
    print(f"  folder id {folder_id}")

    for title, body in KB_ARTICLES:
        r = fs.post("/solutions/articles", {
            "title": title, "description": body, "folder_id": folder_id,
            "status": 2, "tags": [TAG]})
        print(f"  {'+' if r else '!'} {title}")


def seed_tickets(fs, people):
    print("\n== Shift B window - CBE-L3 and neighbours")
    if not people:
        print("  ! no people; skipping")
        return

    supervisor = next((p for p in people if "Shift B" in p["role"]
                       and "Coimbatore" not in p.get("loc", "")), people[0])
    operators = [p for p in people if "Operator" in p["role"]] or people
    maint = [p for p in people if "Maintenance" in p["role"]] or people

    today = utcnow().strftime("%d %b %Y")
    for clock, line, subject, desc, status, priority, tags in SHIFT_B_TICKETS:
        if "Maintenance job" in subject:
            reporter = maint[0]
        elif "Line stopped" in subject:
            reporter = operators[0] if clock == "16:20" else operators[-1]
        else:
            reporter = supervisor
        payload = {
            "email": reporter["email"],
            "subject": f"[{clock}] {line} - {subject}",
            "description": (f"<p><b>Line:</b> {line} &nbsp; <b>Shift:</b> B "
                            f"(14:00-22:00) &nbsp; <b>Shift date:</b> {today} "
                            f"&nbsp; <b>Time:</b> {clock}</p><p>{desc}</p>"),
            "priority": priority,
            "status": status,
            "source": 2,
            "tags": [TAG, "shift-b", line.lower()] + tags,
        }
        r = fs.post("/tickets", payload)
        flag = ""
        if clock in ("16:20", "19:45"):
            flag = "   <-- TRAP 1"
        if clock == "20:10":
            flag = "   <-- TRAP 2 (Category 1 carryover)"
        print(f"  {'+' if r else '!'} [{clock}] {line} {subject[:44]}{flag}")

    print("\n== Prior-weeks recurrence history")
    for days_ago, line, subject, desc, tags in HISTORY_TICKETS:
        when = (utcnow() - timedelta(days=days_ago)).strftime("%d %b %Y")
        payload = {
            "email": operators[0]["email"],
            "subject": f"[{when}] {line} - {subject}",
            "description": f"<p><b>Line:</b> {line} &nbsp; <b>Date:</b> {when}</p><p>{desc}</p>",
            "priority": 2, "status": 5, "source": 2,
            "tags": [TAG, line.lower()] + tags,
        }
        r = fs.post("/tickets", payload)
        print(f"  {'+' if r else '!'} {when} {subject[:50]}")


def seed_change_and_problem(fs, people):
    print("\n== Tonight's change (TRAP 3) and the open problem")
    if not people:
        print("  ! no people; skipping")
        return
    maint = next((p for p in people if "Maintenance Lead" in p["role"]), people[0])

    # Scheduled inside the incoming Shift C window, on the same line as TRAP 1 and 2.
    start = utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(hours=7)
    payload = {
        "email": maint["email"],
        "subject": "CBE-L3 tooling changeover - Variant B to Variant C",
        "description": (
            "<p>Planned tooling variant changeover on CBE-L3, scheduled into the Shift C window "
            "at 02:00. Line to be brought to full stop, safety circuit isolated and control power "
            "cycled before tooling exchange.</p>"
            "<p>Expected duration 3-4 hours. Line must be released back to Shift A production by "
            "06:00.</p>"
            "<p><b>Note:</b> the control power cycle re-initialises the entire gate interlock "
            "chain on this line.</p>"),
        "priority": 3,
        "status": 1,                 # pinned - the stateflow rejects anything else on create
        "risk": 2,
        "impact": 2,
        "change_type": 1,
        "planned_start_date": ts(start),
        "planned_end_date": ts(start + timedelta(hours=4)),
    }
    r = fs.post("/changes", payload)
    print(f"  {'+' if r else '!'} CHG  CBE-L3 tooling changeover  (starts {ts(start)})   <-- TRAP 3")

    # Distractor change on the other plant, far out of window.
    far = utcnow() + timedelta(days=9)
    r = fs.post("/changes", {
        "email": maint["email"],
        "subject": "PUN-L1 outfeed conveyor belt replacement",
        "description": "<p>Planned belt replacement on the Pune line outfeed conveyor. "
                       "Scheduled well outside the current shift window.</p>",
        "priority": 2, "status": 1, "risk": 1, "impact": 1, "change_type": 1,
        "planned_start_date": ts(far),
        "planned_end_date": ts(far + timedelta(hours=6)),
    })
    print(f"  {'+' if r else '!'} CHG  PUN-L1 belt replacement (distractor, +9 days)")

    r = fs.post("/problems", {
        "email": maint["email"],
        "subject": "Recurring safety gate interlock dropouts on CBE-L3",
        "description": (
            "<p>Fault 4021 (E-Stop circuit open) has now occurred six times on CBE-L3 over three "
            "weeks. In the more recent occurrences the safety gate was confirmed closed and "
            "undisturbed before the fault, which rules out gate misalignment as the cause.</p>"
            "<p>Suspected degrading interlock switch S2-B on gate 2, part 44-7781. Intermittent "
            "contact consistent with the observed pattern of dropouts under vibration.</p>"
            "<p><b>Open risk:</b> a degrading interlock frequently fails to re-establish after a "
            "control power cycle. Any changeover on this line carries a restart risk until the "
            "switch is replaced.</p>"),
        "priority": 3, "status": 1, "impact": 2,
    })
    print(f"  {'+' if r else '!'} PRB  Recurring interlock dropouts on CBE-L3")


def report(fs):
    print("\n== Instance contents")
    for label, path, key in [
        ("locations", "/locations?per_page=100", "locations"),
        ("departments", "/departments?per_page=100", "departments"),
        ("requesters", "/requesters?per_page=100", "requesters"),
        ("tickets", "/tickets?per_page=100", "tickets"),
        ("changes", "/changes?per_page=100", "changes"),
        ("problems", "/problems?per_page=100", "problems"),
        ("solution categories", "/solutions/categories?per_page=100", "categories"),
    ]:
        r = fs.get(path)
        n = len(r.get(key, [])) if r else "blocked"
        print(f"  {label:22} {n}")


STAGES = ["locations", "departments", "people", "knowledge", "tickets", "change"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=STAGES)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    domain = os.environ.get("FRESHSERVICE_DOMAIN")
    key = os.environ.get("FRESHSERVICE_API_KEY")
    if not domain or not key:
        sys.exit("Set FRESHSERVICE_DOMAIN and FRESHSERVICE_API_KEY first.")

    fs = FS(domain, key, dry_run=args.dry_run)
    print(f"Target: https://{domain}  {'[DRY RUN]' if args.dry_run else ''}")
    print(f"Corpus: {COMPANY} - Coimbatore and Pune plants")

    if args.report:
        report(fs)
        return
    if fs.get("/tickets?per_page=1") is None:
        sys.exit("Could not authenticate.")

    run = [args.only] if args.only else STAGES
    locations, departments, people = {}, {}, []

    if "locations" in run:
        locations = seed_locations(fs)
    if "departments" in run:
        departments = seed_departments(fs)
    if "people" in run:
        if not departments:
            ex = fs.get("/departments?per_page=100") or {}
            departments = {d["name"]: d["id"] for d in ex.get("departments", [])}
        if not locations:
            ex = fs.get("/locations?per_page=100") or {}
            locations = {l["name"]: l["id"] for l in ex.get("locations", [])}
        people = seed_people(fs, departments, locations)
    if "knowledge" in run:
        seed_knowledge(fs)

    if not people and ({"tickets", "change"} & set(run)):
        ex = fs.get("/requesters?per_page=100") or {}
        people = [{"id": r["id"], "email": r.get("primary_email") or "",
                   "name": f"{r.get('first_name','')} {r.get('last_name') or ''}".strip(),
                   "role": r.get("job_title") or ""}
                  for r in ex.get("requesters", [])
                  if EMAIL_DOMAIN in (r.get("primary_email") or "")]
        if fs.dry_run and not people:
            people = [{"id": 0, "email": f"{f.lower()}.{l.lower()}@{EMAIL_DOMAIN}",
                       "name": f"{f} {l}", "role": t} for f, l, t, _, _ in PEOPLE]

    if "tickets" in run:
        seed_tickets(fs, people)
    if "change" in run:
        seed_change_and_problem(fs, people)

    print(f"\nDone. Everything tagged: {TAG}")
    print("Traps planted: 1) two 4021 stoppages 16:20 + 19:45  "
          "2) open maintenance job 20:10  3) 02:00 changeover on the same line")


if __name__ == "__main__":
    main()
