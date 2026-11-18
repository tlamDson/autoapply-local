'use strict';
// Lever application-form adapter for the script driver. Lever controls have no
// stable IDs, so each one is bound to its form name (name, urls[LinkedIn],
// cards[<id>][fieldN], eeo[gender]). It only reads and sets ordinary controls:
// no click, no Submit, no fake upload. The location suggestion box and
// checkbox-group questions are held for native engine interaction; captcha
// responses are never scanned or filled.
(function install(root) {
  const base = typeof module !== 'undefined' && module.exports ? require('./common.cjs') : root.AutoApplyDOM;
  if (!base) throw new Error('Load common.cjs before the ATS adapter');
  const binding = 'data-autoapply-binding';
  const ignored = /^(h-captcha-response|g-recaptcha-response)$/;
  const required = /✱/;
  const clean = text =>
    String(text || '')
      .replace(/✱/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  const empty = value =>
    value === '' || value === null || value === undefined || value === false || (Array.isArray(value) && !value.length);
  const controls = document =>
    [...document.querySelectorAll('input,textarea,select')].filter(el => el.type !== 'hidden' && !el.disabled);
  const named = (document, name) => controls(document).filter(el => el.name === name);
  // Bind the first control of each name to that name, once; later controls of
  // a radio or checkbox group get the common serial binding.
  function bind(document) {
    const all = controls(document);
    const taken = new Set(all.flatMap(el => [el.id, el.getAttribute(binding)]).filter(Boolean));
    for (const el of all) {
      if (el.getAttribute(binding) || !el.name || taken.has(el.name)) continue;
      el.setAttribute(binding, el.name);
      taken.add(el.name);
    }
  }
  const question = el => el.closest('[class*="application-question"]');
  const questionLabel = el => question(el)?.querySelector('[class*="application-label"]')?.innerText || null;
  const isLocation = el => el?.name === 'location';
  const isGroup = (document, el) => el?.type === 'checkbox' && named(document, el.name).length > 1;
  const isIgnored = el => ignored.test(el?.name || '');
  function decorate(document, result) {
    const fields = [];
    for (const field of result.fields) {
      if (field.type === 'summary') {
        fields.push(field);
        continue;
      }
      const el = base.resolve(document, field.id);
      if (!el || isIgnored(el)) continue;
      const raw = questionLabel(el);
      if (raw !== null) {
        field.label = clean(raw);
        if (required.test(raw)) field.required = true;
      } else field.label = clean(field.label);
      if (isLocation(el)) {
        field.type = 'combobox';
        // Lever keeps the typed text in the input; only a chosen suggestion
        // fills the hidden selectedLocation that Lever submits.
        const chosen = document.querySelector('input[name="selectedLocation"]');
        field.value = chosen?.value ? el.value : '';
      } else if (el.type === 'checkbox' && el.name) {
        const group = named(document, el.name);
        if (group.length > 1) {
          if (group[0] !== el) continue;
          field.type = 'checkbox-group';
          field.options = group.map(box => ({ value: box.value, label: clean(box.labels?.[0]?.innerText || box.value) }));
          field.value = group.filter(box => box.checked).map(box => box.value);
        }
      }
      fields.push(field);
    }
    result.fields = fields;
    return result;
  }
  function scan(document) {
    bind(document);
    return decorate(document, base.scan(document));
  }
  function audit(document) {
    bind(document);
    const result = decorate(document, base.audit(document));
    result.required_unknown = result.fields
      .filter(field => field.type !== 'summary' && field.required)
      .filter(field => empty(field.value) || (field.type === 'checkbox' && field.value !== true))
      .map(field => field.id);
    return result;
  }
  async function fill(document, answers) {
    bind(document);
    const blocked = [],
      safe = {};
    for (const [id, value] of Object.entries(answers)) {
      const el = base.resolve(document, id);
      if (isIgnored(el)) blocked.push({ id, reason: 'field_not_found' });
      else if (isLocation(el) || isGroup(document, el)) blocked.push({ id, reason: 'agent_interaction_required' });
      else safe[id] = value;
    }
    const result = await base.fill(document, safe);
    result.blocked.push(...blocked);
    return result;
  }
  const api = { ...base, scan, audit, fill };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyLever = api;
})(globalThis);
