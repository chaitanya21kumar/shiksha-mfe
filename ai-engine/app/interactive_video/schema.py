"""The Module C.3 contract: everything one interactive video is built from.

An interactive video is an assembly rather than a generation — it composes three
things the engine already produces (a transcript's chapters, questions grounded in
those chapters, and the media itself) into one importable package.

The media is referenced by **URL**, not bundled. That is how H5P's own published
Interactive Video content does it: its `content.json` carries
``{"path": "https://…/iv.mp4", "mime": "video/mp4"}``. Bundling would put the
whole recording inside the ``.h5p`` and run straight into an LMS upload limit,
for no benefit — the LMS is going to stream it either way. See ADR-0009.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..assessment.schema import Question
from ..chaptering.schema import MAX_CHAPTERS, Chapter
from ..transcription.schema import TranscriptSource

#: The container formats H5P.Video 1.6 plays natively. A source outside this set
#: still packages, but the learner may get an empty player, so it is warned about.
KNOWN_VIDEO_MIMES: frozenset[str] = frozenset({"video/mp4", "video/webm", "video/ogg"})

_HTTP_URL = re.compile(r"^https?://", re.IGNORECASE)


class VideoSource(BaseModel):
    """Where the media is hosted, and what it is."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(description="An http(s) URL the LMS can stream the media from.")
    mime: str = Field(default="video/mp4", description="The container's MIME type.")
    subtitles_url: str | None = Field(
        default=None,
        description=(
            "Optional http(s) URL of a WebVTT track. Referenced, not embedded, for the "
            "same reason the media is: H5P resolves an absolute URL as-is."
        ),
    )
    subtitles_language: str = Field(
        default="en", description="BCP-47 tag for the subtitle track, shown in the caption menu."
    )

    @model_validator(mode="after")
    def _url_is_streamable(self) -> VideoSource:
        # A relative path or a file:// URL would package cleanly and then show the
        # learner an empty player, because the LMS serves the package from its own
        # domain and has no access to the author's disk.
        #
        # The stripped value is what gets stored, not just what gets validated. H5P
        # decides whether a path is absolute with `/^[a-z0-9]+:\/\//i`, and a single
        # leading space fails that test — so the player treats the URL as relative to
        # the package, finds nothing, and shows an empty frame with no error anywhere.
        # Validating the stripped form while storing the padded one would let exactly
        # that through.
        cleaned = self.url.strip()
        if not _HTTP_URL.match(cleaned):
            raise ValueError("video url must be an http(s) URL the LMS can reach")
        object.__setattr__(self, "url", cleaned)

        # The same trap, and the same fix: a padded subtitle URL resolves relative to
        # the package and the caption simply never appears.
        if self.subtitles_url is not None:
            track = self.subtitles_url.strip()
            if not _HTTP_URL.match(track):
                raise ValueError("subtitles url must be an http(s) URL the LMS can reach")
            object.__setattr__(self, "subtitles_url", track)
        return self


class ChapterCheck(BaseModel):
    """The questions asked at the end of one chapter."""

    model_config = ConfigDict(extra="forbid")

    chapter_index: int = Field(ge=1, description="The Chapter.index these questions belong to.")
    questions: list[Question] = Field(
        default_factory=list, description="Questions grounded in that chapter's transcript."
    )


class InteractiveVideoSpec(BaseModel):
    """Everything Module C.3 needs to emit one interactive video package."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    content_id: str = Field(
        description="Stable id for this package; subcontent ids are derived from it."
    )
    source: TranscriptSource
    video: VideoSource
    title: str = Field(description="Shown on the video's start screen.")
    language: str = Field(default="en", description="BCP-47 tag for the package manifest.")
    chapters: list[Chapter] = Field(default_factory=list, max_length=MAX_CHAPTERS)
    checks: list[ChapterCheck] = Field(default_factory=list)
    generated_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _checks_point_at_real_chapters(self) -> InteractiveVideoSpec:
        """Every check must name a chapter that exists.

        A check on a missing chapter has no time to be placed at. Left unchecked it
        would either vanish silently or land at second zero, so the contract
        refuses it instead of guessing.
        """
        known = {chapter.index for chapter in self.chapters}
        seen_chapters: set[int] = set()
        for check in self.checks:
            if check.chapter_index not in known:
                raise ValueError(
                    f"check refers to chapter {check.chapter_index}, which is not in this video"
                )
            if check.chapter_index in seen_chapters:
                raise ValueError(f"chapter {check.chapter_index} has more than one check block")
            seen_chapters.add(check.chapter_index)
        return self

    @model_validator(mode="after")
    def _question_ids_are_unique(self) -> InteractiveVideoSpec:
        """No two questions in one video may share an id.

        Questions are generated per chapter, so their ids are only unique within a
        chapter until they are renumbered. An H5P subcontent id is derived from the
        question id, and two interactions sharing one collide in the learner's
        stored state — a silent failure that only shows up on a resumed attempt.
        """
        seen: set[str] = set()
        for check in self.checks:
            for question in check.questions:
                if question.id in seen:
                    raise ValueError(f"question id {question.id!r} appears more than once")
                seen.add(question.id)
        return self
