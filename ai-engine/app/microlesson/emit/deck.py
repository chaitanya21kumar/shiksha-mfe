"""Renders a `MicroLesson` as a self-contained slide deck.

This is the shared half of the two non-H5P targets. The HTML5 download is this
deck and nothing else; the SCORM package is this deck plus a reporting layer. One
renderer, so the two cannot drift into looking different — which they would within
a month if each format built its own markup.

**Self-contained is a requirement, not a nicety.** Everything is inlined: no
stylesheet, no script, no font, no image fetched from anywhere. A teacher can put
the file on a pen drive, open it on a machine with no internet, and it works. It
is also what makes the SCORM package safe, because an LMS serves a SCO from its
own origin and a request out to a CDN is both a privacy leak and a thing that
breaks the moment the network is filtered — which, in the schools this is for, it
often is.

**The deck exposes one hook and no more.** ``window.LessonDeck.onSlide`` is called
with the zero-based index whenever the visible slide changes. SCORM sets it to
report progress; the plain HTML5 file leaves it alone. Anything richer would put
LMS concerns into a file that has no LMS.

Everything the model wrote is escaped on the way in, for the reason the H5P
emitter records: the text came out of a tenant's uploaded document, and here it is
being written straight into a document that will be opened in a browser.
"""

from __future__ import annotations

from ...packaging.h5p import sanitise_language
from ...packaging.naming import escape_text
from ..schema import MicroLesson

#: Kept in one string rather than scattered through the builder so the whole visual
#: design can be read at once. System fonts only — a webfont would be a network
#: request, and the point of this file is that it needs none.
_CSS = """
:root{--ink:#111827;--muted:#4b5563;--line:#e5e7eb;--bg:#ffffff;--accent:#1d4ed8;--panel:#f8fafc}
*{box-sizing:border-box}
html,body{margin:0;padding:0;height:100%}
body{background:var(--panel);color:var(--ink);
  font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
.deck{max-width:60rem;margin:0 auto;min-height:100%;display:flex;flex-direction:column;
  background:var(--bg);box-shadow:0 0 0 1px var(--line)}
header{padding:1.1rem 2rem;border-bottom:1px solid var(--line)}
header h1{margin:0;font-size:1.05rem;font-weight:600;letter-spacing:.01em}
header p{margin:.2rem 0 0;font-size:.8rem;color:var(--muted)}
main{flex:1;padding:2.4rem 2rem}
.slide{display:none}
.slide.is-current{display:block}
.slide h2{margin:0 0 1.4rem;font-size:1.9rem;line-height:1.25;font-weight:650}
.slide ul{margin:0;padding-left:1.3rem}
.slide li{margin:0 0 .85rem;font-size:1.12rem}
.notes{margin-top:2rem;border-top:1px solid var(--line);padding-top:1rem}
.notes summary{cursor:pointer;font-size:.85rem;color:var(--accent);font-weight:600}
.notes p{margin:.7rem 0 0;color:var(--muted);font-size:1rem}
footer{display:flex;align-items:center;gap:1rem;padding:1rem 2rem;border-top:1px solid var(--line)}
button{font:inherit;font-size:.9rem;padding:.45rem 1rem;border:1px solid var(--line);
  border-radius:6px;background:var(--bg);color:var(--ink);cursor:pointer}
button:hover:not(:disabled){border-color:var(--muted)}
button:disabled{opacity:.4;cursor:default}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.count{font-size:.85rem;color:var(--muted);font-variant-numeric:tabular-nums}
.bar{flex:1;height:4px;background:var(--line);border-radius:2px;overflow:hidden}
.bar span{display:block;height:100%;background:var(--accent);transition:width .18s ease}
@media (max-width:640px){main{padding:1.6rem 1.1rem}.slide h2{font-size:1.5rem}
  header,footer{padding-left:1.1rem;padding-right:1.1rem}}
@media print{
  /* Every slide on the page, notes open, chrome gone — so "print to PDF" gives a
     handout rather than one slide and a dead navigation bar. */
  body{background:#fff}.deck{box-shadow:none;max-width:none}
  .slide{display:block!important;page-break-after:always;padding-bottom:1rem}
  footer{display:none}.notes summary{display:none}.notes p{color:var(--ink)}
  details{display:block}details:not([open]) p{display:block}
}
"""

#: No framework, no build step, and it degrades honestly: with JavaScript off the
#: `no-js` rule in the markup leaves every slide visible, so the content is still
#: readable rather than a blank page.
_JS = """
(function(){
  var slides=[].slice.call(document.querySelectorAll('.slide'));
  var prev=document.getElementById('prev'), next=document.getElementById('next');
  var count=document.getElementById('count'), fill=document.getElementById('fill');
  var live=document.getElementById('live');
  var at=0;
  window.LessonDeck={onSlide:null,total:slides.length,go:show,current:function(){return at}};
  function show(i){
    at=Math.max(0,Math.min(slides.length-1,i));
    slides.forEach(function(s,n){s.classList.toggle('is-current',n===at)});
    prev.disabled = at===0; next.disabled = at===slides.length-1;
    count.textContent=(at+1)+' / '+slides.length;
    fill.style.width=(((at+1)/slides.length)*100)+'%';
    var h=slides[at].querySelector('h2');
    live.textContent='Slide '+(at+1)+' of '+slides.length+(h?': '+h.textContent:'');
    if(typeof window.LessonDeck.onSlide==='function'){
      try{window.LessonDeck.onSlide(at)}catch(e){}
    }
  }
  prev.addEventListener('click',function(){show(at-1)});
  next.addEventListener('click',function(){show(at+1)});
  document.addEventListener('keydown',function(e){
    var t=e.target.tagName;
    if(t==='INPUT'||t==='TEXTAREA'||e.metaKey||e.ctrlKey||e.altKey) return;
    if(e.key==='ArrowRight'||e.key==='PageDown'){show(at+1);e.preventDefault()}
    else if(e.key==='ArrowLeft'||e.key==='PageUp'){show(at-1);e.preventDefault()}
    else if(e.key==='Home'){show(0);e.preventDefault()}
    else if(e.key==='End'){show(slides.length-1);e.preventDefault()}
  });
  document.documentElement.classList.remove('no-js');
  show(0);
})();
"""


def _bullets(items: list[str]) -> str:
    rendered = "".join(f"<li>{escape_text(i.strip())}</li>" for i in items if i and i.strip())
    return f"<ul>{rendered}</ul>" if rendered else ""


def _slide(heading: str, body: str, notes: str = "") -> str:
    parts = ['<section class="slide" role="group" aria-roledescription="slide">']
    if heading:
        parts.append(f"<h2>{heading}</h2>")
    if body:
        parts.append(body)
    if notes:
        # A details element rather than a button we would have to script: it opens
        # and closes with no JavaScript at all, and print styles force it open.
        parts.append(
            '<div class="notes"><details><summary>Teacher notes</summary>'
            f"<p>{notes}</p></details></div>"
        )
    parts.append("</section>")
    return "".join(parts)


def slides_html(lesson: MicroLesson) -> list[str]:
    """Every slide of the lesson, escaped and ready to drop into a page.

    Returned as a list rather than one string so a caller can count them without
    parsing — the SCORM layer needs the number to report progress against.
    """
    out: list[str] = []
    objectives = _bullets(lesson.objectives)
    if objectives:
        out.append(_slide(escape_text("What you will be able to do"), objectives))
    for step in lesson.steps:
        heading = escape_text(step.title.strip())
        body = _bullets(step.bullets)
        if not heading and not body:
            continue
        out.append(_slide(heading, body, escape_text(step.notes.strip())))
    return out


def render_deck(lesson: MicroLesson, *, extra_head: str = "", extra_body: str = "") -> str:
    """The whole deck as one self-contained HTML document.

    ``extra_head`` and ``extra_body`` are the seams the SCORM target uses to add
    its API script without this module knowing anything about SCORM.
    """
    slides = slides_html(lesson)
    if not slides:
        raise ValueError("the lesson has no step with any text to put on a slide")

    title = escape_text(lesson.title.strip() or "Micro-lesson")
    subtitle = escape_text(f"{len(slides)} slides")
    # `escape_text` leaves quotes alone on purpose — it is built for text nodes,
    # where escaping them only hurts readability. This is the one place in the deck
    # that writes into an *attribute*, and a first version used it here anyway:
    # a language of `" onload="alert(1)` produced
    # `<html lang="" onload="alert(1)" class="no-js">`, a working injection reachable
    # from a query parameter. Escaping would fix it; enforcing the shape is better,
    # because a language tag has one. `sanitise_language` is the manifest's own
    # guard, `^[-a-zA-Z]{1,10}$`, which cannot express a quote at all.
    lang = sanitise_language(lesson.language or "en")
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="{lang}" class="no-js">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n"
        # `no-js` is removed by the script. Until then every slide is visible, so a
        # browser with scripting disabled shows the whole lesson instead of nothing.
        "<style>.no-js .slide{display:block}</style>\n"
        f"<style>{_CSS}</style>\n"
        f"{extra_head}"
        "</head>\n<body>\n"
        '<div class="deck">\n'
        f"<header><h1>{title}</h1><p>{subtitle}</p></header>\n"
        '<main id="main">\n' + "\n".join(slides) + "\n</main>\n"
        '<footer>\n'
        '<button id="prev" type="button">Previous</button>\n'
        '<button id="next" type="button">Next</button>\n'
        '<div class="bar" role="presentation"><span id="fill"></span></div>\n'
        '<div class="count" id="count"></div>\n'
        "</footer>\n</div>\n"
        # Announces slide changes to a screen reader without stealing focus.
        '<div id="live" aria-live="polite" class="sr" '
        'style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)"></div>\n'
        f"<script>{_JS}</script>\n"
        f"{extra_body}"
        "</body>\n</html>\n"
    )
