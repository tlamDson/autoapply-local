'use strict';

// Workday steps adapter on synthetic pages: the posting, the apply options,
// sign-in, create-account, the verification-code page, form steps and Review.
// Every click goes through press(): a fixed action whose control text matches,
// never a control that says Submit, and Next never on the last step. Loaded
// by adapters.test.cjs so the CI command runs it too. Candidate data is invented.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { ADAPTER_ROOT, load, Element, Input, makeDocument } = require('./fake_dom.cjs');

const EMAIL = 'alex.rivera@example.com';
const PASSWORD = 'synthetic-passphrase-42';

function page() {
  const result = makeDocument();
  for (const key of ['data-page-index', 'data-page-total', 'data-page-title']) delete result.form.attributes[key];
  return result;
}

function button(parent, automationId, text, attributes = {}) {
  return parent.appendChild(new Element('button', { type: 'button', 'data-automation-id': automationId, ...attributes }, text));
}

function input(parent, automationId, attributes = {}) {
  return parent.appendChild(new Input({ type: 'text', 'data-automation-id': automationId, ...attributes }));
}

function progress(parent, text) {
  return parent.appendChild(new Element('div', { 'data-automation-id': 'progressBarActiveStep' }, text));
}

// document.execCommand('insertText') types into the focused element the way a keystroke does.
function typing(document) {
  let focused = null;
  const proto = Object.getPrototypeOf(new Input());
  const focus = function () {
    focused = this;
  };
  proto.focus = focus;
  proto.select = () => {};
  proto.blur = () => {};
  document.execCommand = (command, _ui, value) => {
    if (command !== 'insertText' || !focused) return false;
    focused._value = String(value);
    focused.inserted = (focused.inserted || 0) + 1;
    return true;
  };
}

test('workday steps: where() names every page of the apply flow', () => {
  const steps = load('workday_steps');
  const cases = [
    ['job', form => button(form, 'adventureButton', 'Apply')],
    ['start', form => button(form, 'applyManually', 'Apply Manually')],
    ['sign_in', form => {
      progress(form, 'current step 1 of 6 Create Account/Sign In');
      input(form, 'email');
      input(form, 'password', { type: 'password' });
      button(form, 'signInSubmitButton', 'Sign In');
    }],
    ['create_account', form => {
      progress(form, 'current step 1 of 6 Create Account/Sign In');
      input(form, 'email');
      button(form, 'createAccountSubmitButton', 'Create Account');
    }],
    ['verify_code', form => {
      input(form, 'verificationCode', { 'aria-label': 'Verification code' });
      button(form, 'verifyButton', 'Verify');
    }],
    ['form', form => progress(form, 'current step 2 of 6 My Information')],
    ['review', form => progress(form, 'current step 6 of 6 Review')],
    ['applied', form => form.appendChild(new Element('p', {}, "You've already applied for this job."))],
    ['captcha', form => form.appendChild(new Element('iframe', { src: 'https://captcha.example.invalid/challenge' }))],
    ['verify_email', form => form.appendChild(new Element('p', {}, 'Please verify your email to continue.'))],
    ['unknown', () => {}],
  ];
  for (const [expected, build] of cases) {
    const { document, form } = page();
    build(form);
    assert.equal(steps.where(document).page, expected, expected);
  }
});

test('workday steps: where() reads the step, its title and the page errors', () => {
  const steps = load('workday_steps');
  const { document, form } = page();
  progress(form, 'current step 3 of 6 My Experience');
  form.appendChild(new Element('div', { 'data-automation-id': 'errorMessage' }, 'Error: Resume is required'));
  const where = steps.where(document);
  assert.deepEqual(where.step, { index: 3, total: 6, title: 'My Experience' });
  assert.deepEqual(where.errors, ['Error: Resume is required']);
});

test('workday steps: a sign-in page with only the email option says so', () => {
  const steps = load('workday_steps');
  const { document, form } = page();
  button(form, 'SignInWithEmailButton', 'Sign in with email');
  button(form, 'createAccountLink', 'Create Account');
  const where = steps.where(document);
  assert.equal(where.page, 'sign_in');
  assert.equal(where.sign_in_with_email, true);
  assert.equal(where.create_link, true);
});

test('workday steps: press() clicks the action control and waits for the page to change', async () => {
  const steps = load('workday_steps');
  const { document, form, submit } = page();
  const apply = button(form, 'adventureButton', 'Apply');
  apply.onClick = () => {
    apply.remove();
    button(form, 'applyManually', 'Apply Manually');
  };
  const result = await steps.press(document, 'apply', { ms: 200, tick: 5 });
  assert.equal(result.pressed, true);
  assert.equal(result.moved, true);
  assert.equal(result.where.page, 'start');
  assert.equal(apply.clicks, 1);
  assert.equal(submit.clicks, 0);
});

test('workday steps: press() clicks the click_filter overlay Workday lays over a button', async () => {
  const steps = load('workday_steps');
  const { document, form } = page();
  const box = form.appendChild(new Element('div'));
  const signIn = button(box, 'signInSubmitButton', 'Sign In');
  const filter = box.appendChild(new Element('div', { 'data-automation-id': 'click_filter', 'aria-label': 'Sign In' }));
  const result = await steps.press(document, 'sign_in', { ms: 20, tick: 5 });
  assert.equal(result.pressed, true);
  assert.equal(result.moved, false);
  assert.equal(filter.clicks, 1);
  assert.equal(signIn.clicks, 0);
});

test('workday steps: press() never clicks a control that says Submit, whatever the action', async () => {
  const steps = load('workday_steps');
  const ids = {
    apply: 'adventureButton',
    apply_manually: 'applyManually',
    sign_in_with_email: 'SignInWithEmailButton',
    create_account_link: 'createAccountLink',
    sign_in: 'signInSubmitButton',
    create_account: 'createAccountSubmitButton',
    verify: 'verifyButton',
    next: 'pageFooterNextButton',
  };
  assert.deepEqual([...steps.ACTIONS].sort(), Object.keys(ids).sort());
  for (const [action, id] of Object.entries(ids)) {
    for (const [text, attributes] of [
      ['Submit', {}],
      ['Next', { 'aria-label': 'Submit application' }],
    ]) {
      const { document, form, submit } = page();
      progress(form, 'current step 2 of 6 My Information');
      const control = button(form, id, text, attributes);
      const result = await steps.press(document, action, { ms: 20, tick: 5 });
      assert.deepEqual([result.pressed, result.reason], [false, 'submit_control'], action + ' ' + text);
      assert.equal(control.clicks, 0);
      assert.equal(submit.clicks, 0);
    }
  }
});

test('workday steps: press() refuses an unknown action and a control with unexpected text', async () => {
  const steps = load('workday_steps');
  const { document, form, submit } = page();
  const apply = button(form, 'adventureButton', 'Withdraw application');
  assert.deepEqual(await steps.press(document, 'submit', { ms: 20 }), { pressed: false, reason: 'unknown_action' });
  assert.deepEqual(await steps.press(document, 'toString', { ms: 20 }), { pressed: false, reason: 'unknown_action' });
  assert.deepEqual(await steps.press(document, 'apply', { ms: 20 }), { pressed: false, reason: 'unexpected_control' });
  assert.deepEqual(await steps.press(document, 'sign_in', { ms: 20 }), { pressed: false, reason: 'control_not_found' });
  assert.equal(apply.clicks, 0);
  assert.equal(submit.clicks, 0);
});

test('workday steps: Next is pressed on a form step and refused on Review', async () => {
  const steps = load('workday_steps');
  const { document, form, submit } = page();
  const bar = progress(form, 'current step 5 of 6 Voluntary Disclosures');
  const next = button(form, 'pageFooterNextButton', 'Save and Continue');
  next.onClick = () => {
    bar.textContent = 'current step 6 of 6 Review';
    next.textContent = 'Submit';
  };
  const moved = await steps.press(document, 'next', { ms: 200, tick: 5 });
  assert.deepEqual([moved.pressed, moved.moved, moved.where.page], [true, true, 'review']);
  next.textContent = 'Next';
  assert.deepEqual(await steps.press(document, 'next', { ms: 20 }), { pressed: false, reason: 'not_a_form_step' });
  assert.equal(next.clicks, 1);
  assert.equal(submit.clicks, 0);
});

test('workday steps: Next refuses a submit-type footer button', async () => {
  const steps = load('workday_steps');
  const { document, form } = page();
  progress(form, 'current step 2 of 6 My Information');
  const next = button(form, 'pageFooterNextButton', 'Next', { type: 'submit' });
  assert.deepEqual(await steps.press(document, 'next', { ms: 20 }), { pressed: false, reason: 'submit_control' });
  assert.equal(next.clicks, 0);
});

test('workday steps: Next that raises validation errors reports them and does not move', async () => {
  const steps = load('workday_steps');
  const { document, form } = page();
  progress(form, 'current step 2 of 6 My Information');
  const next = button(form, 'pageFooterNextButton', 'Next');
  next.onClick = () => form.appendChild(new Element('div', { 'data-automation-id': 'errorMessage' }, 'Error: City is required'));
  const result = await steps.press(document, 'next', { ms: 200, tick: 5 });
  assert.deepEqual([result.pressed, result.moved], [true, false]);
  assert.deepEqual(result.where.errors, ['Error: City is required']);
});

const HOST = 'exampleco.wd5.myworkdayjobs.com';

function signInPage(host = HOST) {
  const result = page();
  result.document.location = { hostname: host };
  result.email = input(result.form, 'email');
  result.password = input(result.form, 'password', { type: 'password' });
  result.button = button(result.form, 'signInSubmitButton', 'Sign In');
  return result;
}

function createPage(terms) {
  const result = page();
  result.document.location = { hostname: HOST };
  result.email = input(result.form, 'email');
  result.password = input(result.form, 'password', { type: 'password' });
  result.verify = input(result.form, 'verifyPassword', { type: 'password' });
  if (terms !== undefined) {
    result.terms = result.form.appendChild(new Input({ type: 'checkbox', 'data-automation-id': 'createAccountCheckbox' }));
    result.terms.checked = terms;
  }
  button(result.form, 'createAccountSubmitButton', 'Create Account');
  return result;
}

test('workday steps: where() reports the tab host the writes are bound to', () => {
  const steps = load('workday_steps');
  assert.equal(steps.where(signInPage('ExampleCo.wd5.myworkdayjobs.com').document).host, HOST);
});

test('workday steps: sign-in credentials are typed on the checked page and host; only field IDs come back', () => {
  const steps = load('workday_steps');
  const { document, email, password } = signInPage();
  typing(document);
  const result = steps.credentials(document, { email: EMAIL, password: PASSWORD, page: 'sign_in', host: HOST });
  assert.deepEqual(result, { typed: ['email', 'password'], failed: [] });
  assert.equal(email.value, EMAIL);
  assert.equal(password.value, PASSWORD);
  assert.ok(!JSON.stringify(result).includes(PASSWORD));
});

test('workday steps: credentials are refused on another page, another host or another expected page', () => {
  const steps = load('workday_steps');
  const cases = [
    ['wrong_host', signInPage('evil.example.invalid'), { page: 'sign_in', host: HOST }],
    ['wrong_host', signInPage(), { page: 'sign_in', host: '' }],
    ['wrong_page', signInPage(), { page: 'create_account', host: HOST, confirm: true }],
    ['wrong_page', signInPage(), { page: 'create_account', host: HOST }],
  ];
  const form = page();
  form.document.location = { hostname: HOST };
  form.email = input(form.form, 'email');
  form.password = input(form.form, 'password', { type: 'password' });
  progress(form.form, 'current step 2 of 6 My Information');
  cases.push(['wrong_page', form, { page: 'sign_in', host: HOST }]);
  for (const [reason, built, options] of cases) {
    const result = steps.credentials(built.document, { email: EMAIL, password: PASSWORD, ...options });
    assert.deepEqual(result, { typed: [], failed: [], refused: reason }, JSON.stringify(options));
    assert.equal(built.email.nativeWrites, 0);
    assert.equal(built.password.value, '');
  }
});

test('workday steps: a password box that is not type=password gets nothing typed at all', () => {
  const steps = load('workday_steps');
  const built = page();
  built.document.location = { hostname: HOST };
  const email = input(built.form, 'email');
  const password = input(built.form, 'password');
  button(built.form, 'signInSubmitButton', 'Sign In');
  const result = steps.credentials(built.document, { email: EMAIL, password: PASSWORD, page: 'sign_in', host: HOST });
  assert.deepEqual(result, { typed: [], failed: ['password'], refused: 'unexpected_field' });
  assert.deepEqual([email.value, password.value], ['', '']);
});

test('workday steps: a missing credential field is reported and nothing is typed', () => {
  const steps = load('workday_steps');
  const built = page();
  built.document.location = { hostname: HOST };
  const email = input(built.form, 'email');
  button(built.form, 'signInSubmitButton', 'Sign In');
  assert.deepEqual(steps.credentials(built.document, { email: EMAIL, password: PASSWORD, page: 'sign_in', host: HOST }), {
    typed: [],
    failed: ['password'],
    refused: 'unexpected_field',
  });
  assert.equal(email.value, '');
});

test('workday steps: create-account credentials fill the confirmation and never tick the terms box', () => {
  const steps = load('workday_steps');
  for (const [terms, state] of [
    [false, 'unchecked'],
    [true, 'checked'],
    [undefined, 'absent'],
  ]) {
    const built = createPage(terms);
    const options = { email: EMAIL, password: PASSWORD, confirm: true, page: 'create_account', host: HOST };
    const result = steps.credentials(built.document, options);
    assert.deepEqual(result, { typed: ['email', 'password', 'verifyPassword'], failed: [], terms: state });
    assert.deepEqual([built.email.value, built.password.value, built.verify.value], [EMAIL, PASSWORD, PASSWORD]);
    if (built.terms) {
      assert.equal(built.terms.clicks, 0);
      assert.equal(built.terms.checked, terms);
    }
  }
  const source = fs.readFileSync(path.join(ADAPTER_ROOT, 'workday_steps.cjs'), 'utf8');
  assert.doesNotMatch(source, /createAccountCheckbox[^\n]*\.click\s*\(|box\.click\s*\(/);
});

test('workday steps: the verification code is typed only on the code page of the checked host', () => {
  const steps = load('workday_steps');
  const built = page();
  built.document.location = { hostname: HOST };
  const box = input(built.form, 'otp', { 'aria-label': 'Enter verification code' });
  assert.deepEqual(steps.code(built.document, { code: '482913', host: 'evil.example.invalid' }), {
    typed: false,
    refused: 'wrong_host',
  });
  assert.equal(box.value, '');
  assert.deepEqual(steps.code(built.document, { code: '482913', host: HOST }), { typed: true });
  assert.equal(box.value, '482913');
  const other = signInPage();
  assert.deepEqual(steps.code(other.document, { code: '482913', host: HOST }), { typed: false, refused: 'wrong_page' });
  assert.equal(other.email.value, '');
  const hidden = page();
  hidden.document.location = { hostname: HOST };
  const secret = input(hidden.form, 'verificationCode', { type: 'password' });
  assert.deepEqual(steps.code(hidden.document, { code: '482913', host: HOST }), { typed: false, refused: 'unexpected_field' });
  assert.equal(secret.value, '');
});

test('workday steps: typeText types only with insertText and reports what did not take', () => {
  const steps = load('workday_steps');
  const { document, form } = page();
  const first = input(form, 'legalNameSection_firstName', { id: 'name--legalName--firstName' });
  const resume = form.appendChild(new Input({ id: 'resume-file', type: 'file' }));
  // Without insertText the React-controlled name box is not set by a plain setter.
  assert.deepEqual(steps.typeText(document, { 'name--legalName--firstName': 'Alex' }), {
    typed: [],
    failed: ['name--legalName--firstName'],
  });
  assert.equal(first.nativeWrites, 0);
  typing(document);
  assert.deepEqual(steps.typeText(document, { 'name--legalName--firstName': 'Alex', 'resume-file': 'x', missing: 'y' }), {
    typed: ['name--legalName--firstName'],
    failed: ['resume-file', 'missing'],
  });
  assert.equal(first.value, 'Alex');
  assert.equal(first.inserted, 1);
  assert.deepEqual(resume.files, []);
});

test('workday fill adapter has no click; only the steps adapter moves the tab', () => {
  const fill = fs.readFileSync(path.join(ADAPTER_ROOT, 'workday.cjs'), 'utf8');
  assert.doesNotMatch(fill, /\.click\s*\(|requestSubmit|\.submit\s*\(/);
  const stepsSource = fs.readFileSync(path.join(ADAPTER_ROOT, 'workday_steps.cjs'), 'utf8');
  assert.doesNotMatch(stepsSource, /requestSubmit|\.submit\s*\(/);
  const steps = load('workday_steps');
  assert.equal(typeof steps.navigate, 'function');
  assert.equal(typeof steps.syncRecords, 'function');
  assert.equal(typeof load('workday').navigate, 'undefined');
});
