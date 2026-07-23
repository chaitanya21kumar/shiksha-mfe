"""Normalise a Whisper-reported language to an ISO-639-1 code.

Whisper's ``verbose_json`` reports the detected language as its full English
name — ``"english"``, ``"hindi"`` — whereas the `Transcript` contract, and the
BCP-47 language tag an H5P package later needs, are ISO-639-1 codes. This maps the
one onto the other so the field means what the schema says it does, and so a
detected language and a supplied hint end up in the same representation.

The table is Whisper's own published language set (the inverse of its
``LANGUAGES`` map). An unrecognised value is returned lowercased and unchanged, so
a provider outside this set degrades to its own label rather than being dropped.
"""

from __future__ import annotations

#: Whisper language name → ISO-639-1 code. Names are matched case-insensitively.
_NAME_TO_CODE: dict[str, str] = {
    "english": "en", "chinese": "zh", "german": "de", "spanish": "es",
    "russian": "ru", "korean": "ko", "french": "fr", "japanese": "ja",
    "portuguese": "pt", "turkish": "tr", "polish": "pl", "catalan": "ca",
    "dutch": "nl", "arabic": "ar", "swedish": "sv", "italian": "it",
    "indonesian": "id", "hindi": "hi", "finnish": "fi", "vietnamese": "vi",
    "hebrew": "he", "ukrainian": "uk", "greek": "el", "malay": "ms",
    "czech": "cs", "romanian": "ro", "danish": "da", "hungarian": "hu",
    "tamil": "ta", "norwegian": "no", "thai": "th", "urdu": "ur",
    "croatian": "hr", "bulgarian": "bg", "lithuanian": "lt", "latin": "la",
    "maori": "mi", "malayalam": "ml", "welsh": "cy", "slovak": "sk",
    "telugu": "te", "persian": "fa", "latvian": "lv", "bengali": "bn",
    "serbian": "sr", "azerbaijani": "az", "slovenian": "sl", "kannada": "kn",
    "estonian": "et", "macedonian": "mk", "breton": "br", "basque": "eu",
    "icelandic": "is", "armenian": "hy", "nepali": "ne", "mongolian": "mn",
    "bosnian": "bs", "kazakh": "kk", "albanian": "sq", "swahili": "sw",
    "galician": "gl", "marathi": "mr", "punjabi": "pa", "sinhala": "si",
    "khmer": "km", "shona": "sn", "yoruba": "yo", "somali": "so",
    "afrikaans": "af", "occitan": "oc", "georgian": "ka", "belarusian": "be",
    "tajik": "tg", "sindhi": "sd", "gujarati": "gu", "amharic": "am",
    "yiddish": "yi", "lao": "lo", "uzbek": "uz", "faroese": "fo",
    "pashto": "ps", "maltese": "mt", "sanskrit": "sa", "luxembourgish": "lb",
    "myanmar": "my", "tibetan": "bo", "tagalog": "tl", "malagasy": "mg",
    "assamese": "as", "tatar": "tt", "hawaiian": "haw", "lingala": "ln",
    "hausa": "ha", "bashkir": "ba", "javanese": "jw", "sundanese": "su",
    "burmese": "my", "valencian": "ca", "flemish": "nl", "haitian": "ht",
    "letzeburgesch": "lb", "pushto": "ps", "panjabi": "pa", "moldavian": "ro",
    "moldovan": "ro", "sinhalese": "si", "castilian": "es", "mandarin": "zh",
}


def to_iso639_1(language: str | None) -> str | None:
    """Map a Whisper language name to its ISO-639-1 code.

    A value already looking like a code (two letters) is returned lowercased; a
    known full name is mapped; anything else is returned lowercased unchanged so
    an unfamiliar provider label is preserved rather than lost.
    """
    if not language:
        return language
    key = language.strip().lower()
    if key in _NAME_TO_CODE:
        return _NAME_TO_CODE[key]
    return key
