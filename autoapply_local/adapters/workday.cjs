'use strict';
(function install(root) {
  const base = typeof module !== 'undefined' && module.exports ? require('./common.cjs') : root.AutoApplyDOM;
  if (!base) throw new Error('Load common.cjs before the ATS adapter');
  async function navigate(document, direction) {
    if (!['next', 'back'].includes(direction)) throw new Error('Navigation must be next or back');
    const button = document.querySelector(
      `[data-automation-id="bottom-navigation-${direction}-button"],[data-automation-id="pageFooter${direction === 'next' ? 'Next' : 'Back'}Button"]`,
    );
    if (!button || button.disabled || button.type === 'submit' || /submit/i.test(button.innerText)) {
      throw new Error('Navigation requires native MCP interaction; no safe navigation button');
    }
    const previous = base.scan(document).page;
    button.click();
    const deadline = Date.now() + 3000;
    do {
      const current = base.audit(document);
      if (current.errors.length) throw new Error('Navigation validation error: ' + current.errors.join('; '));
      if (current.page.index !== previous.index) return current.page;
      await new Promise(resolve => setTimeout(resolve, 25));
    } while (Date.now() < deadline);
    throw new Error('Page transition requires native MCP interaction');
  }
  async function fill(document, answers) {
    const blocked = [],
      safe = {};
    const observed = Boolean(document.querySelector('main')?.innerText?.match(/current step\s+\d+\s+of\s+\d+/i));
    for (const [id, value] of Object.entries(answers)) {
      const el = base.resolve(document, id);
      const identity = id + ' ' + (el?.getAttribute('data-automation-id') || '');
      if (
        /name--|employee.?id|fieldOfStudy|countryPhoneCode|dateSection|roleDescription/i.test(identity) ||
        (observed && el?.tagName === 'TEXTAREA')
      )
        blocked.push({ id, reason: 'agent_interaction_required' });
      else safe[id] = value;
    }
    const result = await base.fill(document, safe);
    result.blocked.push(...blocked);
    return result;
  }
  async function syncRecords(document, kind, records) {
    if (!['education', 'experience'].includes(kind) || !Array.isArray(records))
      throw new Error('Invalid repeated record request');
    const section = [...document.querySelectorAll('section')].find(
      el => el.getAttribute('data-record-kind') === kind || el.getAttribute('data-automation-id') === kind,
    );
    if (!section) throw new Error('Repeated section requires agent interaction');
    const getRecords = () =>
      [...section.querySelectorAll('fieldset')].filter(el => el.getAttribute('data-record-kind') === kind);
    let current = getRecords();
    while (current.length < records.length) {
      const add = section.querySelector('[data-action="add-record"],[data-automation-id="add-button"]');
      if (!add || add.disabled || add.type === 'submit') throw new Error('Add record requires agent interaction');
      const previous = current.length;
      add.click();
      current = getRecords();
      if (current.length <= previous) throw new Error('Wait for ATS DOM update using MCP, then rescan');
    }
    while (current.length > records.length) {
      const remove = current[current.length - 1].querySelector(
        '[data-action="remove-record"],[data-automation-id="delete-button"]',
      );
      if (!remove || remove.disabled || remove.type === 'submit')
        throw new Error('Remove record requires agent interaction');
      const previous = current.length;
      remove.click();
      current = getRecords();
      if (current.length >= previous) throw new Error('Wait for ATS DOM update using MCP, then rescan');
    }
    return base.scan(document);
  }
  const api = { ...base, fill, navigate, syncRecords };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyWorkday = api;
})(globalThis);
