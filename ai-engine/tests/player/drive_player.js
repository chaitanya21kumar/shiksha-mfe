/*
 * Drive the generated SCORM packages in a real browser.
 *
 * The Python tests assert on the bytes we emit. They cannot tell whether the
 * countdown actually counts, whether expiry really submits past the min-length
 * guard, or whether a reload hands the learner their time back. Only running the
 * player does that.
 */
const puppeteer = require("puppeteer-core");
const path = require("path");

const CHROME = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const ROOT = process.argv[2];
if (!ROOT) { console.error("usage: node drive_player.js <unpacked-packages-dir>"); process.exit(2); }
const results = [];

function check(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: "new", args: ["--allow-file-access-from-files"],
  });

  // ---- 1. the countdown actually counts -----------------------------------
  let page = await browser.newPage();
  await page.goto("file://" + path.join(ROOT, "timed", "index.html"), { waitUntil: "networkidle0" });
  const first = await page.$eval("#timer", (e) => e.textContent);
  const visible = await page.$eval("#timer", (e) => !e.hidden);
  await new Promise((r) => setTimeout(r, 2200));
  const second = await page.$eval("#timer", (e) => e.textContent);
  check("timer is shown for a timed assessment", visible, first);
  check("timer counts down", first !== second, `${first} -> ${second}`);

  // ---- 2. a reload does NOT restore the spent time -------------------------
  const beforeReload = await page.$eval("#timer", (e) => e.textContent);
  await page.reload({ waitUntil: "networkidle0" });
  const afterReload = await page.$eval("#timer", (e) => e.textContent);
  const parse = (t) => {
    const m = /(\d+):(\d+)/.exec(t || "");
    return m ? parseInt(m[1], 10) * 60 + parseInt(m[2], 10) : NaN;
  };
  check(
    "reload does not hand the learner their time back",
    parse(afterReload) <= parse(beforeReload),
    `${beforeReload} -> ${afterReload}`
  );

  // ---- 3. expiry submits, even with an under-length short answer -----------
  // Wind the stored deadline to just ahead of now, then reload: the same code
  // path a learner hits, without waiting out the real limit.
  await page.evaluate(() => {
    const k = Object.keys(window.sessionStorage).find((x) => x.indexOf("scorm-deadline:") === 0);
    window.sessionStorage.setItem(k, String(Date.now() + 2500));
  });
  await page.reload({ waitUntil: "networkidle0" });
  // Type one character into the short answer: below min_chars, so the guard
  // would normally refuse to submit.
  const box = await page.$("textarea, input[type=text]");
  if (box) await box.type("x");
  const blockedByGuard = await page.evaluate(() => {
    document.getElementById("submit").click();
    return document.getElementById("results").hidden;
  });
  check("the min-length guard still blocks a manual submit", blockedByGuard);

  await new Promise((r) => setTimeout(r, 4000));
  const expired = await page.evaluate(() => ({
    results: !document.getElementById("results").hidden,
    clock: (document.getElementById("timer") || {}).textContent || "",
    hiddenClock: (document.getElementById("timer") || {}).hidden,
  }));
  check("expiry submits past the guard", expired.results, JSON.stringify(expired));
  await page.close();

  // ---- 4. answers withheld -------------------------------------------------
  page = await browser.newPage();
  await page.goto("file://" + path.join(ROOT, "withheld", "index.html"), { waitUntil: "networkidle0" });
  await page.evaluate(() => {
    document.querySelectorAll("textarea").forEach((t) => {
      t.value = "the water cycle moves water between ocean atmosphere and land continuously";
      t.dispatchEvent(new Event("input", { bubbles: true }));
    });
    document.getElementById("submit").click();
  });
  const withheld = await page.evaluate(() => ({
    shown: !document.getElementById("results").hidden,
    text: document.getElementById("results").textContent,
    modelAnswers: document.querySelectorAll(".model-answer").length,
    markSchemes: document.querySelectorAll(".mark-scheme").length,
    explanations: document.querySelectorAll(".explanation").length,
  }));
  check("withheld: the result still appears", withheld.shown);
  check("withheld: no model answer is rendered", withheld.modelAnswers === 0);
  check("withheld: no mark scheme is rendered", withheld.markSchemes === 0);
  check("withheld: no explanation is rendered", withheld.explanations === 0);
  check(
    "withheld: the learner is told answers are not shown",
    /not shown/i.test(withheld.text)
  );
  await page.close();

  // ---- 5. the control still reveals ---------------------------------------
  page = await browser.newPage();
  await page.goto("file://" + path.join(ROOT, "open", "index.html"), { waitUntil: "networkidle0" });
  await page.evaluate(() => {
    document.querySelectorAll("textarea").forEach((t) => {
      t.value = "the water cycle moves water between ocean atmosphere and land continuously";
      t.dispatchEvent(new Event("input", { bubbles: true }));
    });
    document.getElementById("submit").click();
  });
  const open = await page.evaluate(() => ({
    modelAnswers: document.querySelectorAll(".model-answer").length,
    markSchemes: document.querySelectorAll(".mark-scheme").length,
    timer: document.getElementById("timer").hidden,
  }));
  check("untimed assessment shows no clock", open.timer === true);
  check("default still reveals the mark scheme", open.markSchemes > 0, `${open.markSchemes}`);
  check("default still reveals the model answer", open.modelAnswers > 0, `${open.modelAnswers}`);

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(JSON.stringify({ total: results.length, failed: failed.map((f) => f.name) }));
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error("HARNESS FAILED", e);
  process.exit(2);
});
