/*
 * Finds the LMS's SCORM 1.2 API and wraps it safely.
 *
 * Implemented from the discovery algorithm published in the ADL SCORM 1.2
 * Run-Time Environment book rather than adapted from an existing wrapper: the
 * usual one (pipwerks) ships no LICENSE file, only a claim in a source header,
 * and vendoring it would stamp a third-party copyright into every package this
 * engine emits into every tenant's LMS. The algorithm itself is ADL's and is
 * published to be implemented.
 *
 * Everything the API returns is a STRING. That is the single biggest source of
 * silent data loss here, and it is why isTrue() and lastError() exist: the
 * obvious `if (api.LMSGetLastError())` is TRUE for the string "0", i.e. it
 * reports an error exactly when there isn't one.
 */
(function (global) {
  "use strict";

  // ADL's SCORM 1.2 algorithm bails at 7 parents; its 2004 version uses 500.
  // We take 500 — strictly more permissive, and deeply nested LMS frames are
  // real. Stated here so nobody "corrects" it back later.
  var MAX_PARENTS = 500;

  function scan(win) {
    var tries = 0;
    if (!win) return null;
    try {
      while (!win.API && win.parent && win.parent !== win && tries <= MAX_PARENTS) {
        tries += 1;
        win = win.parent;
      }
      return win.API || null;
    } catch (e) {
      // A cross-origin ancestor throws on property access. That means
      // "not found", never "crash the quiz before it renders".
      return null;
    }
  }

  // SCORM 1.2 is window.API. window.API_1484_11 is SCORM 2004 — looking for it
  // here would find nothing, and finding it would be wrong.
  function findAPI() {
    var found = scan(global);
    try {
      if (!found && global.parent && global.parent !== global) {
        found = scan(global.parent);
      }
      if (!found && global.top && global.top.opener) {
        found = scan(global.top.opener);
      }
    } catch (e) {
      /* cross-origin: fall through to null */
    }
    return found;
  }

  function isTrue(value) {
    return /^(true|1)$/i.test(String(value));
  }

  function Scorm() {
    this.api = findAPI();
    this.connected = false;
    this.terminated = false;
    this.errors = [];
  }

  Scorm.prototype.available = function () {
    return this.api !== null;
  };

  Scorm.prototype.lastError = function () {
    if (!this.api) return 0;
    var code = parseInt(this.api.LMSGetLastError(), 10);
    return isNaN(code) ? 0 : code;
  };

  Scorm.prototype.initialize = function () {
    if (!this.api || this.connected) return false;
    // The empty string is required, not decorative. Moodle guards on
    // `param == ""`, and calling LMSInitialize() with no argument passes
    // undefined, which is NOT == "" — so it errors 201 and returns "false".
    // Open edX's shim takes no parameter and always returns "true", so this
    // mistake is invisible there and only ever bites on Moodle.
    this.connected = isTrue(this.api.LMSInitialize(""));
    if (!this.connected) this.record("LMSInitialize");
    return this.connected;
  };

  Scorm.prototype.get = function (element) {
    if (!this.api || !this.connected || this.terminated) return "";
    var value = this.api.LMSGetValue(element);
    if (this.lastError() !== 0) this.record("LMSGetValue(" + element + ")");
    return value;
  };

  Scorm.prototype.set = function (element, value) {
    if (!this.api || !this.connected || this.terminated) return false;
    var ok = isTrue(this.api.LMSSetValue(element, String(value)));
    // A refused write is the only way a 405 ever surfaces. Left unchecked, a
    // malformed value is simply discarded and the report is quietly empty.
    if (!ok) this.record("LMSSetValue(" + element + ", " + value + ")");
    return ok;
  };

  Scorm.prototype.commit = function () {
    if (!this.api || !this.connected || this.terminated) return false;
    var ok = isTrue(this.api.LMSCommit(""));
    if (!ok) this.record("LMSCommit");
    return ok;
  };

  Scorm.prototype.finish = function () {
    if (!this.api || !this.connected || this.terminated) return false;
    // Commit before finishing. SCORM 1.2 guarantees no implicit commit on
    // LMSFinish; Moodle happens to persist anyway, but that is its generosity,
    // not the contract.
    this.commit();
    var ok = isTrue(this.api.LMSFinish(""));
    // After finishing, every further call errors 301 and the value is dropped,
    // so latch it rather than letting later code write into a void.
    this.terminated = true;
    this.connected = false;
    if (!ok) this.record("LMSFinish");
    return ok;
  };

  Scorm.prototype.record = function (what) {
    var code = this.lastError();
    if (code === 0) return;
    var message = what + " -> error " + code;
    this.errors.push(message);
    if (global.console && global.console.warn) global.console.warn("[scorm] " + message);
  };

  global.Scorm = Scorm;
})(window);
