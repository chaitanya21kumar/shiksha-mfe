// A verbatim transcription of the matcher inside H5P.Essay 1.5.13 — the version
// the H5P Hub serves — plus H5P.TextUtilities.isIsolated, which it depends on.
//
// This is the reference our Python grader and our SCORM player must both agree
// with. It is checked in so the agreement is a CI property rather than something
// somebody once observed in a browser.
//
// Sources, read from the Hub package:
//   H5P.Essay-1.5/scripts/essay.js        -> getInput, detectExactMatches
//   H5P.TextUtilities-1.3/scripts/...     -> isIsolated, WORD_DELIMITER

var WORD_DELIMITER = /[\s.?!,';"]/g;

// H5P.TextUtilities.isIsolated
function isIsolated(needle, haystack, params) {
  var pos = params.index;
  var pred = pos === 0 ? '' : haystack.charAt(pos - 1).replace(WORD_DELIMITER, '');
  var end = pos + needle.length;
  var succ = end === haystack.length ? '' : haystack.charAt(end).replace(WORD_DELIMITER, '');
  return pred === '' && succ === '';
}

// Essay.prototype.getInput — note the whitespace pass is /\s\s/g, a single
// non-overlapping pass, so it HALVES runs rather than collapsing them.
function getInput(text, linebreakReplacement) {
  return String(text == null ? '' : text)
    .replace(/(\r\n|\r|\n)/g, linebreakReplacement === undefined ? ' ' : linebreakReplacement)
    .replace(/\s\s/g, ' ')
    .toLowerCase();
}

// Essay.prototype.detectExactMatches — it CONSUMES the haystack rather than
// advancing a cursor, so the start of each remainder counts as a word boundary.
function detectExactMatches(needle, haystack) {
  var matches = [];
  var pos = haystack.indexOf(needle);
  var offset = 0;
  while (pos !== -1) {
    if (isIsolated(needle, haystack, { index: pos })) {
      matches.push(offset + pos);
    }
    offset = offset + pos + needle.length;
    haystack = haystack.substr(pos + needle.length);
    pos = haystack.indexOf(needle);
  }
  return matches;
}

// The scoring H5P applies for our emitted params: caseSensitive false,
// occurrences 1, no wildcards, no fuzzy matching.
function score(keywords, answer) {
  var hay = getInput(answer, ' ');
  var total = 0;
  keywords.forEach(function (group) {
    var forms = [group.keyword].concat(group.alternatives || []);
    var hit = forms.some(function (form) {
      var needle = String(form).toLowerCase();
      return needle !== '' && detectExactMatches(needle, hay).length > 0;
    });
    if (hit) total += group.options.points * group.options.occurrences;
  });
  return total;
}

// Driven by the Python test: reads {keywords, answers} on stdin, writes marks.
var chunks = [];
process.stdin.on('data', function (c) { chunks.push(c); });
process.stdin.on('end', function () {
  var input = JSON.parse(chunks.join(''));
  process.stdout.write(JSON.stringify(input.answers.map(function (a) {
    return score(input.keywords, a);
  })));
});
