'use strict';

// Ashby (jobs.ashbyhq.com) form adapter on top of common.cjs. Ashby marks
// required questions on the entry heading and renders Boolean questions as
// Yes/No toggle buttons, not inputs. The adapter never clicks (script drivers
// refuse any script that can): a confirmed answer whose option is not already
// active is handed back as native_click_required with that option, and an
// option that is a submit control is refused. Scripted clicks also do not stick
// on Ashby's React toggles, so the native browser tool clicks them.
(function install(root) {
  const base = typeof module !== 'undefined' && module.exports ? require('./common.cjs') : root.AutoApplyDOM;
  if (!base) throw new Error('Load common.cjs before the ATS adapter');
  const ENTRY = '[class*="ashby-application-form-field-entry"]';
  const KEY = 'data-autoapply-yesno';
  const SUBMIT_WORDS = /^(submit|send|apply|finish|complete)\b/i;
  const clean = text => String(text || '').replace(/\s+/g, ' ').trim();
  const classes = el => String(el?.getAttribute?.('class') || '').split(/\s+/).filter(Boolean);
  // CSS modules hash class names (_active_1e3gg_160); compare the stem only.
  const stem = name => name.replace(/_[a-z0-9]{5}_\d+$/i, '');
  const isOn = el =>
    classes(el).some(name => /^_?(active|selected|checked)$/i.test(stem(name))) ||
    el.getAttribute('aria-pressed') === 'true';

  function isSubmitControl(el) {
    if (!el) return false;
    // Browsers match the type attribute ASCII case-insensitively and untrimmed;
    // a button whose type is missing, padded or invalid submits a form. A button
    // outside a <form> can still submit one through its form attribute or after
    // React moves it, so only an explicit type="button" or "reset" is safe.
    const raw = el.getAttribute('type');
    const type = raw === null ? null : String(raw).replace(/[A-Z]+/g, letters => letters.toLowerCase());
    if (type === 'submit') return true;
    if (el.tagName === 'INPUT' && type === 'image') return true;
    if (el.tagName === 'BUTTON' && type !== 'button' && type !== 'reset') return true;
    if (classes(el).some(name => /submit/i.test(name))) return true;
    return SUBMIT_WORDS.test(clean(el.innerText || el.value));
  }

  const entries = document => {
    const found = [...document.querySelectorAll(ENTRY)];
    return found.filter(entry => !found.some(other => other !== entry && other.contains?.(entry)));
  };
  const headingOf = entry => entry.querySelector('label,legend');
  const titleOf = entry => clean(headingOf(entry)?.innerText).replace(/\s*\*$/, '');
  const requiredOf = entry => classes(headingOf(entry)).some(name => stem(name) === '_required');

  function toggles(document) {
    const used = new Set();
    let serial = 0;
    const groups = [];
    for (const entry of entries(document)) {
      const buttons = [...entry
        .querySelectorAll('button')]
        .filter(
          button =>
            classes(button).some(name => /yesno/i.test(name)) ||
            classes(button.parentElement).some(name => stem(name) === '_yesno'),
        );
      if (!buttons.length) continue;
      const box = buttons[0].parentElement;
      let id = box.getAttribute(KEY);
      if (!id || used.has(id)) {
        do id = `yesno:${++serial}`;
        while (used.has(id) || document.querySelector(`[${KEY}="${id}"]`));
        box.setAttribute(KEY, id);
      }
      used.add(id);
      groups.push({ id, box, entry, buttons });
    }
    return groups;
  }

  const toggleField = group => ({
    id: group.id,
    label: titleOf(group.entry),
    type: 'yesno',
    required: requiredOf(group.entry),
    value: group.buttons.filter(isOn).map(button => clean(button.innerText)).join(','),
    options: group.buttons.map(button => ({ value: clean(button.innerText), label: clean(button.innerText) })),
  });

  // Ashby inputs rarely carry `required`; the heading does. Radio groups are
  // labelled by their entry heading rather than by one option.
  function decorate(document, fields) {
    for (const field of fields) {
      const entry = base.resolve(document, field.id)?.closest(ENTRY);
      if (!entry) continue;
      const title = titleOf(entry);
      const own = clean(field.label).replace(/\s*\*$/, '');
      field.label = title && (field.type === 'radio' || !own || own === field.id) ? title : own;
      if (requiredOf(entry)) field.required = true;
    }
    return fields;
  }

  function resolve(document, key) {
    return toggles(document).find(group => group.id === key)?.box || base.resolve(document, key);
  }

  function scan(document) {
    const current = base.scan(document);
    current.fields = [...decorate(document, current.fields), ...toggles(document).map(toggleField)];
    return current;
  }

  function audit(document) {
    const current = base.audit(document);
    current.fields = [...decorate(document, current.fields), ...toggles(document).map(toggleField)];
    current.required_unknown = current.fields.filter(item => item.required && base.missing(item)).map(item => item.id);
    return current;
  }

  function toggle(group, supplied) {
    if (supplied && typeof supplied === 'object' && supplied.status !== 'confirmed') return 'unconfirmed_answer';
    const value = supplied && typeof supplied === 'object' ? supplied.value : supplied;
    if (value === null || value === undefined) return 'unknown_answer';
    const wanted = value === true ? 'yes' : value === false ? 'no' : clean(value).toLowerCase();
    const choices = group.buttons.filter(button => clean(button.innerText).toLowerCase() === wanted);
    if (choices.length !== 1) return 'option_not_found';
    const [button] = choices;
    if (isSubmitControl(button) || button.disabled) return 'submit_control';
    if (isOn(button)) return null;
    return { reason: 'native_click_required', option: clean(button.innerText) };
  }

  async function fill(document, answers) {
    const groups = new Map(toggles(document).map(group => [group.id, group]));
    const rest = {},
      done = { filled: [], blocked: [] };
    for (const [key, supplied] of Object.entries(answers || {})) {
      const group = groups.get(key);
      if (!group) {
        rest[key] = supplied;
        continue;
      }
      const outcome = toggle(group, supplied);
      if (typeof outcome === 'string') done.blocked.push({ id: key, reason: outcome });
      else if (outcome) done.blocked.push({ id: key, ...outcome });
      else done.filled.push(key);
    }
    const result = await base.fill(document, rest);
    result.filled.push(...done.filled);
    result.blocked.push(...done.blocked);
    return result;
  }

  const api = { ...base, scan, audit, fill, resolve, isSubmitControl };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyAshby = api;
})(globalThis);
