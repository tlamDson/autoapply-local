'use strict';
(function install(root) {
  const base = typeof module !== 'undefined' && module.exports ? require('./common.cjs') : root.AutoApplyDOM;
  if (!base) throw new Error('Load common.cjs before the ATS adapter');
  // Fill and audit only: nothing here clicks. Moving the tab (Next, Add/Remove,
  // sign-in) lives in workday_steps.cjs behind its guarded press().
  async function fill(document, answers) {
    const blocked = [],
      safe = {};
    const observed = Boolean(document.querySelector('main')?.innerText?.match(/current step\s+\d+\s+of\s+\d+/i));
    for (const [id, value] of Object.entries(answers)) {
      const el = base.resolve(document, id);
      const identity = id + ' ' + (el?.getAttribute('data-automation-id') || '');
      if (
        /name--|employee.?id|fieldOfStudy|countryPhoneCode|dateSection|roleDescription/i.test(identity) ||
        base.checkGroup(el) ||
        (observed && el?.tagName === 'TEXTAREA')
      )
        blocked.push({ id, reason: 'agent_interaction_required' });
      else safe[id] = value;
    }
    const result = await base.fill(document, safe);
    result.blocked.push(...blocked);
    return result;
  }
  // A multi-select checkbox question is one field (common.cjs groupChecks).
  const scan = document => base.groupChecks(document, base.scan(document));
  const audit = document => base.groupChecks(document, base.audit(document));
  const api = { ...base, scan, audit, fill };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyWorkday = api;
})(globalThis);
