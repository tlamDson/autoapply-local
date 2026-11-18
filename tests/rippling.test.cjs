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

test('rippling: model scan reads names, question labels, required flags and options', () => {
  const { document } = formsyForm({ model: { first_name: 'Parsed', resume: [{ name: 'alex.pdf', url: 'https://signed.example/x', progress: 100 }] } });
  const scanned = rippling.modelScan(document);
  assert.equal(scanned.formsy, true);
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
  assert.deepEqual(rippling.modelScan(document), { formsy: false, fields: [], valid: false });
});

test('rippling: modelReady waits for the uploaded resume and the parsed first name', () => {
  const state = formsyForm({ model: { resume: [{ name: 'a.pdf', url: 'u', progress: 40 }] } });
  assert.equal(rippling.modelReady(state.document), false);
  state.model.resume[0].progress = 100;
  assert.equal(rippling.modelReady(state.document), false);
  state.model.first_name = 'Alex';
  assert.equal(rippling.modelReady(state.document), true);
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
  assert.deepEqual(result, { picked: 'Example City, CA, USA', location: 'Example City, CA, USA', placed: true });
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
    assert.equal(result.blocked, reason);
    assert.equal(state.model.externalPlaceId, undefined);
  }
  const state = formsyForm();
  const trap = new Node('button', { role: 'option', type: 'submit' }, 'Example City, CA, USA');
  state.locationInput.listeners.input = () => {
    state.document.options = [trap];
  };
  const result = await rippling.location(state.document, { query: 'Example City, CA', tries: 2, waitMs: 1 });
  assert.equal(result.blocked, 'unsafe_control');
  assert.equal(trap.events.length, 0);
});

test('rippling: location without a form field or query is blocked, not guessed', async () => {
  const document = { querySelector: () => null, querySelectorAll: () => [], body: { innerText: '' } };
  assert.equal((await rippling.location(document, { query: 'Example City, CA' })).blocked, 'form_model_missing');
  const state = formsyForm();
  assert.equal((await rippling.location(state.document, { query: '' })).blocked, 'no_query');
});

test('rippling: audit adds the form validity and invalid model fields', () => {
  const state = formsyForm({ valid: false, invalid: ['phone_number'] });
  const audit = rippling.audit(state.document);
  assert.equal(audit.form_valid, false);
  assert.ok(audit.errors.includes('Invalid field: phone_number'));
  assert.ok(audit.required_unknown.includes('first_name'));
});

test('rippling: the adapter exposes no submit or navigation action', () => {
  for (const name of Object.keys(rippling)) assert.doesNotMatch(name, /submit|navigate|apply/i);
});
