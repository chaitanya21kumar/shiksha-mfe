/*
 * The SCO: renders the questions, grades them, and reports to the LMS.
 *
 * The answer key ships inside the package, so grading happens here rather than in
 * the LMS. That is not a compromise — it is why SCORM can honour the contract's
 * per-question `points` exactly, where H5P cannot express a per-question weight
 * at all and has to score on its own scale.
 *
 * What actually reaches a gradebook is only: score.raw (a percentage),
 * lesson_status, session_time, and the interactions. Everything else the contract
 * carries — explanations, rubric bands, per-choice feedback, tips — has no CMI
 * slot and is rendered here instead.
 */
(function () {
  "use strict";

  var data = JSON.parse(document.getElementById("assessment-data").textContent);
  var scorm = new window.Scorm();
  var launchedAt = Date.now();
  var responses = {};
  var finished = false;

  // --- reporting ------------------------------------------------------------

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  // CMITimespan: HHHH:MM:SS.SS. The hours must be at least two digits — Moodle's
  // regex is ^([0-9]{2,4}): and would reject "0:12:30", i.e. every session
  // shorter than ten hours.
  function timespan(seconds) {
    var cs = Math.max(0, Math.round(seconds * 100));
    var h = Math.floor(cs / 360000);
    var m = Math.floor((cs % 360000) / 6000);
    var s = Math.floor((cs % 6000) / 100);
    var rest = cs % 100;
    return pad(Math.min(h, 9999)) + ":" + pad(m) + ":" + pad(s) + "." + pad(rest);
  }

  // CMITime: a time of DAY, not a duration. cmi.interactions.n.time wants this;
  // cmi.interactions.n.latency wants a timespan. Swapping them fails Moodle's
  // regex.
  function timeOfDay(date) {
    return (
      pad(date.getHours()) +
      ":" +
      pad(date.getMinutes()) +
      ":" +
      pad(date.getSeconds()) +
      "." +
      pad(Math.floor(date.getMilliseconds() / 10))
    );
  }

  function scoreString(earned, possible) {
    if (possible <= 0) return "0";
    var pct = Math.max(0, Math.min(100, (earned / possible) * 100));
    return String(Math.round(pct * 100) / 100);
  }

  // --- grading --------------------------------------------------------------

  function normalise(text, caseSensitive) {
    var value = String(text == null ? "" : text).trim();
    return caseSensitive ? value : value.toLowerCase();
  }

  function gradeMcq(question, answer) {
    var picked = (answer || []).slice().sort().join(",");
    var correct = question.choices
      .filter(function (c) {
        return c.is_correct;
      })
      .map(function (c) {
        return c.id;
      })
      .sort()
      .join(",");
    // All-or-nothing: a partially-correct multi-select is not correct.
    return picked !== "" && picked === correct ? question.points : 0;
  }

  function gradeMatch(question, answer) {
    var got = 0;
    question.sources.forEach(function (source) {
      if ((answer || {})[source.id] === source.target_id) got += 1;
    });
    // Proportional — a match question is several judgements, not one.
    return question.sources.length ? (question.points * got) / question.sources.length : 0;
  }

  function gradeBlanks(question, answer) {
    var got = 0;
    question.blanks.forEach(function (blank) {
      var typed = normalise((answer || {})[blank.id], question.case_sensitive);
      var hit = blank.answers.some(function (accepted) {
        return normalise(accepted, question.case_sensitive) === typed && typed !== "";
      });
      if (hit) got += 1;
    });
    return question.blanks.length ? (question.points * got) / question.blanks.length : 0;
  }

  function grade(question) {
    var answer = responses[question.id];
    if (question.type === "mcq") return gradeMcq(question, answer);
    if (question.type === "match") return gradeMatch(question, answer);
    return gradeBlanks(question, answer);
  }

  // --- rendering ------------------------------------------------------------

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderMcq(question, into) {
    var multi = !question.single_answer;
    responses[question.id] = [];
    question.choices.forEach(function (choice) {
      var row = el("label", "choice");
      var input = document.createElement("input");
      input.type = multi ? "checkbox" : "radio";
      input.name = question.id;
      input.value = choice.id;
      input.addEventListener("change", function () {
        if (multi) {
          responses[question.id] = Array.prototype.slice
            .call(into.querySelectorAll("input:checked"))
            .map(function (i) {
              return i.value;
            });
        } else {
          responses[question.id] = [choice.id];
        }
      });
      row.appendChild(input);
      row.appendChild(el("span", null, choice.text));
      into.appendChild(row);
    });
  }

  function renderMatch(question, into) {
    responses[question.id] = {};
    var targets = question.targets.slice();
    question.sources.forEach(function (source) {
      var row = el("div", "pair");
      row.appendChild(el("span", "term", source.text));
      var select = document.createElement("select");
      select.appendChild(new Option("— choose —", ""));
      targets.forEach(function (target) {
        select.appendChild(new Option(target.text, target.id));
      });
      select.addEventListener("change", function () {
        responses[question.id][source.id] = select.value;
      });
      row.appendChild(select);
      into.appendChild(row);
    });
  }

  function renderBlanks(question, into) {
    responses[question.id] = {};
    var wrap = el("p", "sentence");
    var parts = question.text.split(/(\[\[\d+\]\])/);
    parts.forEach(function (part) {
      var marker = part.match(/^\[\[(\d+)\]\]$/);
      if (!marker) {
        wrap.appendChild(document.createTextNode(part));
        return;
      }
      var blank = question.blanks[parseInt(marker[1], 10) - 1];
      if (!blank) return;
      var input = document.createElement("input");
      input.type = "text";
      input.className = "blank";
      if (blank.tip) input.title = blank.tip;
      input.addEventListener("input", function () {
        responses[question.id][blank.id] = input.value;
      });
      wrap.appendChild(input);
    });
    into.appendChild(wrap);
  }

  function render() {
    var root = document.getElementById("quiz");
    data.questions.forEach(function (question, index) {
      var card = el("section", "question");
      card.appendChild(el("h2", null, "Question " + (index + 1)));
      if (question.prompt) card.appendChild(el("p", "prompt", question.prompt));
      if (question.has_latex) {
        // SCORM has no maths support and no LMS supplies a renderer, so the
        // source is shown as-is rather than the question being withheld.
        card.appendChild(el("p", "latex-note", "This question contains LaTeX, shown as written."));
      }
      var body = el("div", "body");
      if (question.type === "mcq") renderMcq(question, body);
      else if (question.type === "match") renderMatch(question, body);
      else renderBlanks(question, body);
      card.appendChild(body);
      root.appendChild(card);
    });
  }

  // --- results --------------------------------------------------------------

  function bandFor(pct) {
    var bands = data.score_bands || [];
    for (var i = 0; i < bands.length; i += 1) {
      if (pct >= bands[i].from_percent && pct <= bands[i].to_percent) return bands[i].feedback;
    }
    return "";
  }

  function showResults(earned, pct, passed) {
    document.getElementById("quiz").hidden = true;
    document.getElementById("submit").hidden = true;
    var out = document.getElementById("results");
    out.hidden = false;
    out.appendChild(el("h2", null, "Your result"));
    out.appendChild(
      el("p", "score", Math.round(pct) + "% — " + earned.toFixed(2) + " of " + data.max_points)
    );
    out.appendChild(el("p", passed ? "verdict pass" : "verdict fail", passed ? "Passed" : "Not passed"));
    var band = bandFor(pct);
    if (band) out.appendChild(el("p", "band", band));

    data.questions.forEach(function (question, index) {
      if (!question.explanation) return;
      var row = el("div", "explanation");
      row.appendChild(el("strong", null, "Question " + (index + 1) + ": "));
      row.appendChild(document.createTextNode(question.explanation));
      out.appendChild(row);
    });
  }

  // --- submit ---------------------------------------------------------------

  // What the learner actually answered, encoded the way SCORM 1.2 expects. The
  // emitter stamped each option with its single character, so this never has to
  // reimplement the alphabet — it just reads `char` back off the data.
  function learnerResponse(question, interaction) {
    var answer = responses[question.id];
    if (question.type === "mcq") {
      var picked = (answer || []).slice();
      return question.choices
        .filter(function (c) {
          return picked.indexOf(c.id) !== -1;
        })
        .map(function (c) {
          return c.char;
        })
        .join(","); // plain comma. "[,]" is SCORM 2004 and is wrong here.
    }
    if (question.type === "match") {
      var byId = {};
      question.targets.forEach(function (t) {
        byId[t.id] = t.char;
      });
      return question.sources
        .filter(function (s) {
          return (answer || {})[s.id];
        })
        .map(function (s) {
          // period WITHIN a pair, comma BETWEEN pairs
          return s.char + "." + byId[(answer || {})[s.id]];
        })
        .join(",");
    }
    return String((answer || {})[interaction.id] || "");
  }

  function reportInteractions() {
    var n = 0;
    var now = new Date();
    data.questions.forEach(function (question) {
      var scored = grade(question);
      (question.interactions || []).forEach(function (interaction) {
        var base = "cmi.interactions." + n + ".";
        // .id is mandatory in practice: Moodle's report derives its question
        // count solely from rows matching cmi.interactions_%.id, so omitting it
        // leaves the report blank no matter what else was written.
        scorm.set(base + "id", interaction.id || question.id);
        scorm.set(base + "type", interaction.type);
        interaction.correct_responses.forEach(function (pattern, i) {
          scorm.set(base + "correct_responses." + i + ".pattern", pattern);
        });
        var given = learnerResponse(question, interaction);
        scorm.set(base + "student_response", given);
        var correct =
          interaction.type === "fill-in"
            ? blankIsCorrect(question, interaction.id)
            : scored >= question.points;
        // We graded it, so we say so — SCORM 1.2's fill-in pattern cannot carry
        // case-sensitivity (that is 2004's {case_matters=}), and .result is where
        // our verdict actually lives.
        scorm.set(base + "result", correct ? "correct" : "wrong");
        scorm.set(base + "weighting", interaction.weighting);
        // CMITime here is a time of DAY, not a duration.
        scorm.set(base + "time", timeOfDay(now));
        n += 1;
      });
    });
  }

  function blankIsCorrect(question, blankId) {
    var blank = null;
    question.blanks.forEach(function (b) {
      if (b.id === blankId) blank = b;
    });
    if (!blank) return false;
    var typed = normalise((responses[question.id] || {})[blank.id], question.case_sensitive);
    return (
      typed !== "" &&
      blank.answers.some(function (accepted) {
        return normalise(accepted, question.case_sensitive) === typed;
      })
    );
  }

  function submit() {
    if (finished) return;
    finished = true;

    var earned = 0;
    data.questions.forEach(function (question) {
      earned += grade(question);
    });
    var pct = data.max_points > 0 ? (earned / data.max_points) * 100 : 0;
    var passed = pct >= data.pass_percentage;

    if (scorm.connected) {
      reportInteractions();
      scorm.set("cmi.core.score.min", "0");
      scorm.set("cmi.core.score.max", "100");
      // Normalised 0-100. This is normative in SCORM 1.2, not a convention:
      // Moodle enforces the range and Open edX divides by 100 while ignoring
      // score.max entirely, so a raw of 850/1000 would grade as 850% there.
      scorm.set("cmi.core.score.raw", scoreString(earned, data.max_points));
      scorm.set("cmi.core.lesson_status", passed ? "passed" : "failed");
      scorm.set("cmi.core.session_time", timespan((Date.now() - launchedAt) / 1000));
      scorm.set("cmi.core.exit", ""); // "" IS a normal exit; "normal" is invalid
      scorm.finish();
    }

    showResults(earned, pct, passed);
  }

  // --- boot -----------------------------------------------------------------

  render();
  document.getElementById("submit").addEventListener("click", submit);

  if (scorm.available() && scorm.initialize()) {
    var entry = scorm.get("cmi.core.entry");
    if (entry !== "resume") {
      // Never echo lesson_status back: "not attempted" is readable but NOT
      // writable, so a SCO that reads and re-writes it fails on its first write.
      scorm.set("cmi.core.lesson_status", "incomplete");
    }
    scorm.commit();
  } else {
    // Loud to the learner, quiet in the console: they still get the content, and
    // nobody is misled into believing a score was recorded.
    var banner = document.getElementById("banner");
    banner.hidden = false;
    banner.textContent = "Not connected to an LMS — your results will not be saved.";
  }

  window.addEventListener("pagehide", function () {
    if (finished || !scorm.connected) return;
    scorm.set("cmi.core.exit", "suspend");
    scorm.set("cmi.core.session_time", timespan((Date.now() - launchedAt) / 1000));
    scorm.finish();
  });
})();
