/*
 * Reports a micro-lesson's progress to a SCORM 1.2 LMS.
 *
 * The deck itself knows nothing about any of this. It exposes one hook —
 * window.LessonDeck.onSlide(index) — and everything below hangs off that, so the
 * standalone HTML file and the SCO run identical presentation code.
 *
 * What a lesson can honestly report is narrower than what a quiz can, and the
 * choices here follow from that:
 *
 * - There is no score. A lesson asks nothing, so cmi.core.score.* is left unset
 *   rather than reporting 0/0, which some LMSs render as a failed attempt.
 * - Completion means "reached the last slide". It is the only signal the content
 *   actually has. Time spent is not used as a proxy: a learner who leaves the tab
 *   open has not read anything.
 * - lesson_status starts at "incomplete" and becomes "completed". "passed" and
 *   "failed" are never written, because nothing here is being judged.
 *
 * Resume is real, not decorative. The furthest slide reached is stored in
 * cmi.core.lesson_location, and re-entry jumps there. An LMS is free to ignore it,
 * which is why the value is validated on the way back in rather than trusted.
 */
(function (global) {
  "use strict";

  var scorm = new global.Scorm();
  var deck = global.LessonDeck;
  if (!deck) return;

  var started = Date.now();
  var furthest = 0;
  var finished = false;

  function total() {
    return deck.total || 1;
  }

  /* SCORM 1.2 session_time is CMITimespan: HHHH:MM:SS.SS, and the hour field
   * needs at least two digits. Getting this wrong is the classic silent failure —
   * a malformed value is rejected by the LMS and the whole session reports zero. */
  function timespan(ms) {
    var s = Math.max(0, Math.floor(ms / 1000));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    function pad(n) {
      return (n < 10 ? "0" : "") + n;
    }
    return pad(h) + ":" + pad(m) + ":" + pad(sec);
  }

  function report(index) {
    if (index > furthest) furthest = index;
    scorm.set("cmi.core.lesson_location", String(furthest));

    if (!finished && furthest >= total() - 1) {
      finished = true;
      scorm.set("cmi.core.lesson_status", "completed");
    }
    scorm.commit();
  }

  function close(exit) {
    scorm.set("cmi.core.session_time", timespan(Date.now() - started));
    scorm.set("cmi.core.exit", exit);
    scorm.commit();
    scorm.finish();
  }

  if (scorm.initialize()) {
    /* An LMS that has seen this learner before reports "completed" or one of the
     * pass/fail values. Only "not attempted" and the empty string mean a fresh
     * start, and writing "incomplete" over a completed status would take a
     * finished lesson away from someone. */
    var status = scorm.get("cmi.core.lesson_status");
    if (!status || status === "not attempted") {
      scorm.set("cmi.core.lesson_status", "incomplete");
    } else if (status === "completed" || status === "passed") {
      finished = true;
    }

    /* Resume. The stored value came from an LMS and is a string of unknown shape,
     * so it is parsed and range-checked before it is allowed to move the deck. */
    var saved = parseInt(scorm.get("cmi.core.lesson_location"), 10);
    if (!isNaN(saved) && saved > 0 && saved < total()) {
      furthest = saved;
      deck.go(saved);
    }

    deck.onSlide = report;
    report(deck.current());

    /* pagehide rather than unload: unload does not fire reliably on mobile Safari
     * or when a tab is discarded, and a missed finish leaves the attempt open. */
    global.addEventListener("pagehide", function () {
      if (!finished) close("suspend");
      else close("");
    });
  }
})(window);
