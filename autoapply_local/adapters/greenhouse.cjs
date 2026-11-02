'use strict';
(function install(root) {
  const api = typeof module !== 'undefined' && module.exports ? require('./common.cjs') : root.AutoApplyDOM;
  if (!api) throw new Error('Load common.cjs before the ATS adapter');
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.AutoApplyGreenhouse = api;
})(globalThis);
