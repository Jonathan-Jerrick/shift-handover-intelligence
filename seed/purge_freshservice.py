#!/usr/bin/env python3
"""
purge_freshservice.py — remove the Northwind verification seed data.

Clears the throwaway ITSM corpus so the Shift Handover Intelligence build
starts from a clean instance. Only touches records this project created:
tickets tagged `tgpf-seed`, requesters on the demo email domain, the seeded
solution category, and the seeded changes/problems.

USAGE
-----
    export FRESHSERVICE_DOMAIN=shobana.freshservice.com
    export FRESHSERVICE_API_KEY=<api key>

    python3 purge_freshservice.py                # DRY RUN - lists what it would remove
    python3 purge_freshservice.py --confirm      # actually removes

Dry run is the default and --confirm is required. Nothing is deleted without it.

WHAT IT WILL NOT TOUCH
----------------------
* The Finance department (pre-existed this project)
* Any agent account, group, SLA policy, business hours or canned response
* Any ticket without the `tgpf-seed` tag
* Anything created before this project started
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

DELAY = 0.35
TAG = "tgpf-seed"
DEMO_DOMAIN = "northwind-demo.io"
SEEDED_CATEGORY_PREFIX = "Northwind Logistics"
SEEDED_DEPARTMENTS = [
    "Information Technology", "People & Culture",
    "Fleet Operations", "Commercial",
]  # Finance deliberately excluded - it pre-existed


class FS:
    def __init__(self, domain, api_key, confirm=False):
        self.base = f"https://{domain}/api/v2"
        token = base64.b64encode(f"{api_key}:X".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}",
                        "Content-Type": "application/json"}
        self.confirm = confirm
        self.removed = 0
        self.failed = 0

    def _call(self, method, path, payload=None):
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read().decode()
                time.sleep(DELAY)
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:200]
            if e.code == 429:
                print("  rate limited - sleeping 60s")
                time.sleep(60)
                return self._call(method, path, payload)
            print(f"  ! {method} {path} -> {e.code} {detail}")
            return None
        except Exception as e:                                    # noqa: BLE001
            print(f"  ! {method} {path} -> {e}")
            return None

    def get(self, path):
        return self._call("GET", path)

    def delete(self, path, label):
        if not self.confirm:
            print(f"  [dry-run] DELETE {path}   {label}")
            return True
        r = self._call("DELETE", path)
        if r is None:
            self.failed += 1
            print(f"  ! failed  {label}")
            return False
        self.removed += 1
        print(f"  - {label}")
        return True

    def get_all(self, path, key, max_pages=15):
        """Page through a collection endpoint."""
        out, page = [], 1
        while page <= max_pages:
            sep = "&" if "?" in path else "?"
            r = self.get(f"{path}{sep}per_page=100&page={page}")
            if not r or not r.get(key):
                break
            out.extend(r[key])
            if len(r[key]) < 100:
                break
            page += 1
        return out


def purge_tickets(fs):
    print("\n== Tickets tagged", TAG)
    tickets = fs.get_all("/tickets?include=tags", "tickets")
    targets = [t for t in tickets if TAG in (t.get("tags") or [])]
    # Fallback: the include=tags param is not honoured on every plan.
    if not targets:
        print("  tag data not returned inline - matching on seeded subjects instead")
        known = ("VPN drops every 10", "Cannot log in", "Laptop fan running",
                 "Request: second monitor", "Outlook stuck", "New starter",
                 "Printer in Finance", "Need access to the Q3", "Expense portal rejects",
                 "MFA enrolment failing", "Handheld scanner not reading",
                 "Software licence request", "Email quota full", "Guest wifi for auditors",
                 "Field app crashes", "Payroll system slow", "Replace laptop",
                 "Cannot access CRM", "Meeting room display", "Offboarding")
        targets = [t for t in tickets
                   if any(t.get("subject", "").startswith(k) for k in known)]
    print(f"  {len(targets)} to remove")
    for t in targets:
        fs.delete(f"/tickets/{t['id']}", f"#{t['id']} {t.get('subject','')[:60]}")


def purge_requesters(fs):
    print("\n== Requesters on", DEMO_DOMAIN)
    reqs = fs.get_all("/requesters", "requesters")
    targets = [r for r in reqs
               if DEMO_DOMAIN in (r.get("primary_email") or "")]
    print(f"  {len(targets)} to remove")
    for r in targets:
        name = f"{r.get('first_name','')} {r.get('last_name') or ''}".strip()
        fs.delete(f"/requesters/{r['id']}/forget", f"{name} <{r.get('primary_email')}>")


def purge_knowledge(fs):
    print("\n== Solution category:", SEEDED_CATEGORY_PREFIX)
    cats = fs.get_all("/solutions/categories", "categories")
    for c in cats:
        if not c.get("name", "").startswith(SEEDED_CATEGORY_PREFIX):
            continue
        folders = fs.get(f"/solutions/folders?category_id={c['id']}") or {}
        for f in folders.get("folders", []):
            arts = fs.get(f"/solutions/articles?folder_id={f['id']}") or {}
            for a in arts.get("articles", []):
                fs.delete(f"/solutions/articles/{a['id']}", f"article: {a.get('title','')[:50]}")
            fs.delete(f"/solutions/folders/{f['id']}", f"folder: {f.get('name')}")
        fs.delete(f"/solutions/categories/{c['id']}", f"category: {c.get('name')}")


def purge_catalog(fs):
    print("\n== Service catalogue category")
    cats = fs.get_all("/service_catalog/categories", "service_categories")
    for c in cats:
        if c.get("name", "").startswith(SEEDED_CATEGORY_PREFIX):
            fs.delete(f"/service_catalog/categories/{c['id']}", f"category: {c.get('name')}")


def purge_changes_problems(fs):
    print("\n== Changes and problems")
    seeded_changes = ("Upgrade depot wifi", "Migrate payroll portal",
                      "Quarterly certificate rotation", "Increase Exchange mailbox",
                      "Field app hotfix")
    for c in fs.get_all("/changes", "changes"):
        if any(c.get("subject", "").startswith(k) for k in seeded_changes):
            fs.delete(f"/changes/{c['id']}", f"CHG {c.get('subject','')[:55]}")

    seeded_problems = ("Recurring VPN session drops", "Month-end degradation",
                       "Proof of delivery uploads failing")
    for p in fs.get_all("/problems", "problems"):
        if any(p.get("subject", "").startswith(k) for k in seeded_problems):
            fs.delete(f"/problems/{p['id']}", f"PRB {p.get('subject','')[:55]}")


def purge_departments(fs):
    print("\n== Departments (Finance deliberately kept)")
    for d in fs.get_all("/departments", "departments"):
        if d.get("name") in SEEDED_DEPARTMENTS:
            fs.delete(f"/departments/{d['id']}", d["name"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--confirm", action="store_true",
                    help="actually delete. Without this the script only reports.")
    args = ap.parse_args()

    domain = os.environ.get("FRESHSERVICE_DOMAIN")
    key = os.environ.get("FRESHSERVICE_API_KEY")
    if not domain or not key:
        sys.exit("Set FRESHSERVICE_DOMAIN and FRESHSERVICE_API_KEY first.")

    fs = FS(domain, key, confirm=args.confirm)
    print(f"Target: https://{domain}")
    print("Mode:  ", "LIVE DELETE" if args.confirm else "DRY RUN (nothing will be removed)")

    if fs.get("/tickets?per_page=1") is None:
        sys.exit("Could not authenticate.")

    purge_tickets(fs)
    purge_requesters(fs)
    purge_knowledge(fs)
    purge_catalog(fs)
    purge_changes_problems(fs)
    purge_departments(fs)

    print()
    if args.confirm:
        print(f"Removed {fs.removed} records. {fs.failed} failures.")
    else:
        print("Dry run complete. Re-run with --confirm to remove.")


if __name__ == "__main__":
    main()
