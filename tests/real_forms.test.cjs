'use strict';

// Each adapter must recognise the core questions of real forms (structure only,
// tests/fixtures/real): the field reaches its scan with the question's own label,
// its required flag and its options. Every core field it misses is listed.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { load, Element, Input, TextArea, Select, makeDocument, addField } = require('./fake_dom.cjs');

const REAL = path.join(__dirname, 'fixtures', 'real');
// Captured folder -> adapter. iCIMS, SmartRecruiters and generic pages have no adapter.
const ADAPTERS = { gh: 'greenhouse', wd: 'workday', rp: 'rippling', ashby: 'ashby', lever: 'lever' };
const CORE = [
  ['sponsorship', /sponsor|\bvisa\b/i],
  ['work authorization', /authori[sz]|eligib|legally|right to work/i],
  ['eeo', /gender|\brace\b|ethnic|hispanic|latin[oax]|veteran|disabilit/i],
  ['education', /school|universit|college|degree|major|discipline|field of study|\bgpa\b|graduat/i],
  ['contact', /^(first|last|given|family|preferred|legal|full)?\s*name\b|e-?mail|phone|linkedin|address|\bcity\b|zip|postal/i],
];
const category = field => (field.kind === 'file' ? null : CORE.find(([, pattern]) => pattern.test(field.label))?.[0]);
const norm = text =>
  String(text ?? '')
    .replace(/[*✱]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();

function forms(ats) {
  const folder = path.join(REAL, ats);
  if (!fs.existsSync(folder)) return [];
  return fs
    .readdirSync(folder)
    .filter(name => name.endsWith('.json'))
    .sort()
    .map(name => ({ name: `${ats}/${name}`, fields: JSON.parse(fs.readFileSync(path.join(folder, name), 'utf8')).fields }));
}

const options = field => field.opts.map((label, index) => [`o${index}`, label]);
const attrs = (field, index, extra = {}) => ({
  id: field.id || `real-${index}`,
  name: field.id || `real-${index}`,
  ...(field.req ? { 'aria-required': 'true' } : {}),
  ...extra,
});
// A labelled group of radios or checkboxes inside a fieldset with a legend.
function group(parent, field, index, type) {
  const set = parent.appendChild(new Element('fieldset'));
  set.appendChild(new Element('legend', {}, field.label + (field.req ? '*' : '')));
  for (const [value, text] of options(field).length ? options(field) : [['yes', 'Yes'], ['no', 'No']]) {
    const box = new Input({ ...attrs(field, index, { type }), id: `${field.id || index}-${value}`, value });
    addField(set, box, text);
  }
}
function control(field, index, extra = {}) {
  if (field.kind === 'textarea') return new TextArea(attrs(field, index, extra));
  if (field.kind === 'choice' && field.opts.length) return new Select(attrs(field, index, extra), options(field));
  if (field.kind === 'box') return new Input(attrs(field, index, { type: 'checkbox', ...extra }));
  return new Input(attrs(field, index, { type: 'text', ...extra }));
}

// Greenhouse job board: <label for> with a trailing *, react-select shells for choices without native options.
function greenhouse(fields) {
  const page = makeDocument();
  fields.forEach((field, index) => {
    if (['radio', 'checks'].includes(field.kind)) return group(page.form, field, index, field.kind === 'radio' ? 'radio' : 'checkbox');
    const widget = field.kind === 'choice' && !field.opts.length;
    const parent = widget ? page.form.appendChild(new Element('div', { class: 'select__container' })) : page.form;
    addField(parent, control(field, index, widget ? { role: 'combobox' } : {}), field.label + (field.req ? '*' : ''));
  });
  return page.document;
}

// Workday: each question sits in a formField wrapper; choices are listbox buttons.
function workday(fields) {
  const page = makeDocument();
  fields.forEach((field, index) => {
    const wrapper = page.form.appendChild(new Element('div', { 'data-automation-id': `formField-${field.id || index}` }));
    if (['radio', 'checks'].includes(field.kind)) return group(wrapper, field, index, field.kind === 'radio' ? 'radio' : 'checkbox');
    if (field.kind === 'choice') {
      wrapper.appendChild(new Element('label', {}, field.label + (field.req ? '*' : '')));
      wrapper.appendChild(new Element('button', { ...attrs(field, index), 'aria-haspopup': 'listbox' }, 'Select One'));
      return;
    }
    addField(wrapper, control(field, index), field.label + (field.req ? '*' : ''));
  });
  return page.document;
}

// Lever: application-question with an application-label (✱ when required) above the controls.
function lever(fields) {
  const page = makeDocument();
  fields.forEach((field, index) => {
    const question = page.form.appendChild(new Element('li', { class: 'application-question' }));
    question.appendChild(new Element('div', { class: 'application-label' }, field.label + (field.req ? ' ✱' : '')));
    const box = question.appendChild(new Element('div', { class: 'application-field' }));
    if (['radio', 'checks'].includes(field.kind)) {
      for (const [value, text] of options(field)) {
        const el = new Input({ type: field.kind === 'radio' ? 'radio' : 'checkbox', name: field.id || `real-${index}`, value });
        const label = box.appendChild(new Element('label', {}, text));
        label.appendChild(el);
        el.labels = [label];
      }
    } else box.appendChild(control(field, index));
  });
  return page.document;
}

// Ashby: a field entry with a heading label (_required class and * when required); Boolean is a Yes/No toggle.
function ashby(fields) {
  const page = makeDocument();
  fields.forEach((field, index) => {
    const entry = page.form.appendChild(new Element('div', { class: '_fieldEntry_1 ashby-application-form-field-entry' }));
    const heading = entry.appendChild(
      new Element('label', { class: field.req ? '_heading_1 _required_1' : '_heading_1' }, field.label + (field.req ? ' *' : '')),
    );
    if (field.kind === 'radio' && !field.opts.length) {
      const box = entry.appendChild(new Element('div', { class: '_yesno_1' }));
      for (const text of ['Yes', 'No']) box.appendChild(new Element('button', { type: 'button', class: '_option_1' }, text));
      return;
    }
    if (['radio', 'checks'].includes(field.kind)) return group(entry, field, index, field.kind === 'radio' ? 'radio' : 'checkbox');
    const el = entry.appendChild(control(field, index, field.kind === 'choice' && !field.opts.length ? { role: 'combobox' } : {}));
    el.labels = [heading];
  });
  return page.document;
}

// Rippling: React + Formsy model; custom questions carry their text on a parent fiber.
function fiber(props, children = [], stateNode = null) {
  const node = { memoizedProps: props, stateNode, child: null, sibling: null, return: null };
  children.forEach((child, index) => {
    child.return = node;
    if (index === 0) node.child = child;
    else children[index - 1].sibling = child;
  });
  return node;
}
function rippling(fields) {
  const nodes = fields.map((field, index) => {
    const name = field.id || `customQuestions.q${index}.answer`;
    const list = field.opts.length ? { list: field.opts.map((label, at) => ({ value: `o${at}`, label })) } : {};
    const props = { name, required: field.req, isValid: true, setValue() {}, ...list };
    if (name.startsWith('customQuestions.'))
      return fiber({ question: { title: field.label, isRequired: field.req } }, [fiber(props)]);
    return fiber({ ...props, label: field.label });
  });
  const root = fiber({}, [fiber({}, nodes, { getModel: () => ({}), state: { isValid: false } })]);
  const form = { __reactFiber$real: root.child.child };
  return {
    querySelector: selector => (selector === 'form' ? form : null),
    querySelectorAll: () => [],
    defaultView: {},
  };
}

const BUILD = { greenhouse, workday, lever, ashby, rippling };
const scanOf = (adapter, ats, document) => (ats === 'rippling' ? adapter.modelScan(document) : adapter.scan(document));

// The core fields of one real form the adapter's scan does not return as the same question.
function unmatched(ats, form) {
  const adapter = load(ats);
  const scanned = scanOf(adapter, ats, BUILD[ats](form.fields)).fields.filter(item => item.type !== 'summary');
  const missing = [];
  for (const field of form.fields) {
    const kind = category(field);
    if (!kind) continue;
    const found = scanned.find(item => norm(item.label) === norm(field.label));
    const options = new Set((found?.options || []).map(option => norm(option.label)));
    const reason = !found
      ? 'not scanned under its label'
      : Boolean(found.required) !== field.req
        ? `required ${Boolean(found.required)}, form says ${field.req}`
        : field.kind !== 'choice' || found.type === 'combobox' || found.type === 'button'
          ? field.opts.filter(option => ['radio', 'checks'].includes(field.kind) && !options.has(norm(option))).length
            ? 'options missing'
            : null
          : field.opts.some(option => !options.has(norm(option)))
            ? 'options missing'
            : null;
    if (reason) missing.push(`${form.name} [${kind}] "${field.label}" (${field.kind}): ${reason}`);
  }
  return missing;
}

test('real forms: every captured ATS with an adapter has real forms with core questions', () => {
  for (const ats of Object.keys(ADAPTERS)) {
    const core = forms(ats).flatMap(form => form.fields.filter(category));
    assert.ok(core.length > 0, `${ats}: no core question in tests/fixtures/real/${ats}`);
  }
});

for (const [folder, ats] of Object.entries(ADAPTERS)) {
  test(`real forms: the ${ats} adapter recognises every core question of the real ${folder} forms`, () => {
    const missing = forms(folder).flatMap(form => unmatched(ats, form));
    assert.deepEqual(missing, [], `${missing.length} core fields unmatched:\n${missing.join('\n')}`);
  });
}

test('real forms: a core question the adapter misses is listed, not ignored', () => {
  const form = { name: 'gh/synthetic', fields: [{ id: 'q', label: 'Will you require sponsorship?', kind: 'choice', req: true, opts: ['Yes', 'No'] }] };
  const original = BUILD.greenhouse;
  BUILD.greenhouse = () => makeDocument().document;
  try {
    assert.deepEqual(unmatched('greenhouse', form), [
      'gh/synthetic [sponsorship] "Will you require sponsorship?" (choice): not scanned under its label',
    ]);
  } finally {
    BUILD.greenhouse = original;
  }
});

for (const ats of ['greenhouse', 'workday']) {
  test(`real forms: a ${ats} checkbox-group question is one field held for the engine, never set box by box`, async () => {
    const field = { id: 'q9', label: 'Ethnicity (select all that apply)', kind: 'checks', req: true, opts: ['Asian', 'White'] };
    const document = BUILD[ats]([field]);
    const adapter = load(ats);
    const [group] = adapter.scan(document).fields;
    assert.deepEqual(
      { label: group.label, type: group.type, required: group.required, value: group.value, options: group.options },
      {
        label: field.label,
        type: 'checkbox-group',
        required: true,
        value: [],
        options: [
          { value: 'o0', label: 'Asian' },
          { value: 'o1', label: 'White' },
        ],
      },
    );
    assert.deepEqual(adapter.audit(document).required_unknown, [group.id]);
    const result = await adapter.fill(document, { [group.id]: ['o0'] });
    assert.deepEqual(result.blocked, [{ id: group.id, reason: 'agent_interaction_required' }]);
    assert.deepEqual(result.filled, []);
    assert.equal(
      document.querySelectorAll('input').filter(box => box.checked).length,
      0,
      'no box is checked by script',
    );
  });
}

module.exports = { unmatched, category };
