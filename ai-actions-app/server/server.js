/**
 * Shift Handover Intelligence — AI Actions app
 *
 * Exposes two operations to Freshservice AI Agent Studio.
 *
 * WHY THIS APP EXISTS
 * -------------------
 * The Stage 1 agent assembles shift context using three separate no-code
 * Custom API actions and asks the LLM to spot patterns across the raw JSON.
 * That works for a demo and fails in production for three specific reasons:
 *
 *   1. Agent Studio cannot iterate or group. It ships an Execute Function
 *      node with 14 pre-built Functions covering arithmetic, date
 *      comparison and string ops - so scalar transformation IS available,
 *      and any claim otherwise is wrong. What has no construct is walking
 *      an unbounded collection and aggregating over it. findRepeatFaults()
 *      below groups N tickets by fault signature and sums downtime per
 *      group; detectCollisions() is an O(n*m) cross-product. Neither is
 *      expressible in the builder.
 *   2. Three sequential API actions is three chances to fail, each
 *      configured per-account by hand with no version control or tests.
 *   3. Nothing in a no-code build is portable. This app is a distributable
 *      unit another Freshservice customer can install - the actual meaning
 *      of "reusable skills" in the Track 2 brief.
 *
 * This app moves all of that server-side. One call in, structured verdict
 * out. The LLM is left doing the thing it is actually good at — conducting
 * the conversation — rather than doing set operations on JSON in its head.
 */

const DEFAULT_SHIFT_HOURS = 8;
const DEFAULT_LOOKAHEAD_HOURS = 8;

/** Carryover categories, from the Kestrel handover policy. */
const CATEGORY = {
  MAINTENANCE_OPEN: 1,
  RECURRING_FAULT: 2,
  SCHEDULED_ACTIVITY: 3,
  INFORMATIONAL: 4
};

const CATEGORY_NAME = {
  1: 'Maintenance in progress',
  2: 'Recurring or unresolved fault',
  3: 'Scheduled activity in incoming window',
  4: 'Informational'
};

/* ------------------------------------------------------------------ *
 * Pure helpers — exported for unit test, no I/O
 * ------------------------------------------------------------------ */

/** Pull a fault code out of free text: "fault 4021", "4021", "E-Stop 8810". */
function extractFaultCode(text) {
  if (!text) return null;
  const m = String(text).match(/\bfault\s*(\d{3,5})\b/i) || String(text).match(/\b(\d{4})\b/);
  return m ? m[1] : null;
}

/** Pull the shift clock time out of a seeded subject: "[19:45] CBE-L3 — ...". */
function extractClockTime(subject) {
  const m = String(subject || '').match(/\[(\d{2}:\d{2})\]/);
  return m ? m[1] : null;
}

/** Pull "12 minutes lost" / "18 minutes" out of a description. */
function extractDowntimeMinutes(text) {
  if (!text) return 0;
  const m = String(text).match(/(\d{1,4})\s*minutes?\s*(lost|down)?/i);
  return m ? parseInt(m[1], 10) : 0;
}

/** Does this record belong to the line we are handing over? */
function matchesLine(record, lineId) {
  if (!lineId) return true;
  const hay = [
    record.subject,
    record.description_text,
    record.description,
    (record.tags || []).join(' ')
  ].join(' ').toUpperCase();
  return hay.includes(String(lineId).toUpperCase());
}

function isOpen(status) {
  // Freshservice: 2 Open, 3 Pending, 4 Resolved, 5 Closed
  return status === 2 || status === 3;
}

function looksLikeMaintenance(record) {
  const hay = `${record.subject || ''} ${(record.tags || []).join(' ')}`.toLowerCase();
  return hay.includes('maintenance') || hay.includes('inspection') || hay.includes('repair');
}

function looksLikeStoppage(record) {
  const hay = `${record.subject || ''} ${(record.tags || []).join(' ')}`.toLowerCase();
  return hay.includes('stopped') || hay.includes('stoppage') || hay.includes('jam') ||
         hay.includes('down');
}

/**
 * Group stoppages by fault signature and return only signatures seen 2+ times.
 * This is the thing the no-code workflow cannot do: iterate and group.
 */
function findRepeatFaults(tickets) {
  const bySignature = {};
  tickets.filter(looksLikeStoppage).forEach((t) => {
    const code = extractFaultCode(t.subject) || extractFaultCode(t.description_text);
    if (!code) return;
    if (!bySignature[code]) bySignature[code] = [];
    bySignature[code].push({
      id: t.id,
      subject: t.subject,
      at: extractClockTime(t.subject),
      downtime_minutes: extractDowntimeMinutes(t.description_text || t.description)
    });
  });

  return Object.keys(bySignature)
    .filter((code) => bySignature[code].length >= 2)
    .map((code) => ({
      fault_code: code,
      occurrences: bySignature[code].length,
      events: bySignature[code],
      total_downtime_minutes: bySignature[code]
        .reduce((sum, e) => sum + (e.downtime_minutes || 0), 0)
    }))
    .sort((a, b) => b.occurrences - a.occurrences);
}

/** Changes whose planned start falls inside the incoming shift window. */
function findScheduledInWindow(changes, lineId, lookaheadHours, now) {
  const from = now.getTime();
  const to = from + lookaheadHours * 3600 * 1000;
  return changes
    .filter((c) => matchesLine(c, lineId))
    .filter((c) => {
      if (!c.planned_start_date) return false;
      const t = new Date(c.planned_start_date).getTime();
      return !Number.isNaN(t) && t >= from && t <= to;
    })
    .map((c) => ({
      id: c.id,
      subject: c.subject,
      planned_start_date: c.planned_start_date,
      planned_end_date: c.planned_end_date,
      risk: c.risk,
      hours_from_now: Math.round((new Date(c.planned_start_date).getTime() - from) / 36e5)
    }));
}

/**
 * The core insight, expressed as code.
 *
 * A collision is an unresolved Category 1 or Category 2 item sharing a line
 * with scheduled activity in the incoming window. Neither supervisor has any
 * reason to spot it — the outgoing one is thinking about what happened, the
 * incoming one about what is planned. Nobody is thinking about the overlap.
 */
function detectCollisions(openMaintenance, repeatFaults, scheduledInWindow, lineId) {
  if (!scheduledInWindow.length) return [];
  const collisions = [];

  scheduledInWindow.forEach((change) => {
    openMaintenance.forEach((job) => {
      collisions.push({
        severity: 'high',
        line_id: lineId,
        carryover_category: CATEGORY.MAINTENANCE_OPEN,
        carryover: job.subject,
        carryover_ticket_id: job.id,
        scheduled_activity: change.subject,
        scheduled_change_id: change.id,
        starts_in_hours: change.hours_from_now,
        mechanism:
          'Equipment is in a partially worked state and has not been confirmed back to ' +
          'specification. The scheduled activity assumes the line is in a known good state.'
      });
    });

    repeatFaults.forEach((fault) => {
      collisions.push({
        severity: 'high',
        line_id: lineId,
        carryover_category: CATEGORY.RECURRING_FAULT,
        carryover: `Recurring fault ${fault.fault_code}, ${fault.occurrences} occurrences this shift`,
        fault_code: fault.fault_code,
        scheduled_activity: change.subject,
        scheduled_change_id: change.id,
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

/** Rank carryovers by category, then by whether they collide. */
function rankCarryovers(items, incomingShift) {
  const nightShift = String(incomingShift || '').toUpperCase() === 'C';

  const ranked = items
    .map((item) => {
      const text = `${item.summary || item.subject || ''} ${item.status || ''}`.toLowerCase();
      let category = CATEGORY.INFORMATIONAL;

      if (/maintenance|inspection|repair|partially|housing open|not complete|in progress/.test(text)) {
        category = CATEGORY.MAINTENANCE_OPEN;
      } else if (/recurring|repeat|again|second time|unresolved|keeps|intermittent/.test(text)) {
        category = CATEGORY.RECURRING_FAULT;
      } else if (/scheduled|changeover|planned|window|tonight/.test(text)) {
        category = CATEGORY.SCHEDULED_ACTIVITY;
      }

      return {
        ...item,
        category,
        category_name: CATEGORY_NAME[category],
        requires_acknowledgement: category === CATEGORY.MAINTENANCE_OPEN ||
                                  category === CATEGORY.RECURRING_FAULT,
        elevated_because_night_shift: nightShift && category <= CATEGORY.RECURRING_FAULT
      };
    })
    .sort((a, b) => a.category - b.category);

  const requiresAck = ranked.filter((r) => r.requires_acknowledgement);

  return {
    ranked,
    requires_acknowledgement: requiresAck,
    highest_category: ranked.length ? ranked[0].category : null,
    escalation_required: requiresAck.length > 0 && nightShift
  };
}

/* ------------------------------------------------------------------ *
 * Actions invoked by AI Agent Studio
 * ------------------------------------------------------------------ */

exports = {

  /**
   * One call replaces three no-code API actions plus the grouping,
   * summing and date comparison that Agent Studio cannot perform.
   */
  build_shift_context: async function (payload) {
    try {
      const lineId = (payload && payload.line_id) || '';
      const lookahead = (payload && payload.incoming_shift_hours) || DEFAULT_LOOKAHEAD_HOURS;
      const now = new Date();

      const [ticketRes, changeRes, problemRes] = await Promise.all([
        $request.invokeTemplate('getTickets', {}),
        $request.invokeTemplate('getChanges', {}),
        $request.invokeTemplate('getProblems', {})
      ]);

      const tickets = (JSON.parse(ticketRes.response).tickets || [])
        .filter((t) => matchesLine(t, lineId));
      const changes = JSON.parse(changeRes.response).changes || [];
      const problems = (JSON.parse(problemRes.response).problems || [])
        .filter((p) => matchesLine(p, lineId));

      const stoppages = tickets.filter(looksLikeStoppage);
      const openMaintenance = tickets
        .filter((t) => looksLikeMaintenance(t) && isOpen(t.status))
        .map((t) => ({
          id: t.id,
          subject: t.subject,
          at: extractClockTime(t.subject),
          status: t.status
        }));

      const repeatFaults = findRepeatFaults(tickets);
      const scheduledInWindow = findScheduledInWindow(changes, lineId, lookahead, now);
      const collisions = detectCollisions(openMaintenance, repeatFaults, scheduledInWindow, lineId);

      const otherOpen = tickets
        .filter((t) => isOpen(t.status) && !looksLikeMaintenance(t))
        .map((t) => ({ id: t.id, subject: t.subject, at: extractClockTime(t.subject) }));

      const carryovers = [
        ...openMaintenance.map((m) => ({
          summary: m.subject, ticket_id: m.id, category: CATEGORY.MAINTENANCE_OPEN,
          category_name: CATEGORY_NAME[1], requires_acknowledgement: true
        })),
        ...repeatFaults.map((f) => ({
          summary: `Fault ${f.fault_code} recurred ${f.occurrences} times, ` +
                   `${f.total_downtime_minutes} minutes lost`,
          category: CATEGORY.RECURRING_FAULT,
          category_name: CATEGORY_NAME[2], requires_acknowledgement: true
        })),
        ...scheduledInWindow.map((c) => ({
          summary: c.subject, change_id: c.id, category: CATEGORY.SCHEDULED_ACTIVITY,
          category_name: CATEGORY_NAME[3], requires_acknowledgement: false
        })),
        ...otherOpen.map((t) => ({
          summary: t.subject, ticket_id: t.id, category: CATEGORY.INFORMATIONAL,
          category_name: CATEGORY_NAME[4], requires_acknowledgement: false
        }))
      ].sort((a, b) => a.category - b.category);

      const totalDowntime = stoppages.reduce(
        (sum, t) => sum + extractDowntimeMinutes(t.description_text || t.description), 0);

      renderData(null, {
        line_id: lineId,
        generated_at: now.toISOString(),
        stoppage_count: stoppages.length,
        total_downtime_minutes: totalDowntime,
        carryovers,
        repeat_faults: repeatFaults,
        open_maintenance: openMaintenance,
        scheduled_in_window: scheduledInWindow,
        collisions,
        known_problems: problems.map((p) => ({ id: p.id, subject: p.subject })),
        // The single most useful field: it lets the agent refuse
        // "quiet shift, nothing to report" with evidence.
        quiet_shift_claim_valid:
          stoppages.length === 0 &&
          openMaintenance.length === 0 &&
          scheduledInWindow.length === 0
      });
    } catch (error) {
      renderData({
        status: error.status || 500,
        message: `build_shift_context failed: ${error.message}`
      });
    }
  },

  score_carryover_risk: async function (payload) {
    try {
      const items = (payload && payload.items) || [];
      if (!Array.isArray(items)) {
        return renderData({ status: 400, message: 'items must be an array' });
      }
      renderData(null, rankCarryovers(items, payload && payload.incoming_shift));
    } catch (error) {
      renderData({
        status: error.status || 500,
        message: `score_carryover_risk failed: ${error.message}`
      });
    }
  }
};

// Exported for unit tests only. The FDK reads the global `exports` assignment
// above; module.exports is inert in production and exists so the pure helpers
// and the action handlers can both be exercised by vitest.
if (typeof module !== 'undefined') {
  module.exports = {
    actions: exports,
    extractFaultCode,
    extractClockTime,
    extractDowntimeMinutes,
    matchesLine,
    isOpen,
    looksLikeMaintenance,
    looksLikeStoppage,
    findRepeatFaults,
    findScheduledInWindow,
    detectCollisions,
    rankCarryovers,
    CATEGORY,
    CATEGORY_NAME
  };
}
