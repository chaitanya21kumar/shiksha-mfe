"""Render a `Transcript` as WebVTT or SRT subtitles.

Both are the standard caption formats an LMS or an H5P Interactive Video expects:
a list of cues, each a start/end time and a line of text. They differ in two
small, easy-to-get-wrong ways, reproduced exactly here:

- **The decimal separator.** WebVTT uses a dot (``00:00:01.500``); SRT uses a
  comma (``00:00:01,500``). A player fed the wrong one shows no captions.
- **The header and numbering.** WebVTT starts with a literal ``WEBVTT`` line and
  has no cue numbers; SRT has no header and numbers every cue from 1.

Times are always ``HH:MM:SS`` with milliseconds — WebVTT permits a two-field
``MM:SS.mmm`` form, but the three-field form is valid in both, so emitting it
keeps the two renderers as close as possible.
"""

from __future__ import annotations

from html import escape

from .schema import Transcript, TranscriptSegment


def _clock(seconds: float) -> tuple[int, int, int, int]:
    """Split a second count into whole hours, minutes, seconds and milliseconds."""
    if seconds < 0:
        seconds = 0.0
    # Round to the millisecond first so 1.9996 s does not render as 00:00:01.999.
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, millis = divmod(rem_ms, 1000)
    return hours, minutes, secs, millis


def _timestamp(seconds: float, separator: str) -> str:
    """``HH:MM:SS<sep>mmm`` — separator is ``.`` for WebVTT, ``,`` for SRT."""
    hours, minutes, secs, millis = _clock(seconds)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _speaker_prefix(segment: TranscriptSegment) -> str:
    """A ``Name: `` prefix when the cue is attributed to a speaker, else empty."""
    return f"{segment.speaker}: " if segment.speaker else ""


def _one_line(text: str) -> str:
    """Collapse internal whitespace to single spaces.

    A cue payload must not contain a blank line: in both formats a blank line ends
    the cue, so an embedded newline in the transcript text would split one caption
    into two and desync everything after it. Collapsing runs of whitespace removes
    that risk while leaving the words intact.
    """
    return " ".join(text.split())


def _vtt_cue_text(segment: TranscriptSegment) -> str:
    """One-line, HTML-escaped cue text for WebVTT.

    WebVTT cue payloads are HTML-ish: ``&`` and ``<`` are special, and an
    unescaped ``<`` can start a spurious tag. Escaping them keeps the file
    conformant, and it also neutralises the ``-->`` cue-timing sentinel, whose
    ``>`` becomes ``&gt;``.
    """
    return escape(_one_line(f"{_speaker_prefix(segment)}{segment.text}"), quote=False)


def _srt_cue_text(segment: TranscriptSegment) -> str:
    """One-line cue text for SRT (not HTML, so no escaping — just no blank lines)."""
    return _one_line(f"{_speaker_prefix(segment)}{segment.text}")


def to_webvtt(transcript: Transcript) -> str:
    """Render the transcript as a WebVTT file (dot separator, ``WEBVTT`` header)."""
    lines = ["WEBVTT", ""]
    for segment in transcript.segments:
        start = _timestamp(segment.start, ".")
        end = _timestamp(segment.end, ".")
        lines.append(f"{start} --> {end}")
        lines.append(_vtt_cue_text(segment))
        lines.append("")
    return "\n".join(lines)


def to_srt(transcript: Transcript) -> str:
    """Render the transcript as an SRT file (comma separator, numbered cues)."""
    lines: list[str] = []
    for number, segment in enumerate(transcript.segments, start=1):
        start = _timestamp(segment.start, ",")
        end = _timestamp(segment.end, ",")
        lines.append(str(number))
        lines.append(f"{start} --> {end}")
        lines.append(_srt_cue_text(segment))
        lines.append("")
    return "\n".join(lines)
