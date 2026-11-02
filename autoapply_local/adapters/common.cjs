'use strict';

// Browser-native discovery/fill primitives. No network, submission or fake upload.
// This file also runs in evaluate_script: evaluate it first, then an ATS wrapper.
(function install(root) {
  const controls =
    'input,textarea,select,[role="combobox"],[role="listbox"],[role="checkbox"],button[aria-haspopup="listbox"]';
  const all = document =>
    [...document.querySelectorAll(controls)].filter(
      el =>
        !el.disabled &&
        (!['hidden', 'submit', 'button', 'reset'].includes(el.type) || el.getAttribute('aria-haspopup') === 'listbox'),
    );
  // Persist bindings on the actual DOM nodes, so a later evaluate_script scan
  // uses the same controls even when native IDs are missing or duplicated.
  const bindingAttribute = 'data-autoapply-binding';
  function bindings(document) {
    const elements = all(document);
    const counts = new Map();
    for (const el of elements) if (el.id) counts.set(el.id, (counts.get(el.id) || 0) + 1);
    const reserved = new Set(elements.flatMap(el => [el.id, el.getAttribute(bindingAttribute)]).filter(Boolean));
    const used = new Set();
    let serial = 0;
    return elements.map(el => {
      let key = el.getAttribute(bindingAttribute);
      if (!key || used.has(key)) {
        key = el.id && counts.get(el.id) === 1 && !used.has(el.id) ? el.id : null;
        if (!key) {
          do key = `autoapply-field-${++serial}`;
          while (reserved.has(key) || used.has(key));
        }
        el.setAttribute(bindingAttribute, key);
      }
      used.add(key);
      return { el, id: key };
    });
  }
  const resolve = (document, key) => bindings(document).find(item => item.id === key)?.el || null;
  const radioContainer = (document, el) => el.closest('fieldset') || el.closest('form') || document.body;
  const radioGroup = (document, bound, el) =>
    bound.filter(
      item =>
        item.el.type === 'radio' &&
        (item.el === el ||
          (el.name && item.el.name === el.name && radioContainer(document, item.el) === radioContainer(document, el))),
    );
  const label = el => {
    const wrapper = el.closest('[data-automation-id^="formField"]');
    const group = el.closest('fieldset');
    return (
      [...(el.labels || [])]
        .map(item => item.innerText || item.textContent)
        .join(' ')
        .trim() ||
      el.getAttribute('aria-label') ||
      wrapper?.querySelector('label')?.innerText?.trim() ||
      group?.querySelector('legend')?.innerText?.trim() ||
      el.getAttribute('placeholder') ||
      el.name ||
      el.id
    );
  };
  const custom = el => !['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
  const field = (el, key) => {
    const record = el.closest('[data-record-index]');
    const wrapper = el.closest('[data-automation-id^="formField"]');
    const chips = [...(wrapper?.querySelectorAll('[data-automation-id="selectedItem"]') || [])]
      .map(item => item.innerText.trim())
      .filter(Boolean);
    const value = chips.length
      ? chips
      : el.type === 'file'
        ? [...(el.files || [])].map(file => file.name).join(', ')
        : ['checkbox', 'radio'].includes(el.type)
          ? Boolean(el.checked)
          : (el.value ?? el.textContent ?? '');
    const info = {
      id: key,
      label: label(el),
      type: custom(el) ? el.getAttribute('role') || 'combobox' : el.type,
      required: Boolean(el.required || el.getAttribute('aria-required') === 'true'),
      value,
      options: [...(el.options || [])].map(option => ({
        value: option.value,
        label: option.label || option.textContent,
      })),
    };
    if (record)
      info.record = {
        kind: record.getAttribute('data-record-kind'),
        index: Number(record.getAttribute('data-record-index')),
      };
    else {
      const match = el.id?.match(/^(workExperience-\d+|education-\d+)--/);
      if (match)
        info.record = { kind: match[1].startsWith('workExperience') ? 'experience' : 'education', id: match[1] };
    }
    return info;
  };
  function scan(document) {
    const form = document.querySelector('form') || document.body;
    const step = document.querySelector('main')?.innerText?.match(/current step\s+(\d+)\s+of\s+(\d+)/i);
    const bound = bindings(document);
    const seen = new Set();
    const fields = [];
    for (const { el, id } of bound) {
      if (seen.has(el)) continue;
      const info = field(el, id);
      if (el.type === 'radio') {
        const container = radioContainer(document, el);
        const group = radioGroup(document, bound, el);
        for (const item of group) seen.add(item.el);
        const selected = group.filter(item => item.el.checked);
        info.label =
          container?.querySelector('legend')?.innerText?.trim() || container?.getAttribute('aria-label') || info.label;
        info.required = group.some(item => item.el.required || item.el.getAttribute('aria-required') === 'true');
        info.options = group.map(item => ({ value: item.el.value, label: label(item.el) }));
        info.value = selected.length === 1 ? selected[0].el.value : null;
      }
      fields.push(info);
    }
    return {
      fields,
      page: {
        index: Number(step?.[1] || form?.getAttribute('data-page-index') || 1),
        total: Number(step?.[2] || form?.getAttribute('data-page-total') || 1),
        title: form?.getAttribute('data-page-title') || document.title || '',
      },
    };
  }
  function nativeSet(document, el, property, value) {
    const view = document.defaultView;
    const constructor =
      el.tagName === 'TEXTAREA'
        ? view.HTMLTextAreaElement
        : el.tagName === 'SELECT'
          ? view.HTMLSelectElement
          : view.HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(constructor.prototype, property)?.set;
    if (setter) setter.call(el, value);
    else if (property === 'checked') el.checked = value;
    else throw new Error('Native setter unavailable; use MCP native interaction');
    for (const type of ['input', 'change']) el.dispatchEvent(new view.Event(type, { bubbles: true }));
  }
  async function fill(document, answers) {
    const result = { filled: [], blocked: [], uploads: [] };
    const bound = bindings(document);
    const lookup = new Map(bound.map(item => [item.id, item.el]));
    const scanned = new Map(scan(document).fields.map(item => [item.id, item]));
    for (const [key, supplied] of Object.entries(answers)) {
      const el = lookup.get(key);
      const block = reason => result.blocked.push({ id: key, reason });
      if (!el) {
        block('field_not_found');
        continue;
      }
      if (supplied && typeof supplied === 'object' && supplied.status !== 'confirmed') {
        block('unconfirmed_answer');
        continue;
      }
      const value = supplied && typeof supplied === 'object' ? supplied.value : supplied;
      if (value === null || value === undefined) {
        block('unknown_answer');
        continue;
      }
      if (custom(el)) {
        block('agent_interaction_required');
        continue;
      }
      if (el.type === 'radio') {
        const info = scanned.get(key);
        if (!info || info.type !== 'radio') {
          block('field_not_found');
          continue;
        }
        const choices = info.options.filter(option => option.value === value || option.label === value);
        if (choices.length !== 1) {
          block('option_not_found');
          continue;
        }
        const group = radioGroup(document, bound, el);
        if (group.filter(item => item.el.value === choices[0].value).length !== 1) {
          block('ambiguous_option');
          continue;
        }
        for (const item of group) nativeSet(document, item.el, 'checked', item.el.value === choices[0].value);
        result.filled.push(key);
        continue;
      }
      if (el.type === 'file') {
        result.uploads.push({ id: key, path: String(value) });
        continue;
      }
      if (el.tagName === 'SELECT' && ![...el.options].some(option => option.value === String(value))) {
        block('option_not_found');
        continue;
      }
      if (el.maxLength >= 0 && String(value).length > el.maxLength && !['checkbox', 'radio'].includes(el.type)) {
        block('maxlength_exceeded');
        continue;
      }
      if (['checkbox', 'radio'].includes(el.type)) {
        if (typeof value !== 'boolean') {
          block('boolean_required');
          continue;
        }
        nativeSet(document, el, 'checked', value);
      } else nativeSet(document, el, 'value', String(value));
      result.filled.push(key);
    }
    return result;
  }
  function audit(document) {
    const current = scan(document);
    current.text = document.body?.innerText || '';
    // Ordinary definition lists provide explicit observed label/value pairs;
    // arbitrary page prose is never interpreted as expected application data.
    for (const term of document.querySelectorAll('dt')) {
      const siblings = [...(term.parentElement?.children || [])];
      const valueNode = siblings[siblings.indexOf(term) + 1];
      if (valueNode?.tagName !== 'DD') continue;
      const question = term.innerText.trim(),
        value = valueNode.innerText.trim();
      if (!question || !value) continue;
      const start = current.text.indexOf(question);
      const valueStart = current.text.indexOf(value, start + question.length);
      if (start < 0 || valueStart < 0) continue;
      const end = valueStart + value.length;
      current.fields.push({
        id: `summary-${current.fields.length}`,
        label: question,
        value,
        type: 'summary',
        required: true,
        observed: true,
        evidence: { quote: current.text.slice(start, end), start, end },
      });
    }
    current.required_unknown = current.fields
      .filter(
        item =>
          item.required &&
          (item.value === '' ||
            item.value === null ||
            item.value === undefined ||
            (Array.isArray(item.value) && !item.value.length) ||
            (typeof item.value === 'string' && /^select (one|an? |country)/i.test(item.value.trim())) ||
            (item.type === 'checkbox' && item.value !== true)),
      )
      .map(item => item.id);
    current.errors = [
      ...document.querySelectorAll('[role="alert"],[aria-invalid="true"],[data-automation-id="errorMessage"]'),
    ].map(item => item.validationMessage || item.innerText || item.getAttribute('aria-label') || 'Invalid field');
    return current;
  }
  const api = { scan, fill, audit, resolve };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyDOM = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
