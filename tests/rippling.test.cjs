'use strict';

// Rippling adapter on a synthetic Formsy form. Rippling application forms are
// React + Formsy: each field is a fiber whose props carry name/setValue, a
// custom question's text sits on a parent fiber (`question`), and a choice
// field lists its options in `list`. Everything below is invented; no live
// page, network or browser is used.

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const rippling = require(path.resolve(__dirname, '..', 'autoapply_local', 'adapters', 'rippling.cjs'));

class FakeEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.bubbles = Boolean(options.bubbles);
  }
}

class Node {
  constructor(tag, attributes = {}, text = '') {
    this.tagName = tag.toUpperCase();
    this.attributes = { ...attributes };
    this.text = text;
    this.events = [];
    this.clicks = 0;
    this.value = '';
    this.listeners = {};
    this.parentElement = null;
  }
  get type() {
    return this.attributes.type || this.tagName.toLowerCase();
  }
  get innerText() {
    return this.text;
  }
  getAttribute(name) {
    return this.attributes[name] ?? null;
  }
  matches(selector) {
    return selector.split(',').some(part => {
      const trimmed = part.trim();
      if (trimmed === 'input') return this.tagName === 'INPUT';
      const role = trimmed.match(/^\[role=["']?(\w+)["']?\]$/);
      return Boolean(role && this.attributes.role === role[1]);
    });
  }
  querySelector() {
    return null;
  }
  focus() {
    this.focused = true;
  }
  click() {
    this.clicks += 1;
    this.dispatchEvent(new FakeEvent('click'));
  }
  dispatchEvent(event) {
    this.events.push(event.type);
    this.listeners[event.type]?.(event);
    return true;
  }
}

// Puts `child` under `parent` in the synthetic DOM tree and returns the child.
function nest(parent, child) {
  child.parentElement = parent;
  return child;
}

function setPath(model, name, value) {
  const parts = name.split('.');
  let target = model;
  for (const part of parts.slice(0, -1)) target = target[part] ??= {};
  target[parts.at(-1)] = value;
}

function fiber(props, children = [], stateNode = null) {
  const node = { memoizedProps: props, stateNode, child: null, sibling: null, return: null };
  children.forEach((child, index) => {
    child.return = node;
    if (index === 0) node.child = child;
    else children[index - 1].sibling = child;
  });
  return node;
}

// Builds the synthetic form. `overrides` changes one field's props by name.
function formsyForm({ model = {}, valid = false, options = [], invalid = [] } = {}) {
  const writes = [];
  const formsy = { state: { isValid: valid }, getModel: () => model };
  const field = (name, props = {}, children = []) =>
    fiber(
      {
        name,
        required: false,
        isValid: !invalid.includes(name),
        setValue(value) {
          writes.push([name, value]);
          setPath(model, name, value);
        },
        ...props,
      },
      children,
    );
  const locationInput = new Node('input', { name: 'location', type: 'text' });
  const fields = [
    field('first_name', { label: 'First name', required: true }),
    field('last_name', { label: 'Last name', required: true }),
    field('email', { label: 'Email', required: true }),
    field('phone_number', { label: 'Phone number', required: true }),
    field('linkedin_link', { label: 'LinkedIn' }),
    field('resume', { label: 'Resume', required: true }),
    field('location', { label: 'Location', required: true }, [fiber({}, [], locationInput)]),
    field('externalPlaceId', {}),
    fiber({ question: { title: 'How did you hear about us?', isRequired: false } }, [
      field('customQuestions.aaa111.answer', {}),
    ]),
    fiber({ question: { title: 'Will you need visa sponsorship?', isRequired: true } }, [
      field('customQuestions.bbb222.answer', {
        list: [
          { value: 'opt-yes', label: 'Yes' },
          { value: 'opt-no', label: 'No' },
        ],
      }),
    ]),
    fiber({ question: { title: 'Which teams interest you?', isRequired: false } }, [
      field('customQuestions.ccc333.answer', {
        list: [
          { value: 'team-a', label: 'Platform' },
          { value: 'team-b', label: 'Data' },
        ],
      }),
    ]),
    field('eeoc.veteranStatus', {
      list: [
        { value: 'v1', label: 'I am not a protected veteran' },
        { value: 'v2', label: 'I decline to self-identify' },
      ],
    }),
  ];
  const root = fiber({}, [fiber({}, fields, formsy)]);
  const form = new Node('form');
  form.__reactFiber$synthetic = root.child.child;
  const submit = new Node('button', { type: 'submit' }, 'Apply');
  const document = {
    title: 'Example Labs application',
    body: { innerText: 'Apply to Example Labs' },
    defaultView: { Event: FakeEvent, MouseEvent: FakeEvent },
    options,
    querySelector(selector) {
      if (selector === 'form') return form;
      if (/submit/.test(selector)) return submit;
      return null;
    },
    querySelectorAll(selector) {
      if (/option/.test(selector)) return this.options;
      if (/submit/.test(selector)) return [submit];
      return [];
    },
  };
  return { document, model, writes, submit, locationInput, formsy };
}

const confirmed = value => ({ value, status: 'confirmed' });

// The MCP readback contracts every adapter action must fit (EVALUATE_SCHEMAS in
// autoapply_local/browser.py; additionalProperties is false for both).
const SCHEMA = {
  audit: { keys: ['fields', 'errors', 'text', 'url', 'attachments', 'page', 'required_unknown'], required: ['fields', 'errors', 'text', 'url', 'attachments'] },
  fill: { keys: ['filled', 'blocked', 'uploads'], required: ['filled', 'blocked', 'uploads'] },
};
function assertShape(operation, value) {
  const { keys, required } = SCHEMA[operation];
  for (const key of Object.keys(value)) assert.ok(keys.includes(key), `${operation} readback has no ${key} property`);
  for (const key of required) assert.ok(key in value, `${operation} readback needs ${key}`);
  for (const key of ['fields', 'errors', 'attachments', 'required_unknown', 'filled', 'blocked', 'uploads'])
    if (key in value) assert.ok(Array.isArray(value[key]), key);
  for (const key of ['errors', 'required_unknown', 'filled'])
    if (key in value) assert.ok(value[key].every(item => typeof item === 'string'), key);
  for (const key of ['text', 'url']) if (key in value) assert.equal(typeof value[key], 'string', key);
}
const reasonOf = result => result.blocked.map(item => item.reason).join(',');

test('rippling: model scan reads names, question labels, required flags and options', () => {
  const { document } = formsyForm({ model: { first_name: 'Parsed', resume: [{ name: 'alex.pdf', url: 'https://signed.example/x', progress: 100 }] } });
  const scanned = rippling.modelScan(document);
  assertShape('audit', scanned);
  assert.equal(scanned.page.formsy, true);
  const byId = Object.fromEntries(scanned.fields.map(field => [field.id, field]));
  assert.equal(byId.first_name.label, 'First name');
  assert.equal(byId.first_name.required, true);
  assert.equal(byId.first_name.value, 'Parsed');
  assert.equal(byId['customQuestions.bbb222.answer'].label, 'Will you need visa sponsorship?');
  assert.equal(byId['customQuestions.bbb222.answer'].required, true);
  assert.equal(byId['customQuestions.bbb222.answer'].type, 'choice');
  assert.deepEqual(
    byId['customQuestions.bbb222.answer'].options,
    [
      { value: 'opt-yes', label: 'Yes' },
      { value: 'opt-no', label: 'No' },
    ],
  );
  assert.equal(byId['eeoc.veteranStatus'].label, 'veteran status');
  assert.equal(byId.resume.type, 'file');
  assert.deepEqual(byId.resume.value, ['alex.pdf']);
  assert.ok(!JSON.stringify(scanned).includes('signed.example'), 'signed file links never leave the page');
  assert.ok(!byId.externalPlaceId, 'hidden place id is not a question');
});

test('rippling: model scan reports no form model on a plain page', () => {
  const document = { querySelector: () => null, querySelectorAll: () => [], body: { innerText: '' } };
  const scanned = rippling.modelScan(document);
  assertShape('audit', scanned);
  assert.equal(scanned.page.formsy, false);
  assert.deepEqual(scanned.fields, []);
});

test('rippling: modelReady waits for the finished upload and the parsed first name', () => {
  const ready = model => {
    const result = rippling.modelReady(formsyForm({ model }).document);
    assertShape('fill', result);
    return result.filled.includes('resume');
  };
  const done = { name: 'a.pdf', url: 'u', progress: 100 };
  assert.equal(ready({ first_name: 'Alex', resume: [{ ...done, progress: 40 }] }), false, 'upload still in progress');
  assert.equal(ready({ first_name: 'Alex', resume: [{ ...done, progress: 0 }] }), false, 'upload not started');
  assert.equal(ready({ first_name: 'Alex', resume: [{ ...done, url: '' }] }), false, 'no stored file yet');
  assert.equal(ready({ resume: [done] }), false, 'parse has not written the name');
  assert.equal(ready({ first_name: 'Alex', resume: [] }), false);
  assert.equal(ready({ first_name: 'Alex', resume: [done] }), true);
  assert.equal(ready({ first_name: 'Alex', resume: [{ name: 'a.pdf', url: 'u' }] }), true, 'no progress field');
  const pending = rippling.modelReady(formsyForm({ model: {} }).document);
  assert.deepEqual(pending.blocked, [{ id: 'resume', reason: 'resume_parse_pending' }]);
  const plain = { querySelector: () => null, querySelectorAll: () => [], body: { innerText: '' } };
  assert.equal(reasonOf(rippling.modelReady(plain)), 'form_model_missing');
});

test('rippling: modelFill writes confirmed text and maps choice labels to option values', async () => {
  const state = formsyForm({ model: { linkedin_link: 'https://parsed.example/wrong', 'customQuestions': { ccc333: { answer: ['team-a', 'team-b'] } } } });
  const result = await rippling.modelFill(state.document, {
    first_name: confirmed('Alex'),
    linkedin_link: confirmed('https://www.linkedin.com/in/alex-rivera-example'),
    'customQuestions.bbb222.answer': confirmed('No'),
    'customQuestions.aaa111.answer': { value: 'A friend', status: 'unknown' },
    last_name: confirmed(null),
    email: 'plain-string',
    'eeoc.veteranStatus': confirmed('Maybe'),
    'customQuestions.ccc333.answer': confirmed('Data'),
    location: confirmed('Example City, CA'),
    resume: confirmed('/tmp/alex.pdf'),
    'missing.field': confirmed('x'),
  });
  assert.deepEqual(result.filled.sort(), ['customQuestions.bbb222.answer', 'first_name', 'linkedin_link']);
  const reasons = Object.fromEntries(result.blocked.map(item => [item.id, item.reason]));
  assert.deepEqual(reasons, {
    'customQuestions.aaa111.answer': 'unconfirmed_answer',
    last_name: 'unknown_answer',
    email: 'unconfirmed_answer',
    'eeoc.veteranStatus': 'option_not_found',
    'customQuestions.ccc333.answer': 'multi_value',
    location: 'location_requires_suggestion',
    resume: 'upload_requires_native_file',
    'missing.field': 'field_not_found',
  });
  assert.equal(state.model.first_name, 'Alex');
  assert.equal(state.model.linkedin_link, 'https://www.linkedin.com/in/alex-rivera-example');
  assert.equal(state.model.customQuestions.bbb222.answer, 'opt-no');
  assert.equal(state.submit.clicks, 0);
});

test('rippling: a one-element checkbox group keeps its array shape', async () => {
  const state = formsyForm({ model: { customQuestions: { ccc333: { answer: [] } } } });
  const result = await rippling.modelFill(state.document, { 'customQuestions.ccc333.answer': confirmed('Platform') });
  assert.deepEqual(result.filled, ['customQuestions.ccc333.answer']);
  assert.deepEqual(state.model.customQuestions.ccc333.answer, ['team-a']);
});

test('rippling: fill dispatches model names to the model and keeps DOM fallback', async () => {
  const state = formsyForm();
  const result = await rippling.fill(state.document, { first_name: confirmed('Alex') });
  assert.deepEqual(result.filled, ['first_name']);
  assert.equal(state.model.first_name, 'Alex');
});

function suggestion(text, model, place) {
  const option = new Node('li', { role: 'option' }, text);
  option.listeners.click = () => {
    model.location = text;
    model.externalPlaceId = place;
  };
  return option;
}

test('rippling: location types the query and picks the one matching suggestion', async () => {
  const state = formsyForm();
  const wanted = suggestion('Example City, CA, USA', state.model, 'place-1');
  const other = suggestion('Example Town, CA, USA', state.model, 'place-2');
  state.locationInput.listeners.input = () => {
    state.document.options = [other, wanted];
  };
  const result = await rippling.location(state.document, { query: 'Example City, CA', tries: 3, waitMs: 1 });
  assert.equal(state.locationInput.value, 'Example City, CA');
  assert.deepEqual(wanted.events, ['mousedown', 'mouseup', 'click']);
  assert.equal(other.clicks + other.events.length, 0);
  assertShape('fill', result);
  assert.deepEqual(result, { filled: ['location'], blocked: [], uploads: [] });
  assert.equal(state.model.externalPlaceId, 'place-1');
  assert.equal(state.submit.clicks, 0);
});

test('rippling: location blocks on no, ambiguous or unsafe suggestions', async () => {
  for (const [texts, reason] of [
    [[], 'no_suggestion'],
    [['Example City, CA, USA', 'Example City, CA, United States'], 'ambiguous_suggestion'],
  ]) {
    const state = formsyForm();
    state.locationInput.listeners.input = () => {
      state.document.options = texts.map(text => suggestion(text, state.model, 'p'));
    };
    const result = await rippling.location(state.document, { query: 'Example City, CA', tries: 2, waitMs: 1 });
    assertShape('fill', result);
    assert.equal(reasonOf(result), reason);
    assert.ok(!state.model.externalPlaceId, 'no place id');
  }
  const state = formsyForm();
  const trap = new Node('button', { role: 'option', type: 'submit' }, 'Example City, CA, USA');
  state.locationInput.listeners.input = () => {
    state.document.options = [trap];
  };
  const result = await rippling.location(state.document, { query: 'Example City, CA', tries: 2, waitMs: 1 });
  assert.equal(reasonOf(result), 'unsafe_control');
  assert.equal(trap.events.length, 0);
});

test('rippling: location without a form field or query is blocked, not guessed', async () => {
  const document = { querySelector: () => null, querySelectorAll: () => [], body: { innerText: '' } };
  assert.equal(reasonOf(await rippling.location(document, { query: 'Example City, CA' })), 'form_model_missing');
  const state = formsyForm();
  assert.equal(reasonOf(await rippling.location(state.document, { query: '' })), 'no_query');
});

test('rippling: audit keeps the common readback schema and reports model problems as errors', () => {
  const state = formsyForm({ valid: false, invalid: ['phone_number'] });
  const audit = rippling.audit(state.document);
  // browser.py adds url and attachments after audit(); the keys must match the common audit.
  const common = require(path.resolve(__dirname, '..', 'autoapply_local', 'adapters', 'common.cjs'));
  assert.deepEqual(Object.keys(audit).sort(), Object.keys(common.audit(state.document)).sort());
  assertShape('audit', { ...audit, url: '', attachments: [] });
  assert.ok(audit.errors.includes('Invalid field: phone_number'));
  assert.ok(audit.errors.includes('Required field empty: first_name'));
  const ids = new Set(audit.fields.map(field => field.id));
  assert.ok(audit.required_unknown.every(id => ids.has(id)), 'required_unknown names only scanned fields');
  const filled = formsyForm({
    valid: true,
    model: { first_name: 'A', last_name: 'R', email: 'e', phone_number: 'p', location: 'L', resume: [{ name: 'a.pdf' }], customQuestions: { bbb222: { answer: 'opt-no' } } },
  });
  assert.deepEqual(rippling.audit(filled.document).errors, []);
});

test('rippling: every fill-side action returns an MCP fill readback', async () => {
  const state = formsyForm();
  assertShape('fill', await rippling.modelFill(state.document, { first_name: confirmed('Alex') }));
  assertShape('fill', await rippling.fill(state.document, { first_name: confirmed('Alex') }));
});

test('rippling: the adapter exposes no submit or navigation action', () => {
  for (const name of Object.keys(rippling)) assert.doesNotMatch(name, /submit|navigate|apply/i);
});

test('rippling: modelReady waits for the resume the driver uploaded, not an earlier one', () => {
  const model = { first_name: 'Alex', resume: [{ name: 'old.pdf', url: 'u', progress: 100 }] };
  const stale = rippling.modelReady(formsyForm({ model }).document, { name: 'alex.pdf' });
  assertShape('fill', stale);
  assert.equal(reasonOf(stale), 'resume_parse_pending');
  model.resume = [{ name: 'alex.pdf', url: 'u', progress: 100 }];
  assert.deepEqual(rippling.modelReady(formsyForm({ model }).document, { name: 'alex.pdf' }).filled, ['resume']);
});

test('rippling: modelCheck reports confirmed values the parse overwrote', async () => {
  const state = formsyForm({ model: { customQuestions: { ccc333: { answer: [] } } } });
  const answers = {
    first_name: confirmed('Alex'),
    linkedin_link: confirmed('https://www.linkedin.com/in/alex-rivera-example'),
    'customQuestions.bbb222.answer': confirmed('No'),
    'customQuestions.ccc333.answer': confirmed('Platform'),
  };
  await rippling.modelFill(state.document, answers);
  const clean = await rippling.modelCheck(state.document, answers);
  assertShape('fill', clean);
  assert.deepEqual(clean.filled.sort(), Object.keys(answers).sort());
  assert.deepEqual(clean.blocked, []);
  // A late parse writes what it read from the PDF on top of the user's facts.
  state.model.linkedin_link = 'https://parsed.example/wrong';
  state.model.customQuestions.bbb222.answer = 'opt-yes';
  const writes = state.writes.length;
  const dirty = await rippling.modelCheck(state.document, answers);
  assertShape('fill', dirty);
  assert.deepEqual(dirty.filled.sort(), ['customQuestions.ccc333.answer', 'first_name']);
  assert.deepEqual(
    dirty.blocked.sort((a, b) => a.id.localeCompare(b.id)),
    [
      { id: 'customQuestions.bbb222.answer', reason: 'value_changed' },
      { id: 'linkedin_link', reason: 'value_changed' },
    ],
  );
  assert.equal(state.writes.length, writes, 'checking never writes');
});

test('rippling: location never clicks a suggestion inside a control that activates', async () => {
  for (const tag of ['button', 'a', 'label', 'summary']) {
    const state = formsyForm();
    const control = new Node(tag, tag === 'button' ? { type: 'submit' } : {}, 'Apply');
    const wrapper = nest(control, new Node('div'));
    const trap = nest(wrapper, suggestion('Example City, CA, USA', state.model, 'place-1'));
    state.locationInput.listeners.input = () => {
      state.document.options = [trap];
    };
    const result = await rippling.location(state.document, { query: 'Example City, CA', tries: 2, waitMs: 1 });
    assert.equal(reasonOf(result), 'unsafe_control', tag);
    assert.deepEqual(trap.events, [], tag);
    assert.ok(!state.model.externalPlaceId, tag);
  }
  const state = formsyForm();
  const listbox = new Node('ul', { role: 'listbox' });
  const safe = nest(listbox, suggestion('Example City, CA, USA', state.model, 'place-1'));
  state.locationInput.listeners.input = () => {
    state.document.options = [safe];
  };
  const result = await rippling.location(state.document, { query: 'Example City, CA', tries: 2, waitMs: 1 });
  assert.deepEqual(result.filled, ['location']);
});

test('rippling: a suggestion picked on mousedown gets no click at all', async () => {
  const state = formsyForm();
  const option = new Node('li', { role: 'option' }, 'Example City, CA, USA');
  option.listeners.mousedown = () => {
    state.model.location = 'Example City, CA, USA';
    state.model.externalPlaceId = 'place-1';
  };
  state.locationInput.listeners.input = () => {
    state.document.options = [option];
  };
  const result = await rippling.location(state.document, { query: 'Example City, CA', tries: 3, waitMs: 1 });
  assert.deepEqual(result.filled, ['location']);
  assert.ok(!option.events.includes('click'), option.events.join(','));
});

test('rippling: an earlier place id never passes for a new pick', async () => {
  const dead = model => {
    const option = new Node('li', { role: 'option' }, 'Example City, CA, USA');
    option.listeners.click = () => {};
    return option;
  };
  // The click does nothing; the old place id from an earlier fill is still in the model.
  const state = formsyForm({ model: { location: 'Old Town, NY, USA', externalPlaceId: 'old-place' } });
  state.locationInput.listeners.input = () => {
    state.document.options = [dead(state.model)];
  };
  const result = await rippling.location(state.document, { query: 'Example City, CA', tries: 3, waitMs: 1 });
  assertShape('fill', result);
  assert.equal(reasonOf(result), 'place_not_selected');
  // The place id changes but the location text is still the old one.
  const other = formsyForm({ model: { location: 'Old Town, NY, USA', externalPlaceId: 'old-place' } });
  const half = new Node('li', { role: 'option' }, 'Example City, CA, USA');
  half.listeners.click = () => {
    other.model.externalPlaceId = 'place-9';
  };
  other.locationInput.listeners.input = () => {
    other.document.options = [half];
  };
  const partial = await rippling.location(other.document, { query: 'Example City, CA', tries: 3, waitMs: 1 });
  assert.equal(reasonOf(partial), 'place_not_selected');
});
