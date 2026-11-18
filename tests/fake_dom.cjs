'use strict';

// Synthetic DOM shared by the adapter tests: ordinary tag/id/attribute selectors,
// native-setter prototypes and click counters. No browser, network or package dependency.
const assert = require('node:assert/strict');
const path = require('node:path');

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

module.exports = { ADAPTER_ROOT, load, FakeEvent, Element, Input, TextArea, Select, makeDocument, addField };
