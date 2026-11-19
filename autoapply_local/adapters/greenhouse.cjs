'use strict';
// Greenhouse job-board adapter for the script driver. It only reads and sets
// ordinary controls: no click, no Submit, no fake upload. React-select inputs
// (location, EEO, country code) are held for native engine interaction.
(function install(root) {
  const base = typeof module !== 'undefined' && module.exports ? require('./common.cjs') : root.AutoApplyDOM;
  if (!base) throw new Error('Load common.cjs before the ATS adapter');
  const required = /\*\s*$/;
  const clean = text =>
    String(text || '')
      .replace(/\s+/g, ' ')
      .replace(/\s*\*\s*$/, '')
      .trim();
  const widgetShell = el => el.closest('[class*="select__"],[class*="select-shell"]');
  const isWidget = el =>
    el?.tagName === 'INPUT' && (el.getAttribute('role') === 'combobox' || Boolean(widgetShell(el)));
  const empty = value =>
    value === '' || value === null || value === undefined || value === false || (Array.isArray(value) && !value.length);
  function decorate(document, result) {
    for (const field of result.fields) {
      if (field.type === 'summary') continue;
      const el = base.resolve(document, field.id);
      if (!el) continue;
      if (required.test(String(field.label || ''))) field.required = true;
      field.label = clean(field.label);
      if (isWidget(el)) {
        field.type = 'combobox';
        // react-select keeps its input empty and renders the choice beside it.
        const chosen = widgetShell(el)?.querySelector('[class*="single-value"]')?.innerText?.trim();
        if (chosen) field.value = chosen;
      }
    }
    return result;
  }
  const scan = document => decorate(document, base.groupChecks(document, base.scan(document)));
  function audit(document) {
    const result = decorate(document, base.groupChecks(document, base.audit(document)));
    const values = new Map(result.fields.map(field => [field.id, field]));
    const chosen = id => values.get(id)?.type === 'combobox' && !empty(values.get(id).value);
    const unknown = new Set(result.required_unknown.filter(id => !chosen(id)));
    for (const field of result.fields) if (field.required && empty(field.value)) unknown.add(field.id);
    result.required_unknown = [...unknown];
    return result;
  }
  async function fill(document, answers) {
    const blocked = [],
      safe = {};
    for (const [id, value] of Object.entries(answers)) {
      const el = base.resolve(document, id);
      if (isWidget(el) || base.checkGroup(el)) blocked.push({ id, reason: 'agent_interaction_required' });
      else safe[id] = value;
    }
    const result = await base.fill(document, safe);
    result.blocked.push(...blocked);
    return result;
  }
  const api = { ...base, scan, audit, fill };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyGreenhouse = api;
})(globalThis);
