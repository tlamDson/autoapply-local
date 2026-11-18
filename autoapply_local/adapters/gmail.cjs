'use strict';
// Read-only helpers for the Gmail tab of the tool's own Chrome profile. They
// move the tab to a Gmail search (built by gmail_tab.search_query) by changing
// location.hash, and read the visible result rows. Nothing here opens, changes or acts on a message.
(function install(root) {
  const clean = text => String(text || '').replace(/\s+/g, ' ').trim();
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function searchHash(q, page = 1) {
    return '#search/' + encodeURIComponent(q).replace(/%20/g, '+') + (page > 1 ? '/p' + page : '');
  }

  // Gmail keeps hidden copies of earlier views; only the visible one counts.
  const view = document => [...document.querySelectorAll('div[role=main]')].find(main => main.offsetParent !== null);

  function rows(document) {
    const main = view(document);
    if (!main) return [];
    return [...main.querySelectorAll('tr.zA')].map(row => {
      const sender = row.querySelector('[email]');
      const thread = row.querySelector('[data-legacy-thread-id]') || row.querySelector('[data-thread-id]');
      const when = row.querySelector('.xW');
      return {
        thread: thread ? thread.getAttribute('data-legacy-thread-id') || thread.getAttribute('data-thread-id') : '',
        from: clean(sender && (sender.getAttribute('name') || sender.innerText)),
        email: (sender && sender.getAttribute('email')) || '',
        subject: clean(row.querySelector('.bog')?.innerText),
        snippet: clean(row.querySelector('.y2')?.innerText).replace(/^[-–]\s*/, ''),
        date: (when && when.querySelector('span[title]')?.getAttribute('title')) || '',
        unread: row.classList.contains('zE'),
      };
    });
  }

  function isEmpty(document) {
    const main = view(document);
    return Boolean(main && main.querySelector('td.TC'));
  }

  const signature = document => (isEmpty(document) ? 'EMPTY' : '') + rows(document).map(row => row.thread + row.subject).join('|');

  // Wait until the list differs from the previous view (or Gmail says no
  // messages) and has stayed the same for `settle` milliseconds.
  async function search(win, q, { page = 1, ms = 15000, settle = 1500, tick = 500 } = {}) {
    const document = win.document;
    const target = searchHash(q, page);
    const before = win.location.hash === target ? null : signature(document);
    win.location.hash = target;
    const start = Date.now();
    let last = null;
    let since = Date.now();
    while (Date.now() - start < ms) {
      const current = signature(document);
      if (current !== last) {
        last = current;
        since = Date.now();
      }
      const ready = (before === null || current !== before) && (current.startsWith('EMPTY') || rows(document).length > 0);
      if (ready && Date.now() - since >= settle) break;
      await sleep(tick);
    }
    const empty = isEmpty(document);
    return { q, page, empty, rows: empty ? [] : rows(document) };
  }

  const api = { searchHash, rows, isEmpty, search };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyGmail = api;
})(globalThis);
