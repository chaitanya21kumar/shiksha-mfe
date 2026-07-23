"""Tests for the Whisper language-name → ISO-639-1 normalisation."""

from app.transcription.languages import to_iso639_1


def test_a_full_whisper_name_maps_to_its_code():
    assert to_iso639_1("english") == "en"
    assert to_iso639_1("hindi") == "hi"
    assert to_iso639_1("Spanish") == "es"  # matched case-insensitively


def test_a_code_is_returned_unchanged():
    assert to_iso639_1("en") == "en"
    assert to_iso639_1("EN") == "en"


def test_an_unknown_label_is_lowercased_but_kept():
    assert to_iso639_1("klingon") == "klingon"


def test_none_and_empty_pass_through():
    assert to_iso639_1(None) is None
    assert to_iso639_1("") == ""


def test_whisper_aliases_resolve():
    # Names Whisper uses that differ from the primary label.
    assert to_iso639_1("mandarin") == "zh"
    assert to_iso639_1("castilian") == "es"
    assert to_iso639_1("flemish") == "nl"
