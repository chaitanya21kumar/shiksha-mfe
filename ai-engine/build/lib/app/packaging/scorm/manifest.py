r"""Builds ``imsmanifest.xml`` — the file that decides whether an LMS will import.

Three rules here look like formatting and are not. Each was confirmed by reading
the consumers' own parsers, and each fails **silently**:

1. **Never pretty-print this file.** Open edX decides the SCORM version with
   ``re.match("^1.2$", schemaversion.text)``. Indent the XML and that text becomes
   ``"\n      1.2\n    "``, the match fails, the package is treated as SCORM 2004,
   and the LMS injects an ``API_1484_11`` object instead of ``API``. Our SCO looks
   for ``API``, finds nothing, and reports not one byte — while the quiz renders
   perfectly. So no ``ET.indent``, ever.

2. **The prefix must literally be ``adlcp``.** Moodle parses with
   ``xml_parser_create`` (no namespace processing) and matches the upper-cased
   literal string ``ADLCP:SCORMTYPE``. Binding the correct namespace URI to a
   different prefix is valid XML that Moodle cannot see — it would default the
   resource to ``asset`` and the SCO would never make an API call.

3. **The IMS CP namespace must be the default (unprefixed) one.** Open edX takes
   the first namespace whose prefix is empty and uses it to find the resource.
   Prefix it and Open edX finds nothing and falls back to guessing.

The mirror image of (2) is worth stating: because Moodle ignores namespace URIs
entirely, a *wrong* URI passes a Moodle test and fails everywhere else. The URI
below is the ADL schema's own ``targetNamespace`` — ``adlcp_rootv1p2``, not
``adl_cp_rootv1p1``, which is SCORM 1.1.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# These are XML namespace *identifiers*, not addresses. Nothing dereferences them;
# their only job is to be compared, character for character, against the exact
# strings the IMS and ADL specifications define — the same way `xsi` is defined as
# "http://www.w3.org/2001/XMLSchema-instance" by the W3C and is spelled that way
# everywhere, forever. Rewriting them to https would harden nothing and would
# produce a manifest that no LMS recognises, which is precisely the failure this
# module exists to prevent. They are therefore suppressed rather than "fixed".
CP_NAMESPACE = "http://www.imsproject.org/xsd/imscp_rootv1p1p2"  # NOSONAR
ADLCP_NAMESPACE = "http://www.adlnet.org/xsd/adlcp_rootv1p2"  # NOSONAR
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"  # NOSONAR

SCHEMA = "ADL SCORM"
SCHEMA_VERSION = "1.2"

MANIFEST_NAME = "imsmanifest.xml"

_SCHEMA_LOCATION = (
    f"{CP_NAMESPACE} imscp_rootv1p1p2.xsd {ADLCP_NAMESPACE} adlcp_rootv1p2.xsd"
)

# An XML NMTOKEN we are willing to emit as an identifier.
_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def sanitise_identifier(value: str, *, fallback: str = "ASSESSMENT") -> str:
    """Coerce an id into something safe for an XML identifier attribute."""
    cleaned = _UNSAFE_ID.sub("-", (value or "").strip()).strip("-")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"{fallback}-{cleaned}".strip("-")
    return cleaned[:255]


def build_manifest(
    *,
    assessment_id: str,
    title: str,
    launch_href: str,
    files: list[str],
) -> bytes:
    """Build the manifest for a single-SCO package.

    ``files`` lists every file in the package except the manifest itself. The CAM
    allows zero ``<file>`` elements, but some importers use them to decide what to
    extract, so we list them all.

    ``adlcp:masteryscore`` is deliberately absent — see ADR-0005. In short: when
    it is present Moodle stops believing the SCO's own ``lesson_status`` and
    derives pass/fail itself, while Open edX has no mastery-score path at all and
    would never mark success. Omitting it leaves exactly one authority (our
    player) and makes both LMSs agree.
    """
    ET.register_namespace("", CP_NAMESPACE)
    ET.register_namespace("adlcp", ADLCP_NAMESPACE)
    ET.register_namespace("xsi", XSI_NAMESPACE)

    identifier = sanitise_identifier(assessment_id)
    manifest = ET.Element(
        f"{{{CP_NAMESPACE}}}manifest",
        {
            "identifier": identifier,
            "version": SCHEMA_VERSION,
            f"{{{XSI_NAMESPACE}}}schemaLocation": _SCHEMA_LOCATION,
        },
    )

    metadata = ET.SubElement(manifest, f"{{{CP_NAMESPACE}}}metadata")
    # No surrounding whitespace: see the module docstring, rule 1.
    ET.SubElement(metadata, f"{{{CP_NAMESPACE}}}schema").text = SCHEMA
    ET.SubElement(metadata, f"{{{CP_NAMESPACE}}}schemaversion").text = SCHEMA_VERSION

    organisation_id = f"{identifier}-ORG"
    resource_id = f"{identifier}-RES"

    organizations = ET.SubElement(
        manifest, f"{{{CP_NAMESPACE}}}organizations", {"default": organisation_id}
    )
    organization = ET.SubElement(
        organizations, f"{{{CP_NAMESPACE}}}organization", {"identifier": organisation_id}
    )
    # Open edX's find_titles_recursively does an unguarded .text on these, so a
    # missing <title> is an AttributeError at import time, not a warning.
    ET.SubElement(organization, f"{{{CP_NAMESPACE}}}title").text = title
    item = ET.SubElement(
        organization,
        f"{{{CP_NAMESPACE}}}item",
        {"identifier": f"{identifier}-ITEM", "identifierref": resource_id, "isvisible": "true"},
    )
    ET.SubElement(item, f"{{{CP_NAMESPACE}}}title").text = title

    resources = ET.SubElement(manifest, f"{{{CP_NAMESPACE}}}resources")
    resource = ET.SubElement(
        resources,
        f"{{{CP_NAMESPACE}}}resource",
        {
            "identifier": resource_id,
            "type": "webcontent",
            f"{{{ADLCP_NAMESPACE}}}scormtype": "sco",
            "href": launch_href,
        },
    )
    for name in files:
        ET.SubElement(resource, f"{{{CP_NAMESPACE}}}file", {"href": name})

    return ET.tostring(manifest, encoding="utf-8", xml_declaration=True)
