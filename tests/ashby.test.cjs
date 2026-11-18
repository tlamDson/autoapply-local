'use strict';

// Ashby adapter on a synthetic form: hashed CSS-module classes, required marks
// on the heading, Yes/No toggle buttons and the Submit button. Loaded by
// adapters.test.cjs so the CI command runs it too.
const test = require('node:test');
const assert = require('node:assert/strict');
const { load, Element, Input, makeDocument } = require('./fake_dom.cjs');

const SPONSOR = 'Will you now or in the future require visa sponsorship?';
const RELOCATE = 'Are you open to relocation?';

function entry(form, labelText, required) {
  const node = form.appendChild(
    new Element('div', { class: '_fieldEntry_1e3gg_100 ashby-application-form-field-entry' }),
  );
  const heading = node.appendChild(
    new Element(
      'label',
      { class: required ? '_heading_1e3gg_120 _required_1e3gg_130' : '_heading_1e3gg_120' },
      required ? labelText + ' *' : labelText,
    ),
  );
  return { node, heading };
}

function yesNo(parent, { active = null, sticky = true, type = 'button' } = {}) {
  const box = parent.appendChild(new Element('div', { class: '_yesno_1e3gg_148' }));
  const buttons = ['Yes', 'No'].map(text =>
    box.appendChild(
      new Element(
        'button',
        { type, class: '_option_1e3gg_150 ashby-application-form-input-yesno-option' + (text === active ? ' _active_1e3gg_160' : '') },
        text,
      ),
    ),
  );
  for (const button of buttons)
    button.onClick = () => {
      if (!sticky) return; // React resets a scripted click on some forms
      for (const other of buttons)
        other.setAttribute('class', '_option_1e3gg_150' + (other === button ? ' _active_1e3gg_160' : ''));
    };
  return buttons;
}

function ashbyForm(options = {}) {
  const state = makeDocument();
  state.submit.setAttribute('class', '_submit_1e3gg_200 ashby-application-form-submit-button');
  const name = entry(state.form, 'Name', true);
  state.name = name.node.appendChild(new Input({ id: '_systemfield_name', name: '_systemfield_name', type: 'text' }));
  state.name.labels = [name.heading];
  const sponsor = entry(state.form, SPONSOR, true);
  state.sponsor = yesNo(sponsor.node, options.sponsor);
  const relocate = entry(state.form, RELOCATE, false);
  state.relocate = yesNo(relocate.node, { active: 'No' });
  const heard = entry(state.form, 'How did you hear about us?', true);
  state.heard = ['Job board', 'Friend'].map(value => {
    const input = heard.node.appendChild(new Input({ name: 'heard', type: 'radio', value }, value));
    input.labels = [heard.node.appendChild(new Element('label', {}, value))];
    return input;
  });
  return state;
}

const confirmed = value => ({ value, status: 'confirmed' });
const byLabel = (fields, label) => fields.find(field => field.label === label);

test('ashby: scan reads heading labels, heading required marks and Yes/No toggles', () => {
  const adapter = load('ashby');
  const { document } = ashbyForm();
  const fields = adapter.scan(document).fields;
  const name = byLabel(fields, 'Name');
  assert.equal(name.id, '_systemfield_name');
  assert.equal(name.required, true, 'Ashby marks required on the heading, not the input');
  const sponsor = byLabel(fields, SPONSOR);
  assert.equal(sponsor.type, 'yesno');
  assert.equal(sponsor.required, true);
  assert.equal(sponsor.value, '');
  assert.deepEqual(sponsor.options, [
    { value: 'Yes', label: 'Yes' },
    { value: 'No', label: 'No' },
  ]);
  const relocate = byLabel(fields, RELOCATE);
  assert.equal(relocate.required, false);
  assert.equal(relocate.value, 'No');
  assert.notEqual(sponsor.id, relocate.id);
  const heard = fields.filter(field => field.type === 'radio');
  assert.equal(heard.length, 1);
  assert.equal(heard[0].label, 'How did you hear about us?');
  assert.equal(heard[0].required, true);
  assert.ok(!fields.some(field => /submit/i.test(field.label)));
  assert.equal(adapter.scan(document).fields.find(field => field.type === 'yesno').id, sponsor.id, 'IDs are stable');
});

test('ashby: a confirmed Yes/No answer clicks the matching toggle once and reads it back', async () => {
  const adapter = load('ashby');
  const { document, sponsor, relocate, submit } = ashbyForm();
  const fields = adapter.scan(document).fields;
  const sponsorId = byLabel(fields, SPONSOR).id;
  const relocateId = byLabel(fields, RELOCATE).id;
  const result = await adapter.fill(document, { [sponsorId]: confirmed(false), [relocateId]: confirmed('No') });
  assert.deepEqual(result.blocked, []);
  assert.deepEqual([...result.filled].sort(), [relocateId, sponsorId].sort());
  assert.deepEqual(
    sponsor.map(button => button.clicks),
    [0, 1],
  );
  assert.deepEqual(
    relocate.map(button => button.clicks),
    [0, 0],
    'An already active option is not clicked again',
  );
  const audited = adapter.audit(document);
  assert.equal(byLabel(audited.fields, SPONSOR).value, 'No');
  assert.equal(audited.required_unknown.includes(sponsorId), false);
  assert.equal(submit.clicks, 0);
});

test('ashby: text fields still fill through the native setter', async () => {
  const adapter = load('ashby');
  const { document, name, submit } = ashbyForm();
  const result = await adapter.fill(document, { _systemfield_name: confirmed('Alex Rivera') });
  assert.deepEqual(result.filled, ['_systemfield_name']);
  assert.equal(name.value, 'Alex Rivera');
  assert.equal(name.nativeWrites, 1);
  assert.equal(submit.clicks, 0);
});

test('ashby: unconfirmed, unknown or inexact Yes/No answers are blocked without a click', async () => {
  const adapter = load('ashby');
  for (const [supplied, reason] of [
    [{ value: 'Yes', status: 'unknown' }, 'unconfirmed_answer'],
    [confirmed(null), 'unknown_answer'],
    [confirmed('Maybe'), 'option_not_found'],
    [confirmed('yes please'), 'option_not_found'],
  ]) {
    const { document, sponsor, submit } = ashbyForm();
    const id = byLabel(adapter.scan(document).fields, SPONSOR).id;
    const result = await adapter.fill(document, { [id]: supplied });
    assert.deepEqual(result.blocked, [{ id, reason }]);
    assert.deepEqual(result.filled, []);
    assert.equal(sponsor[0].clicks + sponsor[1].clicks, 0);
    assert.equal(submit.clicks, 0);
  }
});

test('ashby: a toggle the page resets is reported for a native click, not claimed filled', async () => {
  const adapter = load('ashby');
  const { document, sponsor } = ashbyForm({ sponsor: { sticky: false } });
  const id = byLabel(adapter.scan(document).fields, SPONSOR).id;
  const result = await adapter.fill(document, { [id]: confirmed('Yes') });
  assert.deepEqual(result.blocked, [{ id, reason: 'native_click_required' }]);
  assert.equal(sponsor[0].clicks, 1);
  assert.ok(adapter.audit(document).required_unknown.includes(id));
});

test('ashby: an option that is a submit control is never clicked', async () => {
  const adapter = load('ashby');
  const { document, sponsor, submit } = ashbyForm({ sponsor: { type: 'submit' } });
  const id = byLabel(adapter.scan(document).fields, SPONSOR).id;
  const result = await adapter.fill(document, { [id]: confirmed('Yes') });
  assert.deepEqual(result.blocked, [{ id, reason: 'submit_control' }]);
  assert.equal(sponsor[0].clicks, 0);
  assert.equal(submit.clicks, 0);
});

test('ashby: the submit guard recognises the Submit button by type, class and words', () => {
  const adapter = load('ashby');
  const { submit } = ashbyForm();
  assert.equal(adapter.isSubmitControl(submit), true);
  assert.equal(adapter.isSubmitControl(new Element('button', { type: 'button' }, 'Submit Application')), true);
  assert.equal(
    adapter.isSubmitControl(new Element('button', { type: 'button', class: 'ashby-application-form-submit-button' }, 'Go')),
    true,
  );
  assert.equal(adapter.isSubmitControl(new Element('button', { type: 'button' }, 'Yes')), false);
});

test('ashby: an answer keyed to the Submit button is refused', async () => {
  const adapter = load('ashby');
  const { document, submit } = ashbyForm();
  submit.setAttribute('data-autoapply-yesno', 'yesno:submit');
  const result = await adapter.fill(document, { 'yesno:submit': confirmed('Yes') });
  assert.deepEqual(result.filled, []);
  assert.equal(result.blocked.length, 1);
  assert.equal(submit.clicks, 0);
});

test('ashby: audit lists required headings that are still empty', () => {
  const adapter = load('ashby');
  const { document } = ashbyForm();
  const audited = adapter.audit(document);
  const ids = Object.fromEntries(audited.fields.map(field => [field.label, field.id]));
  assert.ok(audited.required_unknown.includes('_systemfield_name'));
  assert.ok(audited.required_unknown.includes(ids[SPONSOR]));
  assert.ok(audited.required_unknown.includes(ids['How did you hear about us?']));
  assert.equal(audited.required_unknown.includes(ids[RELOCATE]), false);
});

test('ashby: resolve binds a Yes/No field to its own toggle group', () => {
  const adapter = load('ashby');
  const { document, sponsor } = ashbyForm();
  const id = byLabel(adapter.scan(document).fields, SPONSOR).id;
  assert.equal(adapter.resolve(document, id), sponsor[0].parentElement);
  assert.equal(adapter.resolve(document, '_systemfield_name').id, '_systemfield_name');
});
