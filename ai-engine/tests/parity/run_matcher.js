// Scores a corpus with whichever matcher it is pointed at, and prints the marks.
//
// One runner for both sides of the differential test: the H5P reference and the
// matcher extracted from the player we actually ship. Keeping the scoring and the
// stdin plumbing here means the two matchers are the only thing that differs,
// which is the whole point of the comparison.
//
//   node run_matcher.js <path-to-module-exporting-normalise-and-occursIsolated>

const matcher = require(process.argv[2]);

function score(keywords, answer) {
  const hay = matcher.normalise(answer);
  let total = 0;
  keywords.forEach((group) => {
    const forms = [group.keyword].concat(group.alternatives || []);
    const hit = forms.some((form) => {
      const needle = String(form).trim().toLowerCase();
      return needle !== '' && matcher.occursIsolated(needle, hay);
    });
    if (hit) total += group.options.points * group.options.occurrences;
  });
  return total;
}

const chunks = [];
process.stdin.on('data', (c) => chunks.push(c));
process.stdin.on('end', () => {
  const input = JSON.parse(chunks.join(''));
  process.stdout.write(JSON.stringify(input.answers.map((a) => score(input.keywords, a))));
});
