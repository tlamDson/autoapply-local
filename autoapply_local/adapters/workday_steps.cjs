'use strict';

// Workday steps that move the held tab: the posting's Apply, Apply Manually,
// sign-in, create-account, the verification-code page and the footer Next.
// Every click of the apply flow goes through press(): it clicks only the
// control of a fixed action whose text matches that action, never a control
// whose text or label says Submit, and Next only on a form step before Review.
// Typed values (credentials, code, names) are never returned, and the
// credential and code are typed only on the page and tenant host the caller
// checked. The tenant's account terms box is never ticked.
(function install(root) {
  const base = typeof module !== 'undefined' && module.exports ? require('./workday.cjs') : root.AutoApplyWorkday;
  if (!base) throw new Error('Load workday.cjs before workday_steps.cjs');
  const clean = text => String(text || '').replace(/\s+/g, ' ').trim();
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const byAutomation = (document, id) => document.querySelector(`[data-automation-id="${id}"]`);
  const SUBMIT = /submit/i;
  const ACTIONS = {
    apply: { ids: ['adventureButton'], text: /^apply( now)?$/i },
    apply_manually: { ids: ['applyManually'], text: /^apply manually$/i },
    sign_in_with_email: { ids: ['SignInWithEmailButton'], text: /^sign in with email$/i },
    create_account_link: { ids: ['createAccountLink'], text: /^create account$/i },
    sign_in: { ids: ['signInSubmitButton'], text: /^sign in$/i },
    create_account: { ids: ['createAccountSubmitButton'], text: /^create account$/i },
    verify: { ids: ['verifyButton'], text: /^(verify|confirm|continue)$/i, anyButton: true },
    next: {
      ids: ['pageFooterNextButton', 'bottom-navigation-next-button'],
      text: /^(next|continue|save and continue)$/i,
      step: true,
    },
  };

  function step(document) {
    const bar = clean(byAutomation(document, 'progressBarActiveStep')?.innerText);
    const source = bar || clean(document.querySelector('main')?.innerText);
    const match = source.match(/^(.*?)[,\s]*current step\s+(\d+)\s+of\s+(\d+)\s*(.*)$/i);
    if (!match) return null;
    return { index: Number(match[2]), total: Number(match[3]), title: clean(match[4]) || clean(match[1]) };
  }

  const codeInput = document =>
    byAutomation(document, 'verificationCode') ||
    [...document.querySelectorAll('input')].find(el =>
      /verification code|one[- ]time (pass)?code|security code|passcode/i.test(
        (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('placeholder') || ''),
      ),
    ) ||
    null;
  const captcha = document =>
    document.querySelector('iframe[src*="captcha"],iframe[title*="captcha"],[data-automation-id="captcha"]');

  function where(document) {
    const text = clean(document.body?.innerText);
    const current = step(document);
    const errors = [...document.querySelectorAll('[data-automation-id="errorMessage"],[role="alert"]')]
      .map(item => clean(item.innerText))
      .filter(Boolean)
      .slice(0, 8);
    const has = id => Boolean(byAutomation(document, id));
    let page = 'unknown';
    if (/you.{0,3}ve already applied|already applied for this job/i.test(text)) page = 'applied';
    else if (captcha(document)) page = 'captcha';
    else if (codeInput(document)) page = 'verify_code';
    else if (has('createAccountSubmitButton')) page = 'create_account';
    else if (has('signInSubmitButton') || has('SignInWithEmailButton')) page = 'sign_in';
    else if (current) page = current.index >= current.total || /^review\b/i.test(current.title) ? 'review' : 'form';
    else if (/verify your (email|account)/i.test(text)) page = 'verify_email';
    else if (has('applyManually')) page = 'start';
    else if (has('adventureButton')) page = 'job';
    return {
      page,
      step: current,
      errors,
      host: hostOf(document),
      create_link: has('createAccountLink'),
      sign_in_with_email: has('SignInWithEmailButton') && !has('signInSubmitButton'),
    };
  }

  const said = el => clean([el.innerText, el.getAttribute('aria-label')].filter(Boolean).join(' '));

  function target(document, action) {
    for (const id of action.ids) {
      const el = byAutomation(document, id);
      if (el) return el;
    }
    if (action.anyButton) return [...document.querySelectorAll('button')].find(el => action.text.test(clean(el.innerText))) || null;
    return null;
  }

  // The one place the apply flow clicks.
  async function press(document, name, { ms = 10000, tick = 50 } = {}) {
    const action = Object.prototype.hasOwnProperty.call(ACTIONS, name) ? ACTIONS[name] : null;
    if (!action) return { pressed: false, reason: 'unknown_action' };
    const el = target(document, action);
    if (!el || el.disabled) return { pressed: false, reason: 'control_not_found' };
    if (SUBMIT.test(said(el))) return { pressed: false, reason: 'submit_control' };
    if (!action.text.test(clean(el.innerText) || clean(el.getAttribute('aria-label')))) {
      return { pressed: false, reason: 'unexpected_control' };
    }
    const before = where(document);
    if (action.step) {
      if (el.type === 'submit') return { pressed: false, reason: 'submit_control' };
      if (before.page !== 'form' || !before.step || before.step.index >= before.step.total) {
        return { pressed: false, reason: 'not_a_form_step' };
      }
    }
    // Workday lays a transparent click_filter over some buttons; it takes the click.
    const filter = el.parentElement?.querySelector('[data-automation-id="click_filter"]');
    (filter && !SUBMIT.test(said(filter)) ? filter : el).click();
    const key = state => state.page + ':' + (state.step ? state.step.index : '');
    const errors = JSON.stringify(before.errors);
    const deadline = Date.now() + ms;
    let now = where(document);
    while (key(now) === key(before) && !(now.errors.length && JSON.stringify(now.errors) !== errors) && Date.now() < deadline) {
      await sleep(tick);
      now = where(document);
    }
    return { pressed: true, moved: key(now) !== key(before), where: now };
  }

  function setter(document, el, value) {
    const view = document.defaultView;
    const prototype = el.tagName === 'TEXTAREA' ? view.HTMLTextAreaElement : view.HTMLInputElement;
    const set = Object.getOwnPropertyDescriptor(prototype.prototype, 'value')?.set;
    if (!set) return false;
    set.call(el, value);
    for (const type of ['input', 'change']) el.dispatchEvent(new view.Event(type, { bubbles: true }));
    return el.value === value;
  }

  // Type the way a keystroke does (React-controlled Workday boxes ignore a plain setter).
  function insert(document, el, value) {
    el.focus?.();
    el.select?.();
    let ok = false;
    try {
      ok = Boolean(document.execCommand && document.execCommand('insertText', false, value));
    } catch {
      ok = false;
    }
    if (!ok || el.value !== value) return false;
    el.dispatchEvent(new document.defaultView.Event('change', { bubbles: true }));
    el.blur?.();
    return true;
  }

  const typable = el => el && ['INPUT', 'TEXTAREA'].includes(el.tagName) && !['file', 'checkbox', 'radio'].includes(el.type);

  function typeText(document, values) {
    const typed = [],
      failed = [];
    for (const [id, value] of Object.entries(values)) {
      const el = base.resolve(document, id);
      (typable(el) && insert(document, el, String(value)) ? typed : failed).push(id);
    }
    return { typed, failed };
  }

  const hostOf = document =>
    String(document.location?.hostname || (typeof location !== 'undefined' ? location.hostname : '') || '').toLowerCase();

  // A write is bound to the page and tenant the caller checked: the tab must
  // still show that page on that host, or nothing is typed.
  function bound(document, page, host) {
    if (where(document).page !== page) return 'wrong_page';
    if (!host || hostOf(document) !== String(host).toLowerCase()) return 'wrong_host';
    return null;
  }

  // Types the stored credential; never ticks the tenant's terms box (the user
  // accepts those), only reports whether it is checked.
  function credentials(document, { email, password, confirm = false, page, host }) {
    const refused = bound(document, confirm ? 'create_account' : 'sign_in', host);
    if (refused || page !== (confirm ? 'create_account' : 'sign_in')) return { typed: [], failed: [], refused: refused || 'wrong_page' };
    const wanted = [
      ['email', String(email), el => ['text', 'email'].includes(el.type)],
      ['password', String(password), el => el.type === 'password'],
      ...(confirm ? [['verifyPassword', String(password), el => el.type === 'password']] : []),
    ];
    const targets = wanted.map(([id, value, kind]) => [id, value, byAutomation(document, id), kind]);
    const wrong = targets.filter(([, , el, kind]) => !(typable(el) && kind(el))).map(([id]) => id);
    if (wrong.length) return { typed: [], failed: wrong, refused: 'unexpected_field' };
    const typed = [],
      failed = [];
    for (const [id, value, el] of targets) (insert(document, el, value) || setter(document, el, value) ? typed : failed).push(id);
    const result = { typed, failed };
    if (confirm) {
      const box = byAutomation(document, 'createAccountCheckbox');
      result.terms = !box ? 'absent' : box.checked ? 'checked' : 'unchecked';
    }
    return result;
  }

  function code(document, { code: value, host } = {}) {
    const refused = bound(document, 'verify_code', host);
    if (refused) return { typed: false, refused };
    const el = codeInput(document);
    const text = String(value);
    if (!(typable(el) && ['text', 'tel', 'number'].includes(el.type))) return { typed: false, refused: 'unexpected_field' };
    return { typed: Boolean(insert(document, el, text) || setter(document, el, text)) };
  }

  async function navigate(document, direction) {
    if (!['next', 'back'].includes(direction)) throw new Error('Navigation must be next or back');
    const button = document.querySelector(
      `[data-automation-id="bottom-navigation-${direction}-button"],[data-automation-id="pageFooter${direction === 'next' ? 'Next' : 'Back'}Button"]`,
    );
    if (!button || button.disabled || button.type === 'submit' || SUBMIT.test(button.innerText)) {
      throw new Error('Navigation requires native MCP interaction; no safe navigation button');
    }
    const previous = base.scan(document).page;
    button.click();
    const deadline = Date.now() + 3000;
    do {
      const current = base.audit(document);
      if (current.errors.length) throw new Error('Navigation validation error: ' + current.errors.join('; '));
      if (current.page.index !== previous.index) return current.page;
      await sleep(25);
    } while (Date.now() < deadline);
    throw new Error('Page transition requires native MCP interaction');
  }

  async function syncRecords(document, kind, records) {
    if (!['education', 'experience'].includes(kind) || !Array.isArray(records))
      throw new Error('Invalid repeated record request');
    const section = [...document.querySelectorAll('section')].find(
      el => el.getAttribute('data-record-kind') === kind || el.getAttribute('data-automation-id') === kind,
    );
    if (!section) throw new Error('Repeated section requires agent interaction');
    const getRecords = () =>
      [...section.querySelectorAll('fieldset')].filter(el => el.getAttribute('data-record-kind') === kind);
    let current = getRecords();
    while (current.length < records.length) {
      const add = section.querySelector('[data-action="add-record"],[data-automation-id="add-button"]');
      if (!add || add.disabled || add.type === 'submit') throw new Error('Add record requires agent interaction');
      const previous = current.length;
      add.click();
      current = getRecords();
      if (current.length <= previous) throw new Error('Wait for ATS DOM update using MCP, then rescan');
    }
    while (current.length > records.length) {
      const remove = current[current.length - 1].querySelector(
        '[data-action="remove-record"],[data-automation-id="delete-button"]',
      );
      if (!remove || remove.disabled || remove.type === 'submit')
        throw new Error('Remove record requires agent interaction');
      const previous = current.length;
      remove.click();
      current = getRecords();
      if (current.length >= previous) throw new Error('Wait for ATS DOM update using MCP, then rescan');
    }
    return base.scan(document);
  }

  const api = { ...base, ACTIONS: Object.keys(ACTIONS), where, press, typeText, credentials, code, navigate, syncRecords };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyWorkdaySteps = api;
})(globalThis);
