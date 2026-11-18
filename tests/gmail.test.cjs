'use strict';

// Gmail tab adapter on a synthetic Gmail-like DOM. Invented senders only; no
// browser, network or real mailbox. The adapter reads rows and changes only
// location.hash (a Gmail search); it never clicks, sends or archives.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SOURCE = path.resolve(__dirname, '..', 'autoapply_local', 'adapters', 'gmail.cjs');
const gmail = require(SOURCE);

class Node {
  constructor(tag, { cls = '', attrs = {}, text = '', hidden = false } = {}) {
    this.tagName = tag.toUpperCase();
    this.classList = new Set(cls.split(/\s+/).filter(Boolean));
    this.classList.contains = this.classList.has;
    this.attributes = { ...attrs };
    this.text = text;
    this.children = [];
    this.hidden = hidden;
    this.parent = null;
  }
  add(child) {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  get offsetParent() {
    for (let node = this; node; node = node.parent) if (node.hidden) return null;
    return {};
  }
  get innerText() {
    return [this.text, ...this.children.map(child => child.innerText)].filter(Boolean).join(' ');
  }
  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }
  matches(selector) {
    const head = selector.replace(/\[[^\]]*\]/g, '');
    const tag = head.match(/^[a-z]+/i)?.[0];
    const classes = [...head.matchAll(/\.([\w-]+)/g)].map(match => match[1]);
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    if (classes.some(name => !this.classList.has(name))) return false;
    for (const [, key, value] of selector.matchAll(/\[([\w-]+)(?:=["']?([^\]"']*)["']?)?\]/g)) {
      if (!(key in this.attributes)) return false;
      if (value !== undefined && this.attributes[key] !== value) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    assert.ok(!/\s/.test(selector.trim()), `fixture supports simple selectors only: ${selector}`);
    const found = [];
    for (const child of this.children) {
      if (child.matches(selector)) found.push(child);
      found.push(...child.querySelectorAll(selector));
    }
    return found;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  click() {
    throw new Error('The Gmail adapter must never click');
  }
}

function row({ thread, name, email, subject, snippet, date, unread = false }) {
  const tr = new Node('tr', { cls: unread ? 'zA zE' : 'zA zF' });
  const sender = tr.add(new Node('td')).add(new Node('span', { attrs: { email, name }, text: name }));
  sender.parent.add(new Node('span', { attrs: { 'data-legacy-thread-id': thread } }));
  const body = tr.add(new Node('td'));
  body.add(new Node('span', { cls: 'bog', text: subject }));
  body.add(new Node('span', { cls: 'y2', text: ' - ' + snippet }));
  tr.add(new Node('td', { cls: 'xW' })).add(new Node('span', { attrs: { title: date }, text: 'Nov 10' }));
  return tr;
}

function mailbox(pages) {
  const document = new Node('html');
  const hidden = document.add(new Node('div', { attrs: { role: 'main' }, hidden: true }));
  hidden.add(row({ thread: 'stale', name: 'Hidden', email: 'x@example.test', subject: 'Old view', snippet: '', date: '' }));
  const main = document.add(new Node('div', { attrs: { role: 'main' } }));
  const show = list => {
    main.children = [];
    const table = main.add(new Node('table'));
    if (!list.length) table.add(new Node('td', { cls: 'TC', text: 'No messages matched your search.' }));
    for (const item of list) table.add(row(item));
  };
  show([]);
  const win = {
    document,
    location: {
      _hash: '#inbox',
      get hash() {
        return this._hash;
      },
      set hash(value) {
        this._hash = value;
        win.hashes.push(value);
        const page = Number((value.match(/\/p(\d+)$/) || [])[1] || 1);
        show(pages[page - 1] || []);
      },
    },
    hashes: [],
  };
  return win;
}

const CONFIRM = {
  thread: '18c0a1',
  name: 'Northwind Labs Recruiting',
  email: 'no-reply@northwind.example',
  subject: 'Thank you for applying to Northwind Labs',
  snippet: 'We received your application for Software Engineer Intern.',
  date: 'Tue, Nov 10, 2026, 9:14 AM',
};
const VERIFY = {
  thread: '18c0a2',
  name: 'Contoso Careers',
  email: 'careers@contoso.example',
  subject: 'Verify your candidate account',
  snippet: 'Your security code is below.',
  date: 'Wed, Nov 11, 2026, 8:00 AM',
  unread: true,
};

test('rows reads the visible list only, with thread id, sender, subject, snippet and date', () => {
  const win = mailbox([[CONFIRM, VERIFY]]);
  win.location.hash = '#search/x';
  const rows = gmail.rows(win.document);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0], {
    thread: '18c0a1',
    from: 'Northwind Labs Recruiting',
    email: 'no-reply@northwind.example',
    subject: 'Thank you for applying to Northwind Labs',
    snippet: 'We received your application for Software Engineer Intern.',
    date: 'Tue, Nov 10, 2026, 9:14 AM',
    unread: false,
  });
  assert.equal(rows[1].unread, true);
  assert.equal(gmail.isEmpty(win.document), false);
});

test('query covers six months of confirmation subjects in every folder', () => {
  const q = gmail.query({ kind: 'confirm', days: 183 });
  assert.match(q, /^in:anywhere newer_than:183d /);
  assert.match(q, /subject:application/);
  assert.match(q, /"received your application"/);
  assert.doesNotMatch(q, /from:/);
});

test('query for one company names it as phrase and sender word', () => {
  const q = gmail.query({ kind: 'confirm', company: "Fabrikam's Goods", days: 7 });
  assert.match(q, /newer_than:7d/);
  assert.match(q, /"Fabrikam's Goods"/);
  assert.match(q, /from:fabrikamsgoods/);
  const verify = gmail.query({ kind: 'verify', company: 'Contoso', days: 1 });
  assert.match(verify, /subject:verify/);
  assert.match(verify, /"security code"/);
});

test('searchHash encodes the query and the result page', () => {
  assert.equal(gmail.searchHash('in:anywhere "a b"', 1), '#search/in%3Aanywhere+%22a+b%22');
  assert.equal(gmail.searchHash('x', 3), '#search/x/p3');
});

test('search moves to the results page and waits for the list to settle', async () => {
  const win = mailbox([[CONFIRM], [VERIFY]]);
  const first = await gmail.search(win, 'q', { page: 1, tick: 1, settle: 3, ms: 500 });
  assert.equal(first.page, 1);
  assert.equal(first.empty, false);
  assert.deepEqual(first.rows.map(item => item.thread), ['18c0a1']);
  const second = await gmail.search(win, 'q', { page: 2, tick: 1, settle: 3, ms: 500 });
  assert.deepEqual(second.rows.map(item => item.thread), ['18c0a2']);
  const past = await gmail.search(win, 'q', { page: 3, tick: 1, settle: 3, ms: 500 });
  assert.equal(past.empty, true);
  assert.deepEqual(past.rows, []);
  assert.deepEqual(win.hashes, ['#search/q', '#search/q/p2', '#search/q/p3']);
});

test('adapter source is read-only: no click, send, draft, archive, label or delete', () => {
  const source = fs.readFileSync(SOURCE, 'utf8').replace(/^\s*(\/\/|\/\*|\*).*$/gm, '');
  for (const banned of [/\.click\s*\(/, /dispatchEvent/, /\bsend\b/i, /draft/i, /archive/i, /label/i, /delete/i, /submit/i]) {
    assert.doesNotMatch(source, banned);
  }
});
