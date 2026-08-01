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
  var deadline = null; // absolute epoch ms, or null when untimed
  var ticker = null;
  var announcedAt = {}; // thresholds already spoken, so each is announced once

  // --- teacher controls -----------------------------------------------------

  // "always" and "after_submission" behave identically here, and that is a fact
  // about this player rather than an omission: it has no per-question Check
  // button, so there is no earlier moment at which a solution could appear. The
  // two differ only in H5P, which does have one. "never" is the value that
  // changes anything here, and it changes it in showResults.
  function solutionsVisible() {
    return data.solution_visibility !== "never";
  }

  // --- countdown ------------------------------------------------------------
  //
  // One clock for the whole assessment. H5P Question Set has no timer field of
  // any kind, so this player is the only artefact of the two that can honour it.
  //
  // The deadline is stored as an absolute instant, not as "seconds remaining",
  // because a learner who reloads the page must not be handed their time back.
  // cmi.suspend_data is the right home for it: it is the one CMI element that is
  // ours to define and that the LMS persists across a relaunch. Without an LMS
  // (a package opened directly) sessionStorage keeps the same promise for the
  // life of the tab.

  var DEADLINE_KEY = "scorm-deadline:" + (data.assessment_id || "assessment");

  function loadDeadline() {
    var raw = "";
    if (scorm.connected) raw = scorm.get("cmi.suspend_data") || "";
    if (!raw) {
      try {
        raw = window.sessionStorage.getItem(DEADLINE_KEY) || "";
      } catch (e) {
        raw = ""; // storage can be blocked; an untimed run is better than a crash
      }
    }
    var parsed = parseInt(raw, 10);
    return isFinite(parsed) && parsed > 0 ? parsed : null;
  }

  function saveDeadline(at) {
    if (scorm.connected) scorm.set("cmi.suspend_data", String(at));
    try {
      window.sessionStorage.setItem(DEADLINE_KEY, String(at));
    } catch (e) {
      /* blocked storage is not fatal; the LMS copy is the durable one */
    }
  }

  function clockText(remaining) {
    var s = Math.max(0, Math.ceil(remaining / 1000));
    var m = Math.floor(s / 60);
    if (m >= 60) return Math.floor(m / 60) + ":" + pad(m % 60) + ":" + pad(s % 60);
    return m + ":" + pad(s % 60);
  }

  // Announce at a quarter left and again at a tenth, then count the last ten
  // seconds down. Proportional rather than fixed, so a three-minute quiz and an
  // hour-long paper both warn at a point that means something.
  function announce(remaining, total) {
    var marks = [
      { at: total * 0.25, say: "A quarter of your time remains." },
      { at: total * 0.1, say: "Ten per cent of your time remains." },
    ];
    marks.forEach(function (mark, i) {
      if (announcedAt[i] || remaining > mark.at) return;
      announcedAt[i] = true;
      var node = document.getElementById("timer-announce");
      if (node) node.textContent = mark.say;
    });
  }

  function tick() {
    var box = document.getElementById("timer");
    if (!box || deadline === null) return;
    var remaining = deadline - Date.now();
    var total = data.time_limit_seconds * 1000;
    box.textContent = "Time remaining " + clockText(remaining);
    box.className = remaining <= total * 0.1 ? "urgent" : "";
    announce(remaining, total);
    if (remaining > 0) return;
    if (ticker) window.clearInterval(ticker);
    ticker = null;
    box.textContent = "Time is up";
    // Expiry submits whatever is on the page. It deliberately bypasses the
    // minimum-length guard: the guard exists to stop a learner submitting an
    // unfinished answer by accident, and this is not an accident. Refusing to
    // submit here would leave the learner with no score at all, which is a worse
    // outcome than a short answer marked on what it actually contains.
    submit(true);
  }

  function startTimer() {
    if (!data.time_limit_seconds) return;
    var box = document.getElementById("timer");
    if (!box) return;
    box.hidden = false;
    deadline = loadDeadline();
    if (deadline === null) {
      deadline = Date.now() + data.time_limit_seconds * 1000;
      saveDeadline(deadline);
    }
    tick();
    if (deadline !== null && !finished) ticker = window.setInterval(tick, 1000);
  }

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

  // --- short answer ---------------------------------------------------------
  //
  // A port of H5P.Essay's matcher, so the same text scores the same mark whether
  // the tenant imported the H5P package or this one. Two details are copied
  // deliberately, including one that is arguably a bug:
  //
  //   * the double-space replace is a single non-overlapping pass, so it HALVES
  //     runs of whitespace rather than collapsing them ("a    b" -> "a  b").
  //     Writing /\s+/ here would be tidier and would silently disagree with H5P.
  //   * a match only counts when word-isolated, which is what stops
  //     "grassland heats up" from matching the key point "land heats".

  var WORD_DELIMITER = /[\s.?!,';"]/;

  function essayNormalise(text) {
    return String(text == null ? "" : text)
      .replace(/(\r\n|\r|\n)/g, " ")
      // \s\s, not two literal spaces: H5P collapses any whitespace pair, so a tab
      // or non-breaking-space pair collapses there too. Matching on " {2}" would
      // silently disagree on text pasted from a word processor.
      .replace(/\s\s/g, " ")
      .toLowerCase();
  }

  function isIsolated(needle, hay, pos) {
    var before = pos === 0 ? "" : hay.charAt(pos - 1).replace(WORD_DELIMITER, "");
    var end = pos + needle.length;
    var after = end === hay.length ? "" : hay.charAt(end).replace(WORD_DELIMITER, "");
    return before === "" && after === "";
  }

  // Searches the way Essay.detectExactMatches does — by CONSUMING the haystack
  // rather than advancing a cursor. The difference is observable: the start of
  // each remainder counts as a word boundary, so "moremore" matches the needle
  // "more" even though no position in the original string is isolated.
  function occursIsolated(needle, hay) {
    var remaining = hay;
    for (;;) {
      var pos = remaining.indexOf(needle);
      if (pos === -1) return false;
      if (isIsolated(needle, remaining, pos)) return true;
      remaining = remaining.slice(pos + needle.length);
    }
  }

  function pointIsMade(point, answer) {
    if (!point) return false;
    var hay = essayNormalise(answer);
    return point.accepted.some(function (form) {
      var needle = String(form).trim().toLowerCase();
      return needle !== "" && occursIsolated(needle, hay);
    });
  }

  function keyPointById(question, id) {
    var found = null;
    (question.key_points || []).forEach(function (p) {
      if (p.id === id) found = p;
    });
    return found;
  }

  function gradeShortAnswer(question, answer) {
    var got = 0;
    (question.key_points || []).forEach(function (point) {
      if (pointIsMade(point, answer)) got += point.weight;
    });
    return got; // the weights sum to question.points, by contract
  }

  function grade(question) {
    var answer = responses[question.id];
    if (question.type === "mcq") return gradeMcq(question, answer);
    if (question.type === "match") return gradeMatch(question, answer);
    if (question.type === "short_answer") return gradeShortAnswer(question, answer);
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

  function renderShortAnswer(question, into) {
    responses[question.id] = "";
    var area = document.createElement("textarea");
    area.className = "short-answer";
    area.rows = 6;
    area.maxLength = question.max_chars;
    area.placeholder = "Answer in two or three sentences, in your own words.";
    var note = el("p", "answer-note", "");
    note.setAttribute("data-note-for", question.id);
    function updateNote() {
      var length = area.value.trim().length;
      if (question.min_chars > 0 && length < question.min_chars) {
        note.textContent = "At least " + question.min_chars + " characters (" + length + " so far).";
        note.className = "answer-note short";
      } else {
        note.textContent = length + " of " + question.max_chars + " characters.";
        note.className = "answer-note";
      }
    }
    area.addEventListener("input", function () {
      responses[question.id] = area.value;
      updateNote();
    });
    updateNote();
    into.appendChild(area);
    into.appendChild(note);
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
      else if (question.type === "short_answer") renderShortAnswer(question, body);
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

    // The teacher can withhold the answers. Everything below this point reveals
    // part of the key — the marked-up key points, the model answer, the
    // explanations — so it is all gated together.
    //
    // Be honest about what this is: the answer key travels inside the package,
    // because the package grades the learner on the learner's own machine. This
    // hides it from the interface, not from someone reading the file. Genuinely
    // withholding it needs grading to move server-side, which is Module D.
    if (!solutionsVisible()) {
      out.appendChild(
        el("p", "solutions-withheld", "Answers are not shown for this assessment.")
      );
      return;
    }

    // A short answer is marked by looking for specific phrases, so the learner is
    // shown exactly which points were found and what a complete answer looks like.
    // Marking that a learner cannot inspect is not marking they can learn from —
    // and this is also where someone who was right in different words can see why
    // they scored what they did.
    data.questions.forEach(function (question, index) {
      if (question.type !== "short_answer") return;
      var box = el("div", "mark-scheme");
      box.appendChild(el("h3", null, "Question " + (index + 1) + " — how this was marked"));
      var list = el("ul", null);
      question.key_points.forEach(function (point) {
        var made = pointIsMade(point, responses[question.id]);
        var row = el("li", made ? "point made" : "point missed", null);
        // Green and red alone would leave a colour-blind learner unable to read
        // their own result (WCAG 1.4.1), so the outcome is carried by a glyph as
        // well, and spelled out in words for a screen reader. The H5P package
        // marks its feedback rows the same way.
        row.appendChild(el("span", "sr-only", made ? "Point made: " : "Point missed: "));
        var mark = el("span", "mark", made ? "✓" : "✗");
        mark.setAttribute("aria-hidden", "true");
        row.appendChild(mark);
        row.appendChild(document.createTextNode(" " + point.text));
        var hint = made ? point.feedback_hit : point.feedback_miss;
        if (hint) row.appendChild(el("span", "hint", " — " + hint));
        list.appendChild(row);
      });
      box.appendChild(list);
      box.appendChild(el("p", "model-answer-label", "A complete answer:"));
      box.appendChild(el("p", "model-answer", question.model_answer));
      out.appendChild(box);
    });

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
    if (question.type === "short_answer") {
      // The matched phrase, not the learner's prose: it is the evidence that earned
      // the mark, and it is capped at 60 characters by the contract so it can never
      // overflow CMIString255. The prose itself goes to cmi.comments.
      var point = keyPointById(question, interaction.id);
      if (!point) return "";
      var hay = essayNormalise(answer);
      var matched = "";
      point.accepted.forEach(function (form) {
        if (matched) return;
        var needle = String(form).trim().toLowerCase();
        // Same search the grader used, so the reported evidence can never
        // disagree with the mark it was awarded for.
        if (needle !== "" && occursIsolated(needle, hay)) matched = form;
      });
      return matched;
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
        // Dispatch on the QUESTION type, never the interaction type. A short answer
        // also reports as "fill-in", so keying off that would route it into
        // blankIsCorrect, hit `question.blanks` on a question that has none, and
        // throw — losing the whole report while the quiz still rendered and still
        // showed a score.
        var correct =
          question.type === "fill_blank"
            ? blankIsCorrect(question, interaction.id)
            : question.type === "short_answer"
              ? pointIsMade(keyPointById(question, interaction.id), responses[question.id])
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

  // The learner's own words, for provenance. cmi.interactions carries only the
  // matched phrases, so without this a teacher could never see what was actually
  // written. cmi.comments is a single CMIString4096 rather than an array, so this
  // accumulates and writes once; an LMS that refuses the optional element just
  // records nothing, and Scorm.set logs it without throwing.
  function reportTranscript() {
    var written = data.questions
      .filter(function (q) {
        return q.type === "short_answer" && String(responses[q.id] || "").trim() !== "";
      })
      .map(function (q) {
        return q.id + ": " + responses[q.id];
      })
      .join("\n");
    if (!written) return;
    if (written.length > 4096) {
      var cut = 4093;
      // Never slice between a surrogate pair — that would send a lone surrogate to
      // the LMS. Relevant for emoji and for scripts outside the BMP.
      if (/[\uD800-\uDBFF]/.test(written.charAt(cut - 1))) cut -= 1;
      written = written.slice(0, cut) + "...";
    }
    scorm.set("cmi.comments", written);
  }

  // H5P.Essay refuses to submit an answer below behaviour.minimumLength, so this
  // does too — otherwise the same quiz would accept in one package what it rejects
  // in the other.
  function tooShort() {
    var offenders = data.questions.filter(function (q) {
      if (q.type !== "short_answer" || !q.min_chars) return false;
      return String(responses[q.id] || "").trim().length < q.min_chars;
    });
    return offenders.length ? offenders[0] : null;
  }

  function submit(forced) {
    if (finished) return;
    // Only an expiry may skip the minimum-length guard. `forced` is read as a
    // strict boolean because this function is also a click handler, and a
    // MouseEvent is truthy — passing it straight through would let every manual
    // click bypass the guard.
    forced = forced === true;

    var short = forced ? null : tooShort();
    if (short) {
      var note = document.querySelector('[data-note-for="' + short.id + '"]');
      if (note) {
        note.className = "answer-note short";
        note.textContent =
          "Please write at least " + short.min_chars + " characters before submitting.";
        note.scrollIntoView({ block: "center" });
      }
      return;
    }

    finished = true;
    if (ticker) {
      window.clearInterval(ticker);
      ticker = null;
    }
    var clock = document.getElementById("timer");
    if (clock) clock.hidden = true;

    var earned = 0;
    data.questions.forEach(function (question) {
      earned += grade(question);
    });
    var pct = data.max_points > 0 ? (earned / data.max_points) * 100 : 0;
    var passed = pct >= data.pass_percentage;

    if (scorm.connected) {
      reportInteractions();
      reportTranscript();
      scorm.set("cmi.core.score.min", "0");
      scorm.set("cmi.core.score.max", "100");
      // Normalised 0-100. This is normative in SCORM 1.2, not a convention:
      // Moodle enforces the range and Open edX divides by 100 while ignoring
      // score.max entirely, so a raw of 850/1000 would grade as 850% there.
      scorm.set("cmi.core.score.raw", scoreString(earned, data.max_points));
      scorm.set("cmi.core.lesson_status", passed ? "passed" : "failed");
      scorm.set("cmi.core.session_time", timespan((Date.now() - launchedAt) / 1000));
      // "" IS a normal exit; "normal" is invalid. "time-out" is the one other
      // value that applies here, and SCORM 1.2 defines it for exactly this case,
      // so a report can tell a learner who ran out of time from one who finished.
      scorm.set("cmi.core.exit", forced ? "time-out" : "");
      scorm.finish();
    } else if (scorm.available()) {
      // We found an API at boot but cannot write now — the session ended under us.
      // Say so rather than showing a clean score the LMS never received.
      var banner = document.getElementById("banner");
      banner.hidden = false;
      banner.textContent = "The connection to the LMS has ended — this result was not saved.";
    }

    showResults(earned, pct, passed);
  }

  // --- boot -----------------------------------------------------------------

  render();
  // Wrapped, not passed directly: the handler receives a MouseEvent, and `submit`
  // reads its first argument as "the timer expired".
  document.getElementById("submit").addEventListener("click", function () {
    submit(false);
  });

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

  // After the LMS handshake, so a resumed attempt reads its existing deadline out
  // of suspend_data rather than starting a fresh one.
  startTimer();

  window.addEventListener("pagehide", function (event) {
    if (finished || !scorm.connected) return;
    // A pagehide with persisted:true means the page went into the back/forward
    // cache, not that the learner left — and it WILL come back, with all script
    // state intact. Finishing here would latch the session closed, so the learner
    // could return, finish their answer, press Submit, and have every CMI write
    // silently refused while the results screen still congratulated them. Commit
    // what we have and stay connected.
    if (event && event.persisted) {
      scorm.set("cmi.core.session_time", timespan((Date.now() - launchedAt) / 1000));
      scorm.commit();
      return;
    }
    scorm.set("cmi.core.exit", "suspend");
    scorm.set("cmi.core.session_time", timespan((Date.now() - launchedAt) / 1000));
    scorm.finish();
  });
})();
