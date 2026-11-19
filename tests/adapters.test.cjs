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
 * Workday steps (workday_steps.cjs) bổ sung navigate(document, 'next'|'back') và
 * syncRecords(document, 'education'|'experience', records); custom widget trả
 * blocked [{id,reason:'agent_interaction_required'}], không tự chọn giá trị.
 * DOM dưới đây hoàn toàn tổng hợp, không đọc fixture, network hay browser thật.
 */

const { load, Element, Input, TextArea, Select, makeDocument, addField } = require('./fake_dom.cjs');

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

for (const ats of ['greenhouse', 'rippling', 'workday', 'ashby']) {
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
  const adapter = load('workday_steps');
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
    const adapter = load('workday_steps');
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
  const adapter = load('workday_steps');
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
  const adapter = load('workday_steps');
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
  const adapter = load('workday_steps');
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

for (const ats of ['greenhouse', 'rippling', 'workday', 'ashby']) {
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

/* Greenhouse script driver: tests/greenhouse_form.json holds the synthetic DOM
 * (form), the adapter's recorded scan of it that the Python driver plans from
 * (scan) and the answers the driver plans (answers). These tests prove the
 * adapter still produces that scan and fills those answers.
 */
const GREENHOUSE = JSON.parse(
  require('node:fs').readFileSync(path.resolve(__dirname, 'greenhouse_form.json'), 'utf8'),
);

function greenhouseForm() {
  const result = makeDocument();
  result.submit.setAttribute('id', 'submit_app');
  result.fields = {};
  result.widgets = {};
  for (const spec of GREENHOUSE.form) {
    const parent = spec.widget
      ? result.form.appendChild(new Element('div', { class: 'select__container' }))
      : result.form;
    const el =
      spec.tag === 'select'
        ? new Select(spec.attrs, spec.options)
        : spec.tag === 'textarea'
          ? new TextArea(spec.attrs)
          : new Input(spec.attrs);
    result.fields[spec.attrs.id] = addField(parent, el, spec.label);
    if (spec.widget)
      result.widgets[spec.attrs.id] = parent.appendChild(new Element('div', { class: 'select__single-value' }, ''));
  }
  return result;
}

const allElements = node => [node, ...node.children.flatMap(allElements)];

test('greenhouse: scan of the DOM fixture matches the recorded scan the Python driver plans from', () => {
  const adapter = load('greenhouse');
  const { document } = greenhouseForm();
  assert.deepEqual(adapter.scan(document).fields, GREENHOUSE.scan);
});

test('greenhouse: fill sets every planned field, holds widgets for the engine and never clicks', async () => {
  const adapter = load('greenhouse');
  const { document, fields, submit } = greenhouseForm();
  const planned = GREENHOUSE.answers;
  const result = await adapter.fill(document, { ...planned, 'candidate-location': 'Springfield' });
  assert.deepEqual([...result.filled].sort(), Object.keys(planned).sort());
  for (const [id, value] of Object.entries(planned)) assert.equal(fields[id].value, value, id);
  assert.deepEqual(result.blocked, [{ id: 'candidate-location', reason: 'agent_interaction_required' }]);
  assert.equal(fields['candidate-location'].nativeWrites, 0);
  assert.equal(submit.clicks, 0);
  assert.ok(allElements(document).every(el => el.clicks === 0));
});

test('greenhouse: the Submit control can never be targeted by a fill', async () => {
  const adapter = load('greenhouse');
  const { document, submit } = greenhouseForm();
  const result = await adapter.fill(document, { submit_app: 'Submit application' });
  assert.deepEqual(result.filled, []);
  assert.deepEqual(result.blocked, [{ id: 'submit_app', reason: 'field_not_found' }]);
  assert.equal(submit.clicks, 0);
});

test('greenhouse: audit reads react-select values and asterisk-only required labels', () => {
  const adapter = load('greenhouse');
  const { document, widgets } = greenhouseForm();
  widgets['candidate-location'].textContent = 'Springfield, Illinois, United States';
  const audit = adapter.audit(document);
  const location = audit.fields.find(field => field.id === 'candidate-location');
  assert.equal(location.value, 'Springfield, Illinois, United States');
  assert.ok(!audit.required_unknown.includes('candidate-location'));
  assert.ok(audit.required_unknown.includes('phone'));
  assert.ok(!audit.required_unknown.includes('gender'));
});

/* Lever script driver: tests/lever_form.json holds the synthetic DOM (form),
 * the adapter's recorded scan of it that the Python driver plans from (scan)
 * and the answers the driver plans (answers). Lever controls have no stable
 * IDs, so the adapter binds each one to its form name.
 */
const LEVER = JSON.parse(require('node:fs').readFileSync(path.resolve(__dirname, 'lever_form.json'), 'utf8'));

function leverForm() {
  const result = makeDocument();
  result.submit.setAttribute('id', 'btn-submit');
  result.controls = [];
  for (const question of LEVER.form) {
    let parent = result.form;
    if (question.label !== null) {
      parent = result.form.appendChild(new Element('li', { class: 'application-question' }));
      parent.appendChild(new Element('div', { class: 'application-label' }, question.label));
      parent = parent.appendChild(new Element('div', { class: 'application-field' }));
    }
    for (const spec of question.controls) {
      const el =
        spec.tag === 'select'
          ? new Select(spec.attrs, spec.options)
          : spec.tag === 'textarea'
            ? new TextArea(spec.attrs)
            : new Input(spec.attrs, spec.attrs.value || '');
      if (spec.text) {
        const label = parent.appendChild(new Element('label', {}, spec.text));
        label.appendChild(el);
        el.labels = [label];
      } else parent.appendChild(el);
      result.controls.push(el);
    }
  }
  result.named = name => result.controls.filter(el => el.name === name);
  return result;
}

test('lever: scan of the DOM fixture matches the recorded scan the Python driver plans from', () => {
  const adapter = load('lever');
  const { document } = leverForm();
  assert.deepEqual(adapter.scan(document).fields, LEVER.scan);
  // A second scan reuses the same name bindings.
  assert.deepEqual(adapter.scan(document).fields, LEVER.scan);
});

test('lever: fill sets every planned field, holds widgets for the engine and never clicks', async () => {
  const adapter = load('lever');
  const { document, named, submit } = leverForm();
  const planned = LEVER.answers;
  const result = await adapter.fill(document, {
    ...planned,
    location: 'Springfield',
    'cards[a1b2][field2]': 'Data',
  });
  assert.deepEqual([...result.filled].sort(), Object.keys(planned).sort());
  for (const id of ['name', 'email', 'phone', 'urls[LinkedIn]', 'urls[Portfolio]', 'eeo[gender]'])
    assert.equal(named(id)[0].value, planned[id], id);
  assert.deepEqual(
    named('cards[a1b2][field0]').map(el => el.checked),
    [true, false],
  );
  assert.deepEqual(
    [...result.blocked].sort((a, b) => a.id.localeCompare(b.id)),
    [
      { id: 'cards[a1b2][field2]', reason: 'agent_interaction_required' },
      { id: 'location', reason: 'agent_interaction_required' },
    ],
  );
  assert.equal(named('location')[0].nativeWrites, 0);
  assert.ok(named('cards[a1b2][field2]').every(el => !el.checked));
  assert.equal(submit.clicks, 0);
  assert.ok(allElements(document).every(el => el.clicks === 0));
});

test('lever: the Submit control and the captcha response can never be targeted by a fill', async () => {
  const adapter = load('lever');
  const { document, named, submit } = leverForm();
  const result = await adapter.fill(document, { 'btn-submit': 'Submit', 'h-captcha-response': 'token' });
  assert.deepEqual(result.filled, []);
  assert.deepEqual(
    [...result.blocked].sort((a, b) => a.id.localeCompare(b.id)),
    [
      { id: 'btn-submit', reason: 'field_not_found' },
      { id: 'h-captcha-response', reason: 'field_not_found' },
    ],
  );
  assert.equal(named('h-captcha-response')[0].nativeWrites, 0);
  assert.equal(submit.clicks, 0);
});

test('lever: audit counts the location only once a suggestion is chosen and reads checkbox groups', () => {
  const adapter = load('lever');
  const { document, named } = leverForm();
  named('location')[0]._value = 'Springfield';
  let audit = adapter.audit(document);
  assert.equal(audit.fields.find(field => field.id === 'location').value, '');
  assert.ok(audit.required_unknown.includes('location'));
  named('selectedLocation')[0]._value = '{"name":"Springfield, Illinois, United States"}';
  named('cards[a1b2][field2]')[1].checked = true;
  named('cards[a1b2][field0]')[1].checked = true;
  audit = adapter.audit(document);
  assert.equal(audit.fields.find(field => field.id === 'location').value, 'Springfield');
  assert.deepEqual(audit.fields.find(field => field.id === 'cards[a1b2][field2]').value, ['Data']);
  assert.ok(!audit.required_unknown.includes('location'));
  assert.ok(!audit.required_unknown.includes('cards[a1b2][field0]'));
  assert.ok(audit.required_unknown.includes('cards[a1b2][field1]'));
  assert.ok(audit.required_unknown.includes('cards[a1b2][field3]'));
  assert.ok(audit.required_unknown.includes('name'));
  assert.ok(!audit.required_unknown.includes('phone'));
  assert.ok(!audit.fields.some(field => /captcha/.test(field.id)));
});

/* Workable script driver: tests/workable_form.json holds the synthetic DOM
 * (form), the adapter's recorded scan of it that the Python driver plans from
 * (scan) and the answers the driver plans (answers). Workable wraps each
 * question in a [data-ui=<form-API id>] box; the adapter binds to that id.
 */
const WORKABLE = JSON.parse(require('node:fs').readFileSync(path.resolve(__dirname, 'workable_form.json'), 'utf8'));

function workableForm() {
  const result = makeDocument();
  result.submit.setAttribute('data-ui', 'apply-button');
  result.controls = [];
  result.boxes = {};
  const banner = result.document.appendChild(new Element('div', { 'data-ui': 'cookie-consent' }));
  banner.appendChild(new Element('button', { 'data-ui': 'cookie-consent-decline' }, 'Decline'));
  for (const box of WORKABLE.form) {
    const parent = box.ui === null ? result.form : result.form.appendChild(new Element('div', { 'data-ui': box.ui }));
    if (box.ui !== null) result.boxes[box.ui] = parent;
    if (box.label !== null) parent.appendChild(new Element('label', {}, box.label));
    for (const text of box.radios || [])
      parent.appendChild(new Element('div', { role: 'radio', 'aria-checked': 'false' }, text));
    if (box.add) {
      for (const card of box.cards) parent.appendChild(new Element('div', { 'data-ui': 'group' }, card));
      parent.appendChild(new Element('button', { 'data-ui': 'add-section' }, 'Add'));
    }
    for (const spec of box.controls || []) {
      const el =
        spec.tag === 'select'
          ? new Select(spec.attrs, spec.options)
          : spec.tag === 'textarea'
            ? new TextArea(spec.attrs)
            : new Input(spec.attrs, spec.attrs.value || '');
      if (spec.text) {
        const label = parent.appendChild(new Element('label', {}, spec.text));
        label.appendChild(el);
        el.labels = [label];
      } else parent.appendChild(el);
      result.controls.push(el);
    }
  }
  result.box = ui => result.boxes[ui];
  result.control = ui => result.controls.filter(el => el.closest(`[data-ui="${ui}"]`));
  return result;
}

test('workable: scan of the DOM fixture matches the recorded scan the Python driver plans from', () => {
  const adapter = load('workable');
  const { document } = workableForm();
  assert.deepEqual(adapter.scan(document).fields, WORKABLE.scan);
  // A second scan reuses the same box bindings.
  assert.deepEqual(adapter.scan(document).fields, WORKABLE.scan);
});

test('workable: fill sets every planned field, holds widgets for the engine and never clicks', async () => {
  const adapter = load('workable');
  const { document, control, box, submit } = workableForm();
  const planned = WORKABLE.answers;
  const result = await adapter.fill(document, {
    ...planned,
    QA_100: 'Yes',
    QA_102: 'LinkedIn',
    QA_103: 'Remote',
    education: 'Springfield State University',
  });
  assert.deepEqual([...result.filled].sort(), Object.keys(planned).sort());
  for (const id of Object.keys(planned)) assert.equal(control(id)[0].value, planned[id], id);
  assert.deepEqual(
    [...result.blocked].sort((a, b) => a.id.localeCompare(b.id)),
    [
      { id: 'education', reason: 'agent_interaction_required' },
      { id: 'QA_100', reason: 'agent_interaction_required' },
      { id: 'QA_102', reason: 'agent_interaction_required' },
      { id: 'QA_103', reason: 'agent_interaction_required' },
    ],
  );
  assert.equal(control('QA_102')[0].nativeWrites, 0);
  assert.ok(control('QA_103').every(el => !el.checked));
  assert.ok(box('QA_100').children.every(el => el.getAttribute('aria-checked') !== 'true'));
  assert.equal(submit.clicks, 0);
  assert.ok(allElements(document).every(el => el.clicks === 0));
});

test('workable: the Submit control, cookie banner and captcha response can never be targeted by a fill', async () => {
  const adapter = load('workable');
  const { document, controls, submit } = workableForm();
  const result = await adapter.fill(document, {
    'apply-button': 'Submit',
    'cookie-consent-decline': true,
    'h-captcha-response': 'token',
  });
  assert.deepEqual(result.filled, []);
  assert.deepEqual(
    [...result.blocked].sort((a, b) => a.id.localeCompare(b.id)),
    [
      { id: 'apply-button', reason: 'field_not_found' },
      { id: 'cookie-consent-decline', reason: 'field_not_found' },
      { id: 'h-captcha-response', reason: 'field_not_found' },
    ],
  );
  assert.equal(controls.find(el => el.name === 'h-captcha-response').nativeWrites, 0);
  assert.equal(submit.clicks, 0);
});

test('workable: audit reads chosen Yes/No buttons, saved entries and checkbox groups', () => {
  const adapter = load('workable');
  const { document, box, control } = workableForm();
  let audit = adapter.audit(document);
  for (const id of ['resume', 'firstname', 'QA_100', 'QA_101', 'QA_104', 'gdpr'])
    assert.ok(audit.required_unknown.includes(id), id);
  assert.ok(!audit.required_unknown.includes('headline'));
  box('QA_100').children.find(el => el.innerText.trim() === 'No').setAttribute('aria-checked', 'true');
  const card = new Element('div', { 'data-ui': 'group' });
  card.appendChild(new Element('dd', { 'data-ui': 'school' }, 'Springfield State University'));
  box('education').children.splice(1, 0, card);
  card.parentElement = box('education');
  control('QA_103')[1].checked = true;
  control('QA_102')[0]._value = 'LinkedIn';
  control('gdpr')[0].checked = true;
  audit = adapter.audit(document);
  const byId = Object.fromEntries(audit.fields.map(item => [item.id, item]));
  assert.equal(byId.QA_100.value, 'No');
  assert.deepEqual(byId.education.value, ['Springfield State University']);
  assert.deepEqual(byId.QA_103.value, ['Boston']);
  assert.equal(byId.QA_102.value, 'LinkedIn');
  assert.ok(!audit.required_unknown.includes('QA_100'));
  assert.ok(!audit.required_unknown.includes('gdpr'));
  assert.ok(audit.required_unknown.includes('QA_101'));
  assert.ok(!audit.fields.some(item => /captcha|cookie/.test(item.id)));
});

require('./ashby.test.cjs');
require('./workday.test.cjs');
require('./real_forms.test.cjs');

/* Offline demo form (plan aa-09): autoapply_local/data/demo_form.json is the page
 * setup fills to prove the stack works. The Greenhouse adapter must scan every
 * field with its label and fill the planned ones without a click; the page has
 * no Submit control at all.
 */
function demoForm() {
  const spec = JSON.parse(
    require('node:fs').readFileSync(path.join(__dirname, '..', 'autoapply_local', 'data', 'demo_form.json'), 'utf8'),
  );
  const { document, form } = makeDocument();
  const fields = {};
  for (const item of spec.fields) {
    const el = item.tag === 'textarea' ? new TextArea(item.attrs) : new Input(item.attrs);
    fields[item.attrs.id] = addField(form, el, item.label);
  }
  return { spec, document, fields };
}

test('demo form: greenhouse scan reads every field, label and required flag', () => {
  const adapter = load('greenhouse');
  const { spec, document } = demoForm();
  const scanned = adapter.scan(document).fields.filter(field => field.type !== 'summary');
  assert.deepEqual(
    scanned.map(field => [field.id, field.label, field.type, Boolean(field.required)]),
    spec.fields.map(item => [
      item.attrs.id,
      item.label.replace(/\*$/, '').trim(),
      item.attrs.type || item.tag,
      item.label.endsWith('*'),
    ]),
  );
  assert.ok(!spec.fields.some(item => item.attrs.type === 'submit'));
});

test('demo form: fill sets the planned text fields and never clicks', async () => {
  const adapter = load('greenhouse');
  const { document, fields } = demoForm();
  const answers = {
    first_name: 'Alex',
    last_name: 'Rivera',
    email: 'alex.rivera@example.com',
    phone: '+1 555 0100',
    question_1: 'https://www.linkedin.com/in/alex-rivera-example',
  };
  const confirmed = Object.fromEntries(Object.entries(answers).map(([k, v]) => [k, { value: v, status: 'confirmed' }]));
  const result = await adapter.fill(document, confirmed);
  assert.deepEqual([...result.filled].sort(), Object.keys(answers).sort());
  for (const [id, value] of Object.entries(answers)) assert.equal(fields[id].value, value, id);
  assert.ok(allElements(document).every(el => el.clicks === 0));
});
