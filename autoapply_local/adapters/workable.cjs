'use strict';
// Workable application-form adapter for the script driver. Workable wraps each
// question in a [data-ui=<form-API id>] box (firstname, phone, QA_100), so each
// control is bound to the id of its outermost box. It only reads and sets
// ordinary controls: no click, no Submit, no fake upload. Yes/No role=radio
// buttons, dropdown comboboxes, checkbox groups and the education/experience
// entry lists are held for native engine interaction; the cookie banner and
// captcha responses are never scanned or filled.
(function install(root) {
  const base = typeof module !== 'undefined' && module.exports ? require('./common.cjs') : root.AutoApplyDOM;
  if (!base) throw new Error('Load common.cjs before the ATS adapter');
  const binding = 'data-autoapply-binding';
  const ignored = /^(h-captcha-response|g-recaptcha-response)$/;
  const required = /\*\s*$/;
  const clean = text =>
    String(text || '')
      .replace(/\s+/g, ' ')
      .replace(/\s*\*\s*$/, '')
      .trim();
  const empty = value =>
    value === '' || value === null || value === undefined || value === false || (Array.isArray(value) && !value.length);
  const ui = el => el?.getAttribute?.('data-ui') ?? null;
  // The outermost data-ui ancestor: nested data-ui marks parts of one question.
  function topBox(el) {
    let box = null;
    for (let node = el; node; node = node.parentElement) if (ui(node) !== null) box = node;
    return box;
  }
  const boxes = document => [...document.querySelectorAll('[data-ui]')].filter(el => topBox(el) === el);
  const boxFor = (document, id) => boxes(document).find(box => ui(box) === id) || null;
  const isRadioBox = box => Boolean(box?.querySelector('[role="radio"]'));
  const isGroupBox = box => Boolean(box?.querySelector('[data-ui="add-section"]'));
  const heldBox = box => isRadioBox(box) || isGroupBox(box);
  const controls = document =>
    [...document.querySelectorAll('input,textarea,select')].filter(el => el.type !== 'hidden' && !el.disabled);
  const boxChecks = box => (box ? [...box.querySelectorAll('input')].filter(el => el.type === 'checkbox') : []);
  const isIgnored = el => ignored.test(el?.name || '');
  const isCombo = el => el?.getAttribute('role') === 'combobox' || /^input_.+_input$/.test(el?.id || '');
  const isCheckGroup = el => el?.type === 'checkbox' && boxChecks(topBox(el)).length > 1;
  // Bind the first control of each box to the box id, once; later controls of
  // a checkbox group get the common serial binding.
  function bind(document) {
    const all = controls(document);
    const owner = new Map();
    for (const el of all) for (const key of [el.id, el.getAttribute(binding)]) if (key && !owner.has(key)) owner.set(key, el);
    for (const el of all) {
      const box = topBox(el);
      const id = ui(box);
      if (el.getAttribute(binding) || !id || heldBox(box) || (owner.has(id) && owner.get(id) !== el)) continue;
      el.setAttribute(binding, id);
      owner.set(id, el);
    }
  }
  const boxLabel = box => box?.querySelector('label')?.innerText ?? null;
  function heldField(box) {
    const raw = boxLabel(box);
    const field = {
      id: ui(box),
      label: clean(raw),
      type: 'group',
      required: required.test(String(raw || '').trim()) || box.getAttribute('aria-required') === 'true',
      value: [],
      options: [],
    };
    if (isRadioBox(box)) {
      const radios = [...box.querySelectorAll('[role="radio"]')];
      const chosen = radios.find(el => el.getAttribute('aria-checked') === 'true');
      field.type = 'radiogroup';
      field.value = chosen ? clean(chosen.innerText) : null;
      field.options = radios.map(el => ({ value: clean(el.innerText), label: clean(el.innerText) }));
    } else field.value = [...box.querySelectorAll('[data-ui="group"]')].map(card => clean(card.innerText));
    return field;
  }
  function decorate(document, result) {
    const byBox = new Map(),
      loose = [],
      summaries = [];
    for (const field of result.fields) {
      if (field.type === 'summary') {
        summaries.push(field);
        continue;
      }
      const el = base.resolve(document, field.id);
      if (!el || isIgnored(el)) continue;
      const box = topBox(el);
      if (heldBox(box)) continue;
      const raw = boxLabel(box);
      if (raw !== null) {
        field.label = clean(raw);
        if (required.test(raw.trim()) || box.getAttribute('aria-required') === 'true') field.required = true;
      } else field.label = clean(field.label);
      if (isCombo(el)) field.type = 'combobox';
      else if (isCheckGroup(el)) {
        const group = boxChecks(box);
        if (group[0] !== el) continue;
        field.type = 'checkbox-group';
        field.options = group.map(item => ({ value: item.value, label: clean(item.labels?.[0]?.innerText || item.value) }));
        field.value = group.filter(item => item.checked).map(item => item.value);
      }
      if (!box) loose.push(field);
      else if (byBox.has(box)) byBox.get(box).push(field);
      else byBox.set(box, [field]);
    }
    const fields = [];
    for (const box of boxes(document)) {
      if (heldBox(box)) fields.push(heldField(box));
      else fields.push(...(byBox.get(box) || []));
    }
    result.fields = [...fields, ...loose, ...summaries];
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
      else if (heldBox(boxFor(document, id)) || isCombo(el) || isCheckGroup(el))
        blocked.push({ id, reason: 'agent_interaction_required' });
      else safe[id] = value;
    }
    const result = await base.fill(document, safe);
    result.blocked.push(...blocked);
    return result;
  }
  const api = { ...base, scan, audit, fill };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyWorkable = api;
})(globalThis);
