/**
 * Unit tests for the Shift Handover Intelligence action logic.
 *
 * These cover the pure functions — the parsing, grouping, window comparison
 * and collision detection that Agent Studio's workflow builder cannot express.
 * `fdk pack` requires 80% coverage across statements, branches, functions and
 * lines before it will produce a package.
 */

import { describe, it, expect, beforeAll } from 'vitest';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

let S;
beforeAll(() => {
  // server.js assigns to a bare `exports` for the FDK runtime; provide it.
  global.exports = {};
  global.renderData = () => {};
  global.$request = { invokeTemplate: async () => ({ response: '{}' }) };
  S = require('../server/server.js');
});

describe('extractFaultCode', () => {
  it('finds an explicit fault code', () => {
    expect(S.extractFaultCode('Line stopped - safety gate interlock fault 4021')).toBe('4021');
  });
  it('finds a bare four-digit code', () => {
    expect(S.extractFaultCode('Vision system no-read, 8810')).toBe('8810');
  });
  it('is case insensitive', () => {
    expect(S.extractFaultCode('FAULT 1234 raised')).toBe('1234');
  });
  it('returns null when there is no code', () => {
    expect(S.extractFaultCode('Coolant level low')).toBeNull();
  });
  it('returns null for empty input', () => {
    expect(S.extractFaultCode('')).toBeNull();
    expect(S.extractFaultCode(null)).toBeNull();
  });
});

describe('extractClockTime', () => {
  it('reads the bracketed shift clock', () => {
    expect(S.extractClockTime('[19:45] CBE-L3 - Line stopped again')).toBe('19:45');
  });
  it('returns null when absent', () => {
    expect(S.extractClockTime('CBE-L3 - Line stopped')).toBeNull();
    expect(S.extractClockTime(undefined)).toBeNull();
  });
});

describe('extractDowntimeMinutes', () => {
  it('reads "12 minutes lost"', () => {
    expect(S.extractDowntimeMinutes('Line restarted 16:32. 12 minutes lost.')).toBe(12);
  });
  it('reads a bare minutes figure', () => {
    expect(S.extractDowntimeMinutes('18 minutes')).toBe(18);
  });
  it('returns 0 when nothing matches', () => {
    expect(S.extractDowntimeMinutes('No stoppage')).toBe(0);
    expect(S.extractDowntimeMinutes(null)).toBe(0);
  });
});

describe('matchesLine', () => {
  const t = { subject: '[16:20] CBE-L3 - Line stopped', tags: ['shift-b', 'cbe-l3'] };
  it('matches on the subject', () => {
    expect(S.matchesLine(t, 'CBE-L3')).toBe(true);
  });
  it('is case insensitive', () => {
    expect(S.matchesLine(t, 'cbe-l3')).toBe(true);
  });
  it('rejects a different line', () => {
    expect(S.matchesLine(t, 'CBE-L1')).toBe(false);
  });
  it('matches everything when no line is given', () => {
    expect(S.matchesLine(t, null)).toBe(true);
  });
  it('tolerates records with no tags', () => {
    expect(S.matchesLine({ subject: 'CBE-L3 thing' }, 'CBE-L3')).toBe(true);
  });
});

describe('isOpen', () => {
  it('treats Open and Pending as open', () => {
    expect(S.isOpen(2)).toBe(true);
    expect(S.isOpen(3)).toBe(true);
  });
  it('treats Resolved and Closed as not open', () => {
    expect(S.isOpen(4)).toBe(false);
    expect(S.isOpen(5)).toBe(false);
  });
});

describe('looksLikeMaintenance / looksLikeStoppage', () => {
  it('spots a maintenance job', () => {
    expect(S.looksLikeMaintenance({ subject: 'Maintenance job - S2-B inspection', tags: [] }))
      .toBe(true);
  });
  it('spots maintenance from tags', () => {
    expect(S.looksLikeMaintenance({ subject: 'Gate work', tags: ['maintenance'] })).toBe(true);
  });
  it('does not flag a coolant top-up as maintenance', () => {
    expect(S.looksLikeMaintenance({ subject: 'Coolant level low', tags: ['routine'] })).toBe(false);
  });
  it('spots a stoppage', () => {
    expect(S.looksLikeStoppage({ subject: 'Line stopped - fault 4021', tags: [] })).toBe(true);
  });
  it('spots a jam as a stoppage', () => {
    expect(S.looksLikeStoppage({ subject: 'Pallet jam at outfeed', tags: [] })).toBe(true);
  });
  it('does not flag a calibration reminder', () => {
    expect(S.looksLikeStoppage({ subject: 'Torque wrench calibration due', tags: [] })).toBe(false);
  });
});

describe('findRepeatFaults — the grouping Agent Studio cannot do', () => {
  const tickets = [
    { id: 1, subject: '[16:20] CBE-L3 - Line stopped, fault 4021',
      description_text: '12 minutes lost.' },
    { id: 2, subject: '[19:45] CBE-L3 - Line stopped again, fault 4021',
      description_text: '18 minutes lost.' },
    { id: 3, subject: '[15:05] CBE-L2 - Vision no-read, fault 8810',
      description_text: '17 minutes lost.' },
    { id: 4, subject: '[14:20] CBE-L3 - Coolant top-up', description_text: 'No stoppage.' }
  ];

  it('groups two occurrences of the same code', () => {
    const out = S.findRepeatFaults(tickets);
    expect(out).toHaveLength(1);
    expect(out[0].fault_code).toBe('4021');
    expect(out[0].occurrences).toBe(2);
  });

  it('sums downtime across occurrences', () => {
    expect(S.findRepeatFaults(tickets)[0].total_downtime_minutes).toBe(30);
  });

  it('captures the clock time of each event', () => {
    const events = S.findRepeatFaults(tickets)[0].events.map((e) => e.at);
    expect(events).toEqual(['16:20', '19:45']);
  });

  it('ignores single occurrences', () => {
    const codes = S.findRepeatFaults(tickets).map((f) => f.fault_code);
    expect(codes).not.toContain('8810');
  });

  it('returns empty for no stoppages', () => {
    expect(S.findRepeatFaults([{ id: 9, subject: 'Coolant top-up' }])).toEqual([]);
  });
});

describe('findScheduledInWindow — the date arithmetic Agent Studio cannot do', () => {
  const now = new Date('2026-08-25T20:00:00Z');
  const changes = [
    { id: 1, subject: 'CBE-L3 tooling changeover - Variant B to C',
      planned_start_date: '2026-08-25T22:30:00Z', risk: 2 },
    { id: 2, subject: 'PUN-L1 conveyor belt replacement',
      planned_start_date: '2026-09-03T06:00:00Z', risk: 1 },
    { id: 3, subject: 'CBE-L3 filter change', planned_start_date: null }
  ];

  it('includes a change inside the lookahead window', () => {
    const out = S.findScheduledInWindow(changes, 'CBE-L3', 8, now);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe(1);
  });

  it('computes hours from now', () => {
    expect(S.findScheduledInWindow(changes, 'CBE-L3', 8, now)[0].hours_from_now).toBe(3);
  });

  it('excludes a change outside the window', () => {
    const out = S.findScheduledInWindow(changes, 'PUN-L1', 8, now);
    expect(out).toHaveLength(0);
  });

  it('excludes changes with no planned start', () => {
    const ids = S.findScheduledInWindow(changes, 'CBE-L3', 400, now).map((c) => c.id);
    expect(ids).not.toContain(3);
  });

  it('handles an unparseable date without throwing', () => {
    const bad = [{ id: 4, subject: 'CBE-L3 thing', planned_start_date: 'not-a-date' }];
    expect(S.findScheduledInWindow(bad, 'CBE-L3', 8, now)).toEqual([]);
  });
});

describe('detectCollisions — the core insight', () => {
  const openMaintenance = [{ id: 20, subject: 'Maintenance job - S2-B inspection', at: '20:10' }];
  const repeatFaults = [{ fault_code: '4021', occurrences: 2, events: [],
                          total_downtime_minutes: 30 }];
  const scheduled = [{ id: 1, subject: 'CBE-L3 tooling changeover',
                       planned_start_date: '2026-08-25T22:30:00Z', hours_from_now: 3 }];

  it('flags a maintenance-versus-scheduled collision', () => {
    const out = S.detectCollisions(openMaintenance, [], scheduled, 'CBE-L3');
    expect(out).toHaveLength(1);
    expect(out[0].carryover_category).toBe(S.CATEGORY.MAINTENANCE_OPEN);
    expect(out[0].severity).toBe('high');
  });

  it('flags a recurring-fault-versus-scheduled collision', () => {
    const out = S.detectCollisions([], repeatFaults, scheduled, 'CBE-L3');
    expect(out).toHaveLength(1);
    expect(out[0].fault_code).toBe('4021');
  });

  it('explains the mechanism rather than just asserting risk', () => {
    const out = S.detectCollisions([], repeatFaults, scheduled, 'CBE-L3');
    expect(out[0].mechanism).toMatch(/power.cycle/i);
  });

  it('finds both collisions when both carryover types are present', () => {
    expect(S.detectCollisions(openMaintenance, repeatFaults, scheduled, 'CBE-L3')).toHaveLength(2);
  });

  it('returns nothing when there is no scheduled activity', () => {
    expect(S.detectCollisions(openMaintenance, repeatFaults, [], 'CBE-L3')).toEqual([]);
  });
});

describe('rankCarryovers', () => {
  const items = [
    { summary: 'Reject bin at station 4 approaching full' },
    { summary: 'Maintenance job on gate S2-B, housing open, not complete' },
    { summary: 'Fault 4021 recurring, interlock keeps dropping out, unresolved' },
    { summary: 'CBE-L3 tooling changeover scheduled 02:00' }
  ];

  it('puts maintenance first', () => {
    expect(S.rankCarryovers(items, 'C').ranked[0].category).toBe(S.CATEGORY.MAINTENANCE_OPEN);
  });

  it('orders all four categories correctly', () => {
    const cats = S.rankCarryovers(items, 'A').ranked.map((r) => r.category);
    expect(cats).toEqual([1, 2, 3, 4]);
  });

  it('marks categories 1 and 2 as requiring acknowledgement', () => {
    expect(S.rankCarryovers(items, 'A').requires_acknowledgement).toHaveLength(2);
  });

  it('escalates on night shift', () => {
    expect(S.rankCarryovers(items, 'C').escalation_required).toBe(true);
  });

  it('does not escalate on a day shift', () => {
    expect(S.rankCarryovers(items, 'A').escalation_required).toBe(false);
  });

  it('flags night-shift elevation on high-risk items only', () => {
    const ranked = S.rankCarryovers(items, 'C').ranked;
    expect(ranked[0].elevated_because_night_shift).toBe(true);
    expect(ranked[3].elevated_because_night_shift).toBe(false);
  });

  it('handles an empty list', () => {
    const out = S.rankCarryovers([], 'B');
    expect(out.ranked).toEqual([]);
    expect(out.highest_category).toBeNull();
  });

  it('names each category', () => {
    expect(S.rankCarryovers(items, 'A').ranked[0].category_name)
      .toBe('Maintenance in progress');
  });
});

/* ------------------------------------------------------------------ *
 * The action handlers themselves, invoked with mocked FDK globals.
 * ------------------------------------------------------------------ */

describe('build_shift_context action', () => {
  const TICKETS = {
    tickets: [
      { id: 1, subject: '[16:20] CBE-L3 - Line stopped, safety gate interlock fault 4021',
        description_text: 'Gate reseated. 12 minutes lost.', status: 5, tags: ['cbe-l3'] },
      { id: 2, subject: '[19:45] CBE-L3 - Line stopped again, interlock dropout, fault 4021',
        description_text: 'Reset twice. 18 minutes lost.', status: 5, tags: ['cbe-l3'] },
      { id: 3, subject: '[20:10] CBE-L3 - Maintenance job - safety gate S2-B inspection',
        description_text: 'Housing left open. Not complete.', status: 2,
        tags: ['cbe-l3', 'maintenance'] },
      { id: 4, subject: '[21:50] CBE-L3 - Unusual noise from conveyor drive',
        description_text: 'Not investigated.', status: 2, tags: ['cbe-l3'] },
      { id: 5, subject: '[15:05] CBE-L2 - Vision no-read, fault 8810',
        description_text: '17 minutes lost.', status: 5, tags: ['cbe-l2'] }
    ]
  };
  const CHANGES = {
    changes: [
      { id: 90, subject: 'CBE-L3 tooling changeover - Variant B to Variant C',
        planned_start_date: new Date(Date.now() + 3 * 36e5).toISOString(),
        planned_end_date: new Date(Date.now() + 7 * 36e5).toISOString(), risk: 2 },
      { id: 91, subject: 'PUN-L1 conveyor belt replacement',
        planned_start_date: new Date(Date.now() + 9 * 24 * 36e5).toISOString(), risk: 1 }
    ]
  };
  const PROBLEMS = {
    problems: [{ id: 70, subject: 'Recurring safety gate interlock dropouts on CBE-L3' }]
  };

  async function invoke(payload) {
    let captured = null;
    global.renderData = (err, data) => { captured = { err, data }; };
    global.$request = {
      invokeTemplate: async (name) => {
        if (name === 'getTickets') return { response: JSON.stringify(TICKETS) };
        if (name === 'getChanges') return { response: JSON.stringify(CHANGES) };
        return { response: JSON.stringify(PROBLEMS) };
      }
    };
    await S.actions.build_shift_context(payload);
    return captured;
  }

  it('counts stoppages on the requested line only', async () => {
    const { data } = await invoke({ line_id: 'CBE-L3' });
    expect(data.stoppage_count).toBe(2);
  });

  it('sums downtime across the shift', async () => {
    const { data } = await invoke({ line_id: 'CBE-L3' });
    expect(data.total_downtime_minutes).toBe(30);
  });

  it('finds the repeat fault signature', async () => {
    const { data } = await invoke({ line_id: 'CBE-L3' });
    expect(data.repeat_faults[0].fault_code).toBe('4021');
    expect(data.repeat_faults[0].occurrences).toBe(2);
  });

  it('finds the open maintenance job', async () => {
    const { data } = await invoke({ line_id: 'CBE-L3' });
    expect(data.open_maintenance).toHaveLength(1);
    expect(data.open_maintenance[0].at).toBe('20:10');
  });

  it('finds the change scheduled in the incoming window', async () => {
    const { data } = await invoke({ line_id: 'CBE-L3' });
    expect(data.scheduled_in_window).toHaveLength(1);
    expect(data.scheduled_in_window[0].id).toBe(90);
  });

  it('detects both collisions', async () => {
    const { data } = await invoke({ line_id: 'CBE-L3' });
    expect(data.collisions).toHaveLength(2);
  });

  it('orders carryovers by category', async () => {
    const { data } = await invoke({ line_id: 'CBE-L3' });
    const cats = data.carryovers.map((c) => c.category);
    expect(cats).toEqual([...cats].sort((a, b) => a - b));
    expect(cats[0]).toBe(1);
  });

  it('refuses a quiet-shift claim when the record contradicts it', async () => {
    const { data } = await invoke({ line_id: 'CBE-L3' });
    expect(data.quiet_shift_claim_valid).toBe(false);
  });

  it('allows a quiet-shift claim on a line with nothing on it', async () => {
    const { data } = await invoke({ line_id: 'PUN-L9' });
    expect(data.quiet_shift_claim_valid).toBe(true);
    expect(data.stoppage_count).toBe(0);
  });

  it('surfaces the known problem record', async () => {
    const { data } = await invoke({ line_id: 'CBE-L3' });
    expect(data.known_problems[0].id).toBe(70);
  });

  it('defaults line_id to empty rather than throwing', async () => {
    const { data } = await invoke({});
    expect(data.line_id).toBe('');
  });

  it('returns a structured error when a request template fails', async () => {
    let captured = null;
    global.renderData = (err, data) => { captured = { err, data }; };
    global.$request = {
      invokeTemplate: async () => { throw Object.assign(new Error('upstream 503'),
                                                        { status: 503 }); }
    };
    await S.actions.build_shift_context({ line_id: 'CBE-L3' });
    expect(captured.err.status).toBe(503);
    expect(captured.err.message).toMatch(/build_shift_context failed/);
  });
});

describe('score_carryover_risk action', () => {
  async function invoke(payload) {
    let captured = null;
    global.renderData = (err, data) => { captured = { err, data }; };
    await S.actions.score_carryover_risk(payload);
    return captured;
  }

  it('ranks maintenance highest', async () => {
    const { data } = await invoke({
      items: [{ summary: 'Reject bin full' },
              { summary: 'Maintenance job, housing open, not complete' }],
      incoming_shift: 'C'
    });
    expect(data.ranked[0].category).toBe(1);
  });

  it('escalates on night shift', async () => {
    const { data } = await invoke({
      items: [{ summary: 'Maintenance in progress' }], incoming_shift: 'C'
    });
    expect(data.escalation_required).toBe(true);
  });

  it('rejects a non-array items payload', async () => {
    const { err } = await invoke({ items: 'not an array' });
    expect(err.status).toBe(400);
  });

  it('handles a missing payload without throwing', async () => {
    const { data } = await invoke(undefined);
    expect(data.ranked).toEqual([]);
  });
});
