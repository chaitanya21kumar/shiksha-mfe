"""Module B — automated assessment suite.

Turns a parsed document into a source-grounded `AssessmentSet` (multiple-choice,
match-the-pair, and fill-in-the-blank questions). The contract is neutral: it
maps losslessly, in later modules, into an H5P Question Set, a SCORM 1.2
package, and xAPI statements without being coupled to any one of them.
"""
