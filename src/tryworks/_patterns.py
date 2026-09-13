"""Regular expressions shared by the text classifiers and the cleaners."""

from __future__ import annotations

import re

# -- characters that mark a bulleted line on their own --
UNICODE_BULLETS = (
    "\u0095\u00b7\u2022\u2023\u2043\u204c\u204d\u2219\u25a0\u25a1\u25aa\u25ab\u25b6\u25ba"
    "\u25cb\u25cf\u25d8\u25e6\u2619\u2765\u2767\u27a2\u27a4\u29be\u29bf\uf0a7\uf0b7"
)
# -- ASCII-ish markers only count when followed by whitespace, so "**bold**", "-5 degrees"
# -- and "+49 30 1234" are not taken for list items.
BULLET_RE = re.compile(rf"^\s*(?:[{UNICODE_BULLETS}]|[-*+\u2013\u2014](?=\s))")
BULLET_PREFIX_RE = re.compile(rf"^\s*(?:[{UNICODE_BULLETS}]+|[-*+\u2013\u2014](?=\s))\s*")

NUMBERED_LIST_RE = re.compile(r"^\s*\d{1,3}(?:\.\d{1,3})*[.)]\s+\S")

EMAIL_ADDRESS_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")

US_PHONE_NUMBER_RE = re.compile(
    r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\b\d{3}[\s.-])?\b\d{3}[\s.-]\d{4}\b"
)

_US_STATES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|"
    "Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|"
    "Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|"
    "New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|"
    "Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|"
    "West Virginia|Wisconsin|Wyoming|District of Columbia"
)
_US_STATE_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|"
    "NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY"
)
US_CITY_STATE_ZIP_RE = re.compile(
    rf"^[A-Za-z][A-Za-z .'\-]*,\s*(?:{_US_STATES}|{_US_STATE_CODES}),?\s+\d{{5}}(?:-\d{{4}})?$",
    re.IGNORECASE,
)

ENDS_IN_PUNCT_RE = re.compile(r"[^\w\s]\Z")

# -- paragraph grouping --
BLANK_LINE_SPLIT_RE = re.compile(r"\n[ \t\f\v\r]*\n\s*")
LINE_SPLIT_RE = re.compile(r"[ \t\f\v\r]*\n[ \t\f\v\r]*")

WORD_RE = re.compile(r"\w+(?:['\u2019-]\w+)*|[^\w\s]")
