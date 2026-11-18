'use strict';

// Rippling (ats.rippling.com) adapter. Load common.cjs first.
// Rippling forms are React + Formsy: every field is a fiber whose props carry
// name/setValue/required/isValid; a custom question's text sits on a parent
// fiber (`question`) and a choice lists its options in `list`. The model is the
// truth, so this adapter reads and writes it through setValue. Pages without
// that model fall back to the common DOM primitives. Nothing here clicks a
// button: the user submits each held form by hand.
(function install(root) {
  const base = typeof module !== 'undefined' && module.exports ? require('./common.cjs') : root.AutoApplyDOM;
  if (!base) throw new Error('Load common.cjs before the ATS adapter');

  const HIDDEN = /^(externalPlaceId|aiOptOut|phone_number-code)$/;
  const FILES = /^(resume|cover_letter)$/;
  const fiberKey = el => (el ? Object.keys(el).find(key => key.startsWith('__reactFiber')) : null);

  function formsy(document) {
    const form = document.querySelector('form');
    const key = fiberKey(form);
    if (!key) return null;
    let top = form[key];
    while (top.return) top = top.return;
    const findModel = (node, depth) => {
      if (!node || depth > 400) return null;
      if (node.stateNode && typeof node.stateNode.getModel === 'function') return node.stateNode;
      for (let child = node.child; child; child = child.sibling) {
        const found = findModel(child, depth + 1);
        if (found) return found;
      }
      return null;
    };
    const owner = findModel(top, 0);
    if (!owner) return null;
    const fields = new Map();
    const walk = (node, depth) => {
      if (!node || depth > 400) return;
      const props = node.memoizedProps;
      if (props && typeof props.name === 'string' && typeof props.setValue === 'function' && !fields.has(props.name))
        fields.set(props.name, node);
      for (let child = node.child; child; child = child.sibling) walk(child, depth + 1);
    };
    walk(top, 0);
    return { owner, fields, model: owner.getModel() || {} };
  }

  const questionOf = node => {
    for (let up = node.return, depth = 0; up && depth < 10; depth++, up = up.return)
      if (up.memoizedProps?.question) return up.memoizedProps.question;
    return null;
  };
  const optionsOf = node => {
    const search = (item, depth) => {
      if (!item || depth > 12) return null;
      const props = item.memoizedProps;
      if (Array.isArray(props?.list) && props.list.length) return props.list;
      if (Array.isArray(props?.options) && props.options[0]?.label) return props.options;
      for (let child = item.child; child; child = child.sibling) {
        const found = search(child, depth + 1);
        if (found) return found;
      }
      return null;
    };
    return search(node, 0);
  };
  const inputOf = node => {
    const search = item => {
      if (!item) return null;
      const el = item.stateNode;
      if (el && typeof el.matches === 'function') return el.matches('input') ? el : el.querySelector('input');
      for (let child = item.child; child; child = child.sibling) {
        const found = search(child);
        if (found) return found;
      }
      return null;
    };
    return search(node);
  };
  const valueAt = (model, name) => {
    let value = model;
    for (const part of name.split('.')) {
      if (value === undefined || value === null) return undefined;
      value = value[part];
    }
    return value;
  };
  const empty = value =>
    value === undefined || value === null || value === '' || (Array.isArray(value) && !value.length);
  const norm = text =>
    String(text ?? '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();

  function describe(name, node, model) {
    const props = node.memoizedProps;
    const question = questionOf(node);
    const list = optionsOf(node);
    // eeoc.* fields carry no label on real forms: eeoc.veteranStatus reads "veteran status".
    const plain = name.startsWith('eeoc.')
      ? name
          .slice(5)
          .replace(/([A-Z])/g, ' $1')
          .toLowerCase()
          .trim()
      : name;
    const raw = valueAt(model, name);
    const file = FILES.test(name);
    // File entries hold signed links to the user's document; only names leave the page.
    const value = file
      ? (Array.isArray(raw) ? raw : []).map(item => item?.name).filter(Boolean)
      : Array.isArray(raw) || typeof raw !== 'object'
        ? (raw ?? '')
        : JSON.stringify(raw);
    return {
      id: name,
      label: question?.title || props.label || plain,
      type: file ? 'file' : list ? 'choice' : 'text',
      required: Boolean(question ? question.isRequired : props.required),
      value,
      options: (list || []).map(option => ({ value: option.value, label: option.label })),
      valid: props.isValid !== false,
    };
  }

  // Readbacks keep the MCP contracts in browser.py EVALUATE_SCHEMAS: scans are
  // audit-shaped (model status in `page`), every other action is fill-shaped.
  const readback = (filled = [], blocked = []) => ({ filled, blocked, uploads: [] });

  function modelScan(document) {
    const state = formsy(document);
    const fields = [];
    if (state)
      for (const [name, node] of state.fields) if (!HIDDEN.test(name)) fields.push(describe(name, node, state.model));
    return {
      fields,
      errors: [],
      text: '',
      url: String(document.location?.href || root.location?.href || ''),
      attachments: [],
      page: {
        index: 1,
        total: 1,
        title: String(document.title || ''),
        formsy: Boolean(state),
        valid: Boolean(state?.owner.state?.isValid),
      },
      required_unknown: [],
    };
  }

  // Ready once the upload finished (progress absent or 100), Rippling stored the
  // file (url) and its parse wrote the first name. With `name`, the stored file
  // must be that upload, not one left from an earlier attempt.
  function modelReady(document, { name } = {}) {
    const state = formsy(document);
    if (!state) return readback([], [{ id: 'resume', reason: 'form_model_missing' }]);
    const resume = Array.isArray(state.model.resume) ? state.model.resume[0] : null;
    const uploaded = Boolean(resume?.url) && (resume.progress === undefined || resume.progress === 100);
    const ours = name === undefined || resume?.name === name;
    if (uploaded && ours && !empty(state.model.first_name)) return readback(['resume']);
    return readback([], [{ id: 'resume', reason: 'resume_parse_pending' }]);
  }

  // The exact model value a confirmed answer stands for, or why it has none.
  function planWrite(state, name, supplied) {
    const node = state?.fields.get(name);
    if (!node || HIDDEN.test(name)) return { reason: 'field_not_found' };
    if (!supplied || typeof supplied !== 'object' || supplied.status !== 'confirmed')
      return { reason: 'unconfirmed_answer' };
    if (supplied.value === null || supplied.value === undefined || supplied.value === '')
      return { reason: 'unknown_answer' };
    if (FILES.test(name)) return { reason: 'upload_requires_native_file' };
    if (name === 'location') return { reason: 'location_requires_suggestion' };
    const list = optionsOf(node);
    if (!list) return { node, value: String(supplied.value) };
    // A checkbox group holds an array; several ticked options are the user's, not ours.
    const current = valueAt(state.model, name);
    const multi = Array.isArray(current);
    if (multi && current.length > 1) return { reason: 'multi_value' };
    const wanted = norm(supplied.value);
    const hits = list.filter(option => option.value === supplied.value || norm(option.label) === wanted);
    if (hits.length !== 1) return { reason: hits.length ? 'ambiguous_option' : 'option_not_found' };
    return { node, value: multi ? [hits[0].value] : hits[0].value };
  }

  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  async function modelFill(document, answers) {
    const result = readback();
    const state = formsy(document);
    for (const [name, supplied] of Object.entries(answers || {})) {
      const write = planWrite(state, name, supplied);
      if (write.reason) {
        result.blocked.push({ id: name, reason: write.reason });
        continue;
      }
      write.node.memoizedProps.setValue(write.value);
      result.filled.push(name);
    }
    return result;
  }

  // Reads back, never writes: each confirmed answer still holds its exact value,
  // or `value_changed` when something (a late resume parse) wrote over it.
  async function modelCheck(document, answers) {
    const result = readback();
    const state = formsy(document);
    for (const [name, supplied] of Object.entries(answers || {})) {
      const write = planWrite(state, name, supplied);
      if (write.reason) result.blocked.push({ id: name, reason: write.reason });
      else if (same(valueAt(state.model, name), write.value)) result.filled.push(name);
      else result.blocked.push({ id: name, reason: 'value_changed' });
    }
    return result;
  }

  async function fill(document, answers) {
    const state = formsy(document);
    if (!state) return base.fill(document, answers);
    const model = {},
      dom = {};
    for (const [key, value] of Object.entries(answers || {})) (state.fields.has(key) ? model : dom)[key] = value;
    const own = await modelFill(document, model);
    if (!Object.keys(dom).length) return own;
    const other = await base.fill(document, dom);
    return {
      filled: [...own.filled, ...other.filled],
      blocked: [...own.blocked, ...other.blocked],
      uploads: [...own.uploads, ...other.uploads],
    };
  }

  // A click activates the nearest ancestor with activation behaviour (a submit
  // button, link, label or summary), and React runs every ancestor's onClick.
  // Any such control from the suggestion up to its form blocks the pick.
  const ACTIVATES = new Set(['BUTTON', 'INPUT', 'A', 'AREA', 'LABEL', 'SUMMARY', 'SELECT', 'TEXTAREA']);
  const unsafe = pick => {
    for (let el = pick, depth = 0; el && depth < 50; depth++, el = el.parentElement) {
      if (el.tagName === 'FORM' || el.tagName === 'BODY') return false;
      if (ACTIVATES.has(el.tagName) || el.isContentEditable) return true;
      const attr = key => String(el.getAttribute?.(key) || '');
      if (/^(button|link|menuitem|checkbox|radio|switch|tab)$/i.test(attr('role'))) return true;
      if (/submit/i.test([attr('type'), attr('name'), attr('id'), attr('data-testid')].join(' '))) return true;
    }
    return false;
  };

  // Google Places autocomplete: the form wants the picked text and its place id.
  // Type the confirmed "City, ST" query, then pick the only suggestion that
  // starts with it; none, several or an unsafe control blocks for the user.
  async function location(document, options = {}) {
    const result = await placeLocation(document, options);
    return result.blocked ? readback([], [{ id: 'location', reason: result.blocked }]) : readback(['location']);
  }

  async function placeLocation(document, { query, tries = 60, waitMs = 50 } = {}) {
    const state = formsy(document);
    const node = state?.fields.get('location');
    if (!node) return { blocked: 'form_model_missing' };
    if (!query) return { blocked: 'no_query' };
    const input = inputOf(node);
    if (!input) return { blocked: 'agent_interaction_required' };
    const wait = () => new Promise(resolve => setTimeout(resolve, waitMs));
    const view = document.defaultView || root;
    // A place id from an earlier pick must never pass for this one: clear it
    // first, and remember it in case the page refuses the clear or restores it.
    const stale = state.owner.getModel()?.externalPlaceId;
    state.fields.get('externalPlaceId')?.memoizedProps.setValue('');
    const setter =
      view.HTMLInputElement && Object.getOwnPropertyDescriptor(view.HTMLInputElement.prototype, 'value')?.set;
    input.focus?.();
    if (setter) setter.call(input, query);
    else input.value = query;
    input.dispatchEvent(new view.Event('input', { bubbles: true }));
    const wanted = norm(query);
    let matches = [];
    for (let attempt = 0; attempt < tries; attempt++) {
      matches = [...document.querySelectorAll('[role=option],[role=listbox] li')].filter(el =>
        norm(el.innerText).startsWith(wanted),
      );
      if (matches.length) break;
      await wait();
    }
    if (!matches.length) return { blocked: 'no_suggestion' };
    if (matches.length > 1) return { blocked: 'ambiguous_suggestion' };
    const pick = matches[0];
    if (unsafe(pick)) return { blocked: 'unsafe_control' };
    // Ids seen before the pick (the earlier fill's, or one the page set while
    // typing) can never prove this pick; the same place picked again blocks.
    const seen = new Set([stale, state.owner.getModel()?.externalPlaceId].filter(id => !empty(id)));
    // Placed = a place id set by the pick and the location text of the suggestion.
    const placed = () => {
      const model = state.owner.getModel() || {};
      const id = model.externalPlaceId;
      return !empty(id) && !seen.has(id) && norm(model.location).startsWith(wanted);
    };
    const settle = async rounds => {
      for (let attempt = 0; attempt < rounds && !placed(); attempt++) await wait();
      return placed();
    };
    const Mouse = view.MouseEvent || view.Event;
    // Places lists select on mousedown; a click is sent only when that did nothing.
    for (const type of ['mousedown', 'mouseup']) pick.dispatchEvent(new Mouse(type, { bubbles: true }));
    if (await settle(Math.min(tries, 5))) return { placed: true };
    pick.dispatchEvent(new Mouse('click', { bubbles: true }));
    if (await settle(tries)) return { placed: true };
    return { blocked: 'place_not_selected' };
  }

  // The common audit readback, unchanged in shape; model-only problems become
  // error strings because required_unknown may only name scanned DOM fields.
  function audit(document) {
    const current = base.audit(document);
    const scanned = modelScan(document);
    if (!scanned.page.formsy) return current;
    for (const field of scanned.fields) {
      if (!field.valid) current.errors.push('Invalid field: ' + field.id);
      if (field.required && empty(field.value)) current.errors.push('Required field empty: ' + field.id);
    }
    return current;
  }

  const api = { ...base, scan: base.scan, fill, audit, modelScan, modelReady, modelFill, modelCheck, location };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyRippling = api;
})(globalThis);
