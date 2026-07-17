"""Emitters for the portable formats the engine publishes into an LMS.

This layer knows about *file formats* (H5P, SCORM) and nothing about where the
content came from. The modules that own the content map onto it: Module B's
assessments today, Module C's interactive video and Module D's micro-lessons
later. Keeping the format knowledge here is what stops those modules from having
to import from each other.
"""
