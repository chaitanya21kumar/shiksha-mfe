// A transcription of the matcher inside H5P.Essay 1.5.13 — the version the H5P Hub
// serves — plus the H5P.TextUtilities.isIsolated it depends on.
//
// This is the reference our Python grader and our SCORM player must both agree
// with, checked in so the agreement is a CI property rather than something
// somebody once watched happen in a browser.
//
// Transcribed from the Hub package:
//   H5P.Essay-1.5/scripts/essay.js    -> getInput, detectExactMatches
//   H5P.TextUtilities-1.3/scripts/... -> isIsolated, WORD_DELIMITER
//
// The declarations are modernised; the ALGORITHM is not. The regexes, the
// non-overlapping whitespace pass and the haystack-consuming search loop are
// exactly H5P's, and those are the parts that decide a mark.

const WORD_DELIMITER = /[\s.?!,';"]/g;

// H5P.TextUtilities.isIsolated
function isIsolated(needle, haystack, params) {
  const pos = params.index;
  const pred = pos === 0 ? '' : haystack.charAt(pos - 1).replace(WORD_DELIMITER, '');
  const end = pos + needle.length;
  const succ = end === haystack.length ? '' : haystack.charAt(end).replace(WORD_DELIMITER, '');
  return pred === '' && succ === '';
}

// Essay.prototype.getInput. The whitespace pass is /\s\s/g — a single
// non-overlapping pass, so it HALVES runs rather than collapsing them.
function normalise(text) {
  return String(text === null || text === undefined ? '' : text)
    .replace(/(\r\n|\r|\n)/g, ' ')
    .replace(/\s\s/g, ' ')
    .toLowerCase();
}

// Essay.prototype.detectExactMatches. It CONSUMES the haystack rather than
// advancing a cursor, so the start of each remainder counts as a word boundary.
function occursIsolated(needle, haystack) {
  let remaining = haystack;
  for (;;) {
    const pos = remaining.indexOf(needle);
    if (pos === -1) return false;
    if (isIsolated(needle, remaining, { index: pos })) return true;
    remaining = remaining.slice(pos + needle.length);
  }
}

module.exports = { normalise, occursIsolated };
