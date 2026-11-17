"""Diacritic-insensitive name matching ("Długa" == "dluga")."""
import re
import unicodedata

_EXTRA = str.maketrans({"ł": "l", "Ł": "l", "ß": "ss", "đ": "d", "ø": "o"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


# Common Polish street/stop abbreviations: both spellings fold to the short form, so
# "Plac Grunwaldzki" matches "PL. GRUNWALDZKI" and "Aleja Róż" matches "al. Róż".
_ABBREV = {"plac": "pl", "aleja": "al", "aleje": "al", "alei": "al", "osiedle": "os", "osiedla": "os",
           "ulica": "ul", "ulicy": "ul", "swietego": "sw", "swietej": "sw", "swiety": "sw", "swieta": "sw",
           "ksiedza": "ks", "generala": "gen"}


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", s.translate(_EXTRA))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(_ABBREV.get(w, w) for w in _NON_ALNUM.sub(" ", s).split())


def tokens(s: str) -> list[str]:
    return [t for t in fold(s).split(" ") if t]
