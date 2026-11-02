'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

/* Giả định của người thực thi (test-only; không sửa plan): adapters CommonJS ở
 * autoapply_local/adapters/<ats>.cjs có scan(document), fill(document, answers),
 * audit(document). scan/audit luôn đọc DOM hiện tại; fill trả {filled, blocked,
 * uploads}, không click Submit. Key answer là id của field. Scan trả {fields,
 * page}; field có {id,label,type,required,value,options,record?}. audit trả thêm
 * required_unknown và errors. Upload trả yêu cầu {id,path} cho MCP bên ngoài.
 * Workday bổ sung navigate(document, 'next'|'back') và
 * syncRecords(document, 'education'|'experience', records); custom widget trả
 * blocked [{id,reason:'agent_interaction_required'}], không tự chọn giá trị.
 * DOM dưới đây hoàn toàn tổng hợp, không đọc fixture, network hay browser thật.
 */

const ADAPTER_ROOT = path.resolve(__dirname, '..', 'autoapply_local', 'adapters');
const load = ats => require(path.join(ADAPTER_ROOT, `${ats}.cjs`));

class FakeEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.bubbles = Boolean(options.bubbles);
  }
}

class Element {
  constructor(tag, attributes = {}, text = '') {
    this.tagName = tag.toUpperCase();
    this.attributes = { ...attributes };
    this.children = [];
    this.parentElement = null;
    this.textContent = text;
    this.events = [];
    this.clicks = 0;
  }
  get id() {
    return this.attributes.id || '';
  }
  get name() {
    return this.attributes.name || '';
  }
  get type() {
    return this.attributes.type || this.tagName.toLowerCase();
  }
  get required() {
    return 'required' in this.attributes;
  }
  get disabled() {
    return 'disabled' in this.attributes;
  }
  get innerText() {
    return [this.textContent, ...this.children.map(x => x.innerText)].join(' ');
  }
  get dataset() {
    return Object.fromEntries(
      Object.entries(this.attributes)
        .filter(([key]) => key.startsWith('data-'))
        .map(([key, value]) => [key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), value]),
    );
  }
  getAttribute(name) {
    return this.attributes[name] ?? null;
  }
  hasAttribute(name) {
    return name in this.attributes;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  appendChild(child) {
    child.parentElement = this;
    child.ownerDocument = this.ownerDocument;
    this.children.push(child);
    return child;
  }
  remove() {
    this.parentElement.children = this.parentElement.children.filter(x => x !== this);
  }
  matches(selector) {
    if (selector.includes(',')) return selector.split(',').some(part => this.matches(part.trim()));
    // The synthetic fixture supports ordinary tag/id/attribute selectors only.
    // Adapters can use the same browser DOM APIs without a package dependency.
    const attrs = [...selector.matchAll(/\[([\w-]+)(?:\s*([*^$]?=)\s*["']?([^\]"']*)["']?)?\]/g)];
    const head = selector.replace(/\[[^\]]*\]/g, '').trim();
    const tag = head.match(/^[a-z][\w-]*/i)?.[0];
    const id = head.match(/#([\w-]+)/)?.[1];
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    if (id && this.id !== id) return false;
    for (const [, key, op, value] of attrs) {
      const actual = this.getAttribute(key);
      if (actual === null) return false;
      if (op === '=' && actual !== value) return false;
      if (op === '*=' && !actual.includes(value)) return false;
      if (op === '^=' && !actual.startsWith(value)) return false;
      if (op === '$=' && !actual.endsWith(value)) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map(x => x.trim());
    const found = [];
    for (const child of this.children) {
      if (selectors.some(s => child.matches(s))) found.push(child);
      found.push(...child.querySelectorAll(selector));
    }
    return found;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  closest(selector) {
    for (let element = this; element; element = element.parentElement) {
      if (element.matches(selector)) return element;
    }
    return null;
  }
  dispatchEvent(event) {
    this.events.push(event);
    return true;
  }
  click() {
    this.clicks += 1;
    this.onClick?.();
  }
}

class Input extends Element {
  constructor(attributes = {}, initial = '') {
    super('input', attributes);
    this._value = initial;
    this.nativeWrites = 0;
    this.checked = false;
    this.files = [];
    this.labels = [];
  }
  get value() {
    return this._value;
  }
  set value(value) {
    assert.notEqual(this.type, 'file', 'JS must never fake a file upload');
    this.nativeWrites += 1;
    this._value = String(value);
  }
}

class TextArea extends Input {
  constructor(attributes = {}, initial = '') {
    super(attributes, initial);
    this.tagName = 'TEXTAREA';
  }
  get value() {
    return this._value;
  }
  set value(value) {
    Object.getOwnPropertyDescriptor(Input.prototype, 'value').set.call(this, value);
  }
}

class Select extends Input {
  constructor(attributes = {}, options = []) {
    super(attributes);
    this.tagName = 'SELECT';
    this.options = options.map(([value, label]) => {
      const option = new Element('option', { value }, label);
      option.value = value;
      option.label = label;
      this.appendChild(option);
      return option;
    });
  }
  get value() {
    return this._value;
  }
  set value(value) {
    Object.getOwnPropertyDescriptor(Input.prototype, 'value').set.call(this, value);
  }
}

function makeDocument() {
  const document = new Element('document');
  document.ownerDocument = document;
  document.defaultView = {
    HTMLInputElement: Input,
    HTMLTextAreaElement: TextArea,
    HTMLSelectElement: Select,
    Event: FakeEvent,
  };
  document.getElementById = id => document.querySelector(`#${id}`);
  document.createEvent = () => ({
    initEvent(type, bubbles) {
      this.type = type;
      this.bubbles = bubbles;
    },
  });
  const form = document.appendChild(
    new Element('form', {
      id: 'application',
      'data-page-index': '1',
      'data-page-total': '3',
      'data-page-title': 'My Experience',
    }),
  );
  document.body = form;
  const submit = form.appendChild(new Element('button', { type: 'submit' }, 'Submit application'));
  return { document, form, submit };
}

function addField(parent, field, labelText) {
  const label = parent.appendChild(new Element('label', { for: field.id }, labelText));
  label.htmlFor = field.id;
  label.control = field;
  field.labels = [label];
  parent.appendChild(field);
  return field;
}

function fixture() {
  const result = makeDocument();
  result.name = addField(
    result.form,
    new Input({ id: 'legal-name', name: 'legal_name', type: 'text', required: '' }),
    'Legal name',
  );
  result.email = addField(result.form, new Input({ id: 'email', type: 'email', required: '' }), 'Application email');
  result.country = addField(
    result.form,
    new Select({ id: 'country', required: '' }, [
      ['', 'Select country'],
      ['US', 'United States'],
      ['CA', 'Canada'],
    ]),
    'Country',
  );
  result.notes = addField(result.form, new TextArea({ id: 'notes' }), 'Additional information');
  result.resume = addField(result.form, new Input({ id: 'resume', type: 'file', required: '' }), 'Resume PDF');
  return result;
}

for (const ats of ['greenhouse', 'rippling', 'workday']) {
  test(`${ats}: scan discovers labels, native types, required flags and option values`, () => {
    const adapter = load(ats);
    const { document } = fixture();
    const snapshot = adapter.scan(document);
    const byId = Object.fromEntries(snapshot.fields.map(field => [field.id, field]));
    assert.equal(byId['legal-name'].label, 'Legal name');
    assert.equal(byId.email.type, 'email');
    assert.equal(byId.email.required, true);
    assert.equal(byId.notes.required, false);
    assert.equal(byId.resume.type, 'file');
    assert.deepEqual(
      byId.country.options.map(({ value, label }) => ({ value, label })),
      [
        { value: '', label: 'Select country' },
        { value: 'US', label: 'United States' },
        { value: 'CA', label: 'Canada' },
      ],
    );
  });

  test(`${ats}: fill uses native setters and bubbling input/change events without submitting`, async () => {
    const adapter = load(ats);
    const { document, name, email, country, notes, submit } = fixture();
    Object.defineProperty(name, 'value', {
      get() {
        return this._value;
      },
      set() {
        assert.fail('Controlled fields require the native prototype setter');
      },
    });
    const result = await adapter.fill(document, {
      'legal-name': 'Alex Example',
      email: 'alex@example.invalid',
      country: 'US',
      notes: 'Synthetic candidate',
    });
    assert.equal(name.value, 'Alex Example');
    assert.equal(email.value, 'alex@example.invalid');
    assert.equal(country.value, 'US');
    assert.equal(notes.value, 'Synthetic candidate');
    for (const field of [name, email, country, notes]) {
      assert.equal(field.nativeWrites, 1);
      assert.ok(field.events.some(event => event.type === 'input' && event.bubbles));
      assert.ok(field.events.some(event => event.type === 'change' && event.bubbles));
    }
    assert.deepEqual([...result.filled].sort(), ['country', 'email', 'legal-name', 'notes']);
    assert.equal(submit.clicks, 0);
  });

  test(`${ats}: PDF attachment is returned for MCP upload and never forged in JS`, async () => {
    const adapter = load(ats);
    const { document, resume, submit } = fixture();
    const result = await adapter.fill(document, { resume: '/synthetic/resume.pdf' });
    assert.deepEqual(result.uploads, [{ id: 'resume', path: '/synthetic/resume.pdf' }]);
    assert.equal(resume.value, '');
    assert.equal(resume.nativeWrites, 0);
    assert.deepEqual(resume.files, []);
    assert.equal(submit.clicks, 0);
  });

  test(`${ats}: audit reads parsed values again and reports required unknowns`, async () => {
    const adapter = load(ats);
    const { document, email, submit } = fixture();
    await adapter.fill(document, { email: 'alex@example.invalid' });
    // Simulate an ATS resume parser overwriting a previously filled value.
    email.value = 'parser-value@example.invalid';
    const current = adapter.audit(document);
    assert.equal(current.fields.find(field => field.id === 'email').value, 'parser-value@example.invalid');
    assert.deepEqual([...current.required_unknown].sort(), ['country', 'legal-name', 'resume']);
    assert.equal(submit.clicks, 0);
  });

  test(`${ats}: unknown select option is blocked without picking a different answer`, async () => {
    const adapter = load(ats);
    const { document, country } = fixture();
    const result = await adapter.fill(document, { country: 'Unlisted country' });
    assert.equal(country.value, '');
    assert.ok(result.blocked.some(field => field.id === 'country'));
  });

  test(`${ats}: repeated education and experience fields retain record identity`, () => {
    const adapter = load(ats);
    const { document, form } = makeDocument();
    for (const kind of ['education', 'experience']) {
      for (const index of [0, 1]) {
        const record = form.appendChild(
          new Element('fieldset', {
            'data-record-kind': kind,
            'data-record-index': String(index),
            'data-automation-id': `${kind}-${index + 1}`,
          }),
        );
        addField(record, new Input({ id: `${kind}-${index}-title`, type: 'text' }), `${kind} title`);
      }
    }
    const fields = adapter.scan(document).fields;
    for (const kind of ['education', 'experience']) {
      for (const index of [0, 1]) {
        assert.deepEqual(fields.find(field => field.id === `${kind}-${index}-title`).record, { kind, index });
      }
    }
  });
}

test('workday: page navigation advances/backtracks without clicking Submit', async () => {
  const adapter = load('workday');
  const { document, form, submit } = makeDocument();
  const next = form.appendChild(
    new Element(
      'button',
      { type: 'button', 'data-automation-id': 'bottom-navigation-next-button' },
      'Save and Continue',
    ),
  );
  const back = form.appendChild(
    new Element('button', { type: 'button', 'data-automation-id': 'bottom-navigation-back-button' }, 'Back'),
  );
  next.onClick = () => form.setAttribute('data-page-index', '2');
  back.onClick = () => form.setAttribute('data-page-index', '1');
  assert.deepEqual(adapter.scan(document).page, { index: 1, total: 3, title: 'My Experience' });
  await adapter.navigate(document, 'next');
  assert.equal(next.clicks, 1);
  assert.equal(adapter.scan(document).page.index, 2);
  await adapter.navigate(document, 'back');
  assert.equal(back.clicks, 1);
  assert.equal(adapter.scan(document).page.index, 1);
  assert.equal(submit.clicks, 0);
});

for (const kind of ['education', 'experience']) {
  test(`workday: synchronizes ${kind} record count by clicking Add and Remove`, async () => {
    const adapter = load('workday');
    const { document, form, submit } = makeDocument();
    const section = form.appendChild(new Element('section', { 'data-record-kind': kind, 'data-automation-id': kind }));
    const add = section.appendChild(
      new Element('button', { type: 'button', 'data-action': 'add-record', 'data-automation-id': 'add-button' }, 'Add'),
    );
    let serial = 0;
    add.onClick = () => {
      const index = section.querySelectorAll('fieldset').length;
      const record = section.appendChild(
        new Element('fieldset', {
          'data-record-kind': kind,
          'data-record-index': String(index),
          'data-automation-id': `${kind}-${++serial}`,
        }),
      );
      addField(record, new Input({ id: `${kind}-${serial}-title`, type: 'text' }), `${kind} title`);
      const remove = record.appendChild(
        new Element(
          'button',
          { type: 'button', 'data-action': 'remove-record', 'data-automation-id': 'delete-button' },
          'Remove',
        ),
      );
      remove.onClick = () => record.remove();
    };
    await adapter.syncRecords(document, kind, [{ title: 'First' }, { title: 'Second' }]);
    assert.equal(section.querySelectorAll('fieldset').length, 2);
    assert.equal(add.clicks, 2);
    await adapter.syncRecords(document, kind, [{ title: 'First' }]);
    assert.equal(section.querySelectorAll('fieldset').length, 1);
    assert.equal(submit.clicks, 0);
  });
}

test('workday: unsupported custom widgets fail closed for native MCP interaction', async () => {
  const adapter = load('workday');
  const { document, form, submit } = makeDocument();
  const custom = form.appendChild(
    new Element('div', {
      id: 'school',
      role: 'combobox',
      'aria-label': 'School',
      'aria-required': 'true',
      'data-automation-id': 'school',
    }),
  );
  const result = await adapter.fill(document, { school: 'Example University' });
  assert.ok(result.blocked.some(field => field.id === 'school' && field.reason === 'agent_interaction_required'));
  assert.equal(custom.textContent, '');
  assert.equal(custom.clicks, 0);
  assert.ok(adapter.audit(document).required_unknown.includes('school'));
  assert.equal(submit.clicks, 0);
});

/* Giả định của người thực thi: observed record prefixes are opaque IDs, exposed
 * as record.id alongside kind; no fabricated zero-based profile index. Native
 * fallback uses the existing blocked reason agent_interaction_required. These
 * fixtures contain only the selector patterns listed in the approved plan.
 */
function observedWorkday() {
  const result = makeDocument();
  for (const key of ['data-page-index', 'data-page-total', 'data-page-title']) {
    delete result.form.attributes[key];
  }
  result.main = result.form.appendChild(new Element('main', {}, 'My Experience, current step 2 of 5'));
  return result;
}

test('workday: required listbox button is scanned and audited with its actual question label', () => {
  const adapter = load('workday');
  const { document, form, submit } = observedWorkday();
  const wrapper = form.appendChild(new Element('div', { 'data-automation-id': 'formField-country' }));
  wrapper.appendChild(new Element('label', {}, 'Country of residence'));
  wrapper.appendChild(
    new Element(
      'button',
      {
        id: 'country-button',
        type: 'button',
        'aria-haspopup': 'listbox',
        'aria-controls': 'country-options',
        'aria-required': 'true',
      },
      'Select One',
    ),
  );
  const field = adapter.scan(document).fields.find(item => item.id === 'country-button');
  assert.ok(field, 'Required Workday buttons must not disappear from the scan');
  assert.equal(field.label, 'Country of residence');
  assert.equal(field.required, true);
  assert.ok(adapter.audit(document).required_unknown.includes('country-button'));
  assert.equal(submit.clicks, 0);
});

test('workday: fieldset legend labels a button instead of generic Select One', () => {
  const adapter = load('workday');
  const { document, form } = observedWorkday();
  const group = form.appendChild(new Element('fieldset'));
  group.appendChild(new Element('legend', {}, 'Have you previously worked here?'));
  group.appendChild(
    new Element(
      'button',
      {
        id: 'prior-employment',
        type: 'button',
        'aria-haspopup': 'listbox',
        'aria-required': 'true',
      },
      'Select One',
    ),
  );
  const field = adapter.scan(document).fields.find(item => item.id === 'prior-employment');
  assert.ok(field);
  assert.equal(field.label, 'Have you previously worked here?');
});

test('workday: stale options outside aria-controls listbox are never selected', async () => {
  const adapter = load('workday');
  const { document, form, submit } = observedWorkday();
  const stale = form.appendChild(new Element('div', { id: 'stale-options', role: 'listbox' }));
  const staleOption = stale.appendChild(new Element('div', { role: 'option' }, 'Canada'));
  const button = form.appendChild(
    new Element(
      'button',
      {
        id: 'country',
        type: 'button',
        'aria-haspopup': 'listbox',
        'aria-controls': 'active-options',
        'aria-label': 'Country',
        'aria-required': 'true',
      },
      'Select One',
    ),
  );
  const active = form.appendChild(new Element('div', { id: 'active-options', role: 'listbox' }));
  const activeOption = active.appendChild(new Element('div', { role: 'option' }, 'Canada'));
  activeOption.onClick = () => {
    button.textContent = 'Canada';
  };
  const result = await adapter.fill(document, { country: 'Canada' });
  assert.equal(staleOption.clicks, 0);
  // Native agent fallback is valid; silently reporting filled is not.
  if (result.filled.includes('country')) {
    assert.equal(activeOption.clicks, 1);
    assert.equal(adapter.audit(document).fields.find(item => item.id === 'country').value, 'Canada');
  } else {
    assert.ok(result.blocked.some(item => item.id === 'country' && item.reason === 'agent_interaction_required'));
    assert.equal(activeOption.clicks, 0);
  }
  assert.equal(submit.clicks, 0);
});

test('workday: sparse observed experience/education IDs preserve opaque record identity', () => {
  const adapter = load('workday');
  const { document, form } = observedWorkday();
  const definitions = [
    [
      'experience',
      'workExperience-40',
      [
        'jobTitle',
        'companyName',
        'location',
        'currentlyWorkHere',
        'roleDescription',
        'startDate-dateSectionMonth',
        'startDate-dateSectionYear',
        'endDate-dateSectionMonth',
        'endDate-dateSectionYear',
      ],
    ],
    [
      'education',
      'education-73',
      [
        'schoolName',
        'degree',
        'fieldOfStudy',
        'gradeAverage',
        'firstYearAttended-dateSectionYear',
        'lastYearAttended-dateSectionYear',
      ],
    ],
  ];
  for (const [kind, prefix, names] of definitions) {
    const record = form.appendChild(new Element('div', { 'data-automation-id': prefix }));
    for (const name of names) {
      addField(
        record,
        new Input({ id: `${prefix}--${name}`, type: name === 'currentlyWorkHere' ? 'checkbox' : 'text' }),
        name,
      );
    }
  }
  const fields = adapter.scan(document).fields;
  for (const [kind, prefix, names] of definitions) {
    for (const name of names) {
      const field = fields.find(item => item.id === `${prefix}--${name}`);
      assert.ok(field, `${prefix}--${name} must be discovered`);
      assert.ok(field.record, `${field.id} must retain record ownership`);
      assert.equal(field.record.kind, kind);
      assert.equal(field.record.id, prefix);
      assert.equal('index' in field.record, false, 'Opaque Workday record ID is not profile index');
    }
  }
});

for (const [id, automationId, tag] of [
  ['name--legalName', 'legalName', 'input'],
  ['name--firstName', 'legalNameSection_firstName', 'input'],
  ['employee-id', 'employeeID', 'input'],
  ['education-73--fieldOfStudy', 'fieldOfStudy', 'input'],
  ['phone--countryPhoneCode', 'countryPhoneCode', 'input'],
  ['workExperience-40--roleDescription', 'roleDescription', 'textarea'],
  ['workExperience-40--startDate-dateSectionMonth', 'dateSectionMonth-input', 'input'],
  ['workExperience-40--endDate-dateSectionYear', 'dateSectionYear-input', 'input'],
  ['education-73--firstYearAttended-dateSectionYear', 'dateSectionYear-input', 'input'],
]) {
  test(`workday: ${id} requires native MCP interaction without claiming setter success`, async () => {
    const adapter = load('workday');
    const { document, form, submit } = observedWorkday();
    const Type = tag === 'textarea' ? TextArea : Input;
    const field = addField(form, new Type({ id, type: 'text', 'data-automation-id': automationId }), automationId);
    const result = await adapter.fill(document, { [id]: 'Synthetic value' });
    assert.equal(field.nativeWrites, 0, 'Known unreliable Workday setters must not masquerade as successful fill');
    assert.equal(field.value, '');
    assert.equal(result.filled.includes(id), false);
    assert.ok(result.blocked.some(item => item.id === id && item.reason === 'agent_interaction_required'));
    assert.equal(submit.clicks, 0);
  });
}

test('workday: current step text and pageFooter navigation wait for asynchronous transition', async () => {
  const adapter = load('workday');
  const { document, form, main, submit } = observedWorkday();
  const next = form.appendChild(
    new Element(
      'button',
      {
        type: 'button',
        'data-automation-id': 'pageFooterNextButton',
      },
      'Save and Continue',
    ),
  );
  const back = form.appendChild(
    new Element(
      'button',
      {
        type: 'button',
        'data-automation-id': 'pageFooterBackButton',
      },
      'Back',
    ),
  );
  next.onClick = () =>
    setTimeout(() => {
      main.textContent = 'Review, current step 3 of 5';
    }, 10);
  back.onClick = () =>
    setTimeout(() => {
      main.textContent = 'My Experience, current step 2 of 5';
    }, 10);
  assert.equal(adapter.scan(document).page.index, 2);
  assert.equal(adapter.scan(document).page.total, 5);
  const advanced = await adapter.navigate(document, 'next');
  assert.equal(advanced.index, 3, 'Click completion alone is not a completed page transition');
  assert.equal(advanced.total, 5);
  const returned = await adapter.navigate(document, 'back');
  assert.equal(returned.index, 2);
  assert.equal(next.clicks, 1);
  assert.equal(back.clicks, 1);
  assert.equal(submit.clicks, 0);
});

test('workday: navigation validation errors cannot count as a step transition', async () => {
  const adapter = load('workday');
  const { document, form, submit } = observedWorkday();
  const next = form.appendChild(
    new Element(
      'button',
      {
        type: 'button',
        'data-automation-id': 'pageFooterNextButton',
      },
      'Save and Continue',
    ),
  );
  next.onClick = () => form.appendChild(new Element('div', { role: 'alert' }, 'Required date missing'));
  await assert.rejects(adapter.navigate(document, 'next'), /required|validation|error|interaction/i);
  assert.equal(next.clicks, 1, 'The test must exercise the actual footer button then see the error');
  assert.equal(submit.clicks, 0);
});

test('workday: selectedItem chips prove multi-select state with empty search input', () => {
  const adapter = load('workday');
  const { document, form } = observedWorkday();
  const wrapper = form.appendChild(new Element('div', { 'data-automation-id': 'formField-skills' }));
  wrapper.appendChild(new Element('label', {}, 'Skills'));
  wrapper.appendChild(new Input({ id: 'skills', role: 'combobox', 'aria-required': 'true', type: 'text' }));
  wrapper.appendChild(new Element('div', { 'data-automation-id': 'selectedItem' }, 'Python'));
  wrapper.appendChild(new Element('div', { 'data-automation-id': 'selectedItem' }, 'HTTP'));
  const observed = adapter.audit(document);
  const field = observed.fields.find(item => item.id === 'skills');
  assert.ok(field);
  assert.deepEqual(field.value, ['Python', 'HTTP']);
  assert.equal(observed.required_unknown.includes('skills'), false);
});

test('workday: unverified Add/Remove markup requires native agent inspection without clicking', async () => {
  const adapter = load('workday');
  const { document, form, submit } = observedWorkday();
  const section = form.appendChild(new Element('div', { 'data-automation-id': 'workExperienceSection' }));
  const unverified = section.appendChild(new Element('button', { type: 'button' }, 'Add'));
  await assert.rejects(
    adapter.syncRecords(document, 'experience', [{ title: 'Engineering Intern' }]),
    /agent|interaction/i,
  );
  assert.equal(unverified.clicks, 0);
  assert.equal(submit.clicks, 0);
});

// Giả định của người thực thi: radio group giữ một binding ổn định và option
// value/label; scan -> fill -> audit dùng lại binding dù input thiếu/trùng ID.
function sponsorshipRadios(idMode = 'unique') {
  const state = makeDocument();
  const group = state.form.appendChild(new Element('fieldset'));
  group.appendChild(new Element('legend', {}, 'Do you require future sponsorship?'));
  state.radios = ['Yes', 'No'].map((value, index) => {
    const attrs = { name: 'sponsorship', type: 'radio', required: '' };
    if (idMode !== 'missing') attrs.id = idMode === 'duplicate' ? 'duplicate-option' : `sponsor-${index}`;
    return addField(group, new Input(attrs, value), value);
  });
  state.consent = addField(
    state.form,
    new Input({ id: 'retention-consent', type: 'checkbox', required: '' }),
    'I consent to retention',
  );
  return state;
}

for (const ats of ['greenhouse', 'rippling', 'workday']) {
  for (const idMode of ['unique', 'missing', 'duplicate']) {
    test(`${ats}: radio No is one selected option with a stable ${idMode} ID binding`, async () => {
      const adapter = load(ats);
      const { document, radios, consent, submit } = sponsorshipRadios(idMode);
      radios[0].checked = true;
      const groups = adapter.scan(document).fields.filter(field => field.type === 'radio');
      assert.equal(groups.length, 1, 'One question must not become independent required Booleans');
      const group = groups[0];
      assert.ok(group.id);
      assert.equal(group.label, 'Do you require future sponsorship?');
      assert.equal(group.value, 'Yes');
      assert.deepEqual(
        group.options.map(({ value, label }) => ({ value, label })),
        [
          { value: 'Yes', label: 'Yes' },
          { value: 'No', label: 'No' },
        ],
      );
      const result = await adapter.fill(document, { [group.id]: { value: 'No', status: 'confirmed' } });
      assert.ok(result.filled.includes(group.id));
      assert.equal(result.blocked.length, 0);
      assert.deepEqual(
        radios.map(input => input.checked),
        [false, true],
      );
      assert.ok(radios[1].events.some(event => event.type === 'input' && event.bubbles));
      assert.ok(radios[1].events.some(event => event.type === 'change' && event.bubbles));
      const audited = adapter.audit(document);
      assert.equal(audited.fields.find(field => field.id === group.id).value, 'No');
      assert.equal(audited.required_unknown.includes(group.id), false);
      assert.equal(audited.required_unknown.includes('retention-consent'), true);
      assert.equal(consent.checked, false);
      assert.equal(submit.clicks, 0);
    });
  }

  test(`${ats}: unselected radio remains unknown and an inexact option cannot select it`, async () => {
    const adapter = load(ats);
    const { document, radios, submit } = sponsorshipRadios();
    const groups = adapter.scan(document).fields.filter(field => field.type === 'radio');
    assert.equal(groups.length, 1);
    assert.ok([null, ''].includes(groups[0].value));
    assert.ok(adapter.audit(document).required_unknown.includes(groups[0].id));
    const result = await adapter.fill(document, { [groups[0].id]: { value: 'N', status: 'confirmed' } });
    assert.ok(result.blocked.some(field => field.id === groups[0].id));
    assert.deepEqual(
      radios.map(input => input.checked),
      [false, false],
    );
    assert.equal(submit.clicks, 0);
  });

  test(`${ats}: missing and duplicate text IDs bind each exact DOM control through readback`, async () => {
    const adapter = load(ats);
    const { document, form, submit } = makeDocument();
    const controls = [
      addField(form, new Input({ name: 'shared', type: 'text', required: '' }), 'First question'),
      addField(form, new Input({ name: 'shared', type: 'text', required: '' }), 'Second question'),
      addField(form, new Input({ id: 'duplicate', type: 'text', required: '' }), 'Third question'),
      addField(form, new Input({ id: 'duplicate', type: 'text', required: '' }), 'Fourth question'),
    ];
    const fields = adapter.scan(document).fields;
    assert.equal(fields.length, 4);
    assert.equal(new Set(fields.map(field => field.id)).size, 4);
    const answers = Object.fromEntries(
      fields.map((field, i) => [field.id, { value: `value-${i}`, status: 'confirmed' }]),
    );
    const filled = await adapter.fill(document, answers);
    assert.equal(filled.blocked.length, 0);
    assert.deepEqual(
      controls.map(input => input.value),
      ['value-0', 'value-1', 'value-2', 'value-3'],
    );
    const readback = adapter.audit(document);
    assert.deepEqual(
      readback.fields.map(field => field.id),
      fields.map(field => field.id),
    );
    assert.deepEqual(
      readback.fields.map(field => field.value),
      controls.map(input => input.value),
    );
    assert.deepEqual(readback.required_unknown, []);
    assert.equal(submit.clicks, 0);
  });
}

// Giả định của người thực thi: static Review dùng DOM label/value thông thường;
// evidence {quote,start,end} trỏ vào text vừa quan sát, không nhận profile đáp án.
test('workday: static Review derives fresh label/value evidence without editable inputs', () => {
  const adapter = load('workday');
  const { document, form, submit } = makeDocument();
  form.setAttribute('data-page-title', 'Review');
  form.setAttribute('data-page-index', '3');
  const summary = form.appendChild(new Element('dl'));
  const row = summary.appendChild(new Element('div'));
  row.appendChild(new Element('dt', {}, 'Legal name'));
  const name = row.appendChild(new Element('dd', {}, 'Morgan Example'));
  const contact = summary.appendChild(new Element('div'));
  contact.appendChild(new Element('dt', {}, 'City'));
  contact.appendChild(new Element('dd', {}, 'Example City'));
  assert.equal(document.querySelectorAll('input,textarea,select').length, 0);

  const checkEvidence = (audit, label, value) => {
    const field = audit.fields.find(item => item.label === label);
    assert.ok(field, `Static Review must expose the actual ${label}`);
    assert.equal(field.value, value);
    assert.equal(field.observed, true);
    assert.equal(typeof audit.text, 'string');
    const evidence = field.evidence;
    assert.ok(evidence && Number.isInteger(evidence.start) && Number.isInteger(evidence.end));
    assert.ok(evidence.start >= 0 && evidence.end > evidence.start && evidence.end <= audit.text.length);
    assert.equal(audit.text.slice(evidence.start, evidence.end), evidence.quote);
    assert.ok(evidence.quote.includes(label));
    assert.ok(evidence.quote.includes(value));
  };
  const first = adapter.audit(document);
  checkEvidence(first, 'Legal name', 'Morgan Example');
  checkEvidence(first, 'City', 'Example City');
  name.textContent = 'Different Applicant';
  const fresh = adapter.audit(document);
  checkEvidence(fresh, 'Legal name', 'Different Applicant');
  assert.equal(
    fresh.fields.some(field => field.value === 'Morgan Example'),
    false,
  );
  assert.equal(submit.clicks, 0);
});
