"""Classify a run of text as narrative, title, list item, address and so on.

Same functions, signatures and environment-variable overrides as
``unstructured.partition.text_type``, without NLTK: sentences are split by rule and verbs are
detected with a compact English lexicon, inflection rules and a little word-order context.
"""

from __future__ import annotations

import os
from typing import Iterator, List, Optional

from tryworks import _patterns as P
from tryworks.cleaners.core import remove_punctuation

# -- sentence splitting ----------------------------------------------------------------------

_ABBREVIATIONS = frozenset(
    "mr mrs ms dr prof sr jr st vs etc inc ltd co corp dept est fig figs no nos approx cf al "
    "pp vol eds e.g i.e u.s u.k a.m p.m jan feb mar apr jun jul aug sep sept oct nov dec".split()
)
_CLOSERS = "\"')]’”"


def sent_tokenize(text: str) -> list[str]:
    """Split text into sentences at ., ! or ? followed by whitespace and a non-lowercase start."""
    text = text.strip()
    if not text:
        return []
    sentences: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] not in ".!?":
            i += 1
            continue
        j = i
        while j < n and text[j] in ".!?":
            j += 1
        while j < n and text[j] in _CLOSERS:
            j += 1
        if j >= n or not text[j].isspace():
            i = j
            continue
        k = j
        while k < n and text[k].isspace():
            k += 1
        if k >= n:
            break
        if text[i:j].rstrip(_CLOSERS) == ".":
            words = text[start:i].split()
            word = words[-1].lower().lstrip("(\"'") if words else ""
            if word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                i = j
                continue
        if text[k].islower():
            i = j
            continue
        sentences.append(text[start:j].strip())
        start = k
        i = k
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def word_tokenize(text: str) -> list[str]:
    return P.WORD_RE.findall(text)


# -- verb detection --------------------------------------------------------------------------

_ALWAYS_VERBS = frozenset(
    """am is are was were be been being has have had having does did doing done can could will
    would shall should may might must cannot can't won't don't doesn't didn't isn't aren't wasn't
    weren't hasn't haven't hadn't shouldn't wouldn't couldn't i'm you're we're they're he's she's
    it's that's there's i've you've we've they've i'd you'd we'd they'd i'll you'll we'll they'll
    went gone took taken made said told gave given got gotten came saw seen knew known thought
    found brought bought began begun became kept felt meant ran sat stood lost paid met sent built
    fell fallen held wrote written spoke spoken chose chosen grew grown drew drawn drove driven ate
    eaten flew flown forgot forgotten threw thrown wore worn won understood taught caught sold heard
    laid hid hidden shook shaken woke spent struck swore torn broke broken seemed""".split()
)

_BASE_VERBS = frozenset(
    """accept achieve add admit affect agree allow announce answer appear apply approve argue arrange
    arrive ask assume attack attempt attend avoid become begin believe belong bring build buy
    calculate call care carry catch cause change check choose claim clean click close collect come
    compare complain complete concern confirm connect consider consist contain continue contribute
    control cost count cover create cut deal decide define deliver demand deny depend describe
    design destroy determine develop die disappear discover discuss divide do download draw drink
    drive drop earn eat enable encourage end enjoy ensure enter establish estimate examine exist
    expect experience explain express extend face fail fall feel fight fill find finish fit fly
    focus follow force forget form get give go grow handle happen hate have hear help hide hit hold
    hope identify ignore imagine improve include increase indicate influence inform install intend
    introduce invest investigate invite involve join jump keep kill know lack last laugh lead learn
    leave let lie like limit link listen live look lose love maintain make manage mark matter mean
    measure meet mention mind miss move need note notice obtain occur offer open operate order own
    pay perform pick place plan play point prefer prepare present prevent produce promise protect
    prove provide publish pull push put raise reach read realize receive recognize recommend record
    reduce refer reflect refuse regard relate release remain remember remove repeat replace reply
    report represent require respond rest result return reveal run save say see seem select sell
    send serve set settle shake share shoot show shut sing sit sleep smile solve sound speak spend
    stand start state stay stick stop study submit succeed suffer suggest supply support suppose
    survive take talk teach tell tend test thank think throw touch train travel treat try turn
    understand update upload use visit wait walk want warn wash watch wear win wish wonder work
    worry write""".split()
)

_NON_VERB_ED = frozenset(
    "hundred kindred sacred naked wicked rugged ragged crooked speed breed indeed embed need seed "
    "feed weed deed greed bleed shed sled bed red".split()
)
_NON_VERB_ING = frozenset(
    """thing something nothing anything everything morning evening ceiling king ring spring string
    during wing sibling pudding wedding building meeting setting settings training marketing
    heading clothing housing funding pricing shipping engineering accounting banking billing
    booking briefing catering coaching consulting ending finding hearing lighting listing logging
    mapping offering opening painting parking planning printing processing reading recording rating
    ranking saving savings scaling screening seating spelling staffing testing timing tracking
    trading warning wiring writing""".split()
)

_SUBJECTS = frozenset("i you we they he she it who which that this there".split())
_BARE_FORM_CUES = frozenset(
    """to will would can could shall should may might must do does did don't doesn't didn't not
    never please let's also always often usually just only""".split()
)
_DETERMINERS = frozenset(
    """a an the this that these those my your our their his her its some any many much several
    all each every no few more most other another such what which whose both either neither one
    two three four five six seven eight nine ten""".split()
)
_PREPOSITIONS = frozenset(
    """of in on at by for with from into onto about above below under over between among through
    during before after without within across against toward towards upon via per like than as
    and or""".split()
)
_IMPERATIVE_NEXT = _DETERMINERS | frozenset(
    "me us him them it to on in for with out up down here below above".split()
)
_ADJECTIVE_ENDINGS = ("ly", "al", "ic", "ive", "ous", "ful", "less", "able", "ible", "ary", "ern")


def _base_candidates(tok: str) -> Iterator[tuple[str, str]]:
    """Yield (candidate base form, inflection kind) for an English token."""
    yield tok, "bare"
    if len(tok) > 4 and tok.endswith("ies"):
        yield tok[:-3] + "y", "s"
    if len(tok) > 3 and tok.endswith("es"):
        yield tok[:-2], "s"
    if len(tok) > 2 and tok.endswith("s") and not tok.endswith("ss"):
        yield tok[:-1], "s"


def contains_verb(text: str) -> bool:
    """True when the text appears to contain a verb.

    Auxiliaries and irregular past forms always count, as do regular "-ed" and "-ing" forms.
    Bare and "-s" forms of common verbs, which are often nouns ("annual report", "change log"),
    only count in a verb position: after a subject or a modal, or opening an imperative.
    """
    if text.isupper():
        text = text.lower()
    tokens = [t.lower() for t in word_tokenize(text) if t[0].isalnum()]
    for i, tok in enumerate(tokens):
        if tok in _ALWAYS_VERBS:
            return True
        if len(tok) >= 5 and tok.endswith("ed") and tok not in _NON_VERB_ED:
            return True
        if len(tok) >= 6 and tok.endswith("ing") and tok not in _NON_VERB_ING:
            return True
        prev = tokens[i - 1] if i > 0 else ""
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        for base, kind in _base_candidates(tok):
            if base not in _BASE_VERBS:
                continue
            if kind == "bare":
                if prev in _SUBJECTS or prev in _BARE_FORM_CUES:
                    return True
                if i == 0 and nxt in _IMPERATIVE_NEXT:
                    return True
                # -- plural subject + verb + object/preposition: "questions go to", "teams use the" --
                if (
                    len(prev) > 3
                    and prev.isalpha()
                    and prev.endswith("s")
                    and not prev.endswith("ss")
                    and prev not in _DETERMINERS
                    and prev not in _PREPOSITIONS
                    and (nxt in _IMPERATIVE_NEXT or nxt in _PREPOSITIONS)
                ):
                    return True
            elif prev in _SUBJECTS or (
                prev
                and prev.isalpha()
                and prev not in _DETERMINERS
                and prev not in _PREPOSITIONS
                and not prev.endswith(_ADJECTIVE_ENDINGS)
            ):
                return True
    return False


_COMMON_WORDS = _ALWAYS_VERBS | _BASE_VERBS | _DETERMINERS | _PREPOSITIONS | _SUBJECTS | frozenset(
    """time year people way day man woman child world life hand part place case week company system
    program question work government number night point home water room mother area money story
    fact month lot right study book eye job word business issue side kind head house service
    friend father power hour game line end member law car city community name president team
    minute idea kid body information back parent face others level office door health person art
    war history party result change morning reason research girl guy moment air teacher force
    education report data table page section summary total good new first last long great little
    own old big high different small large next early young important public bad same able not
    yes very well also just now then here there when where why how""".split()
)


def contains_english_word(text: str) -> bool:
    """True when any token is a common English word (used only when ``language_checks`` is on)."""
    for token in word_tokenize(text.lower()):
        word = "".join(ch for ch in token if "a" <= ch <= "z")
        if len(word) > 1 and word in _COMMON_WORDS:
            return True
    return False


# -- ratio checks ----------------------------------------------------------------------------


def sentence_count(text: str, min_length: Optional[int] = None) -> int:
    """Count sentences, ignoring those shorter than ``min_length`` words when it is given."""
    sentences = sent_tokenize(text)
    if not min_length:
        return len(sentences)
    return sum(1 for s in sentences if len(remove_punctuation(s).split()) >= min_length)


def under_non_alpha_ratio(text: str, threshold: float = 0.5) -> bool:
    """True when alphabetic characters make up less than ``threshold`` of non-space characters."""
    if not text:
        return False
    visible = [ch for ch in text if not ch.isspace()]
    if not visible:
        return False
    return sum(ch.isalpha() for ch in visible) / len(visible) < threshold


def exceeds_cap_ratio(text: str, threshold: float = 0.5) -> bool:
    """True when more than ``threshold`` of the words are capitalized, as in headings."""
    if sentence_count(text, 3) > 1:
        return False
    if text.isupper():
        return True
    words = [tok for tok in word_tokenize(text) if tok.isalpha()]
    if not words:
        return True
    capitalized = sum(1 for w in words if w.istitle() or w.isupper())
    return capitalized / len(words) > threshold


def is_possible_narrative_text(
    text: str,
    cap_threshold: float = 0.5,
    non_alpha_threshold: float = 0.5,
    languages: List[str] = ["eng"],
    language_checks: bool = False,
) -> bool:
    """True when the text reads like prose rather than a label, heading or number."""
    env_checks = os.environ.get("UNSTRUCTURED_LANGUAGE_CHECKS")
    if env_checks is not None:
        language_checks = env_checks.lower() == "true"
    if not text or text.isnumeric():
        return False
    if "eng" in languages and language_checks and not contains_english_word(text):
        return False
    cap_threshold = float(os.environ.get("UNSTRUCTURED_NARRATIVE_TEXT_CAP_THRESHOLD", cap_threshold))
    if exceeds_cap_ratio(text, threshold=cap_threshold):
        return False
    non_alpha_threshold = float(
        os.environ.get("UNSTRUCTURED_NARRATIVE_TEXT_NON_ALPHA_THRESHOLD", non_alpha_threshold)
    )
    if under_non_alpha_ratio(text, threshold=non_alpha_threshold):
        return False
    if "eng" in languages and sentence_count(text, 3) < 2 and not contains_verb(text):
        return False
    return True


def is_possible_title(
    text: str,
    sentence_min_length: int = 5,
    title_max_word_length: int = 12,
    non_alpha_threshold: float = 0.5,
    languages: List[str] = ["eng"],
    language_checks: bool = False,
) -> bool:
    """True when the text is short, single-sentence and mostly alphabetic, like a heading."""
    env_checks = os.environ.get("UNSTRUCTURED_LANGUAGE_CHECKS")
    if env_checks is not None:
        language_checks = env_checks.lower() == "true"
    if not text:
        return False
    if text.isupper() and P.ENDS_IN_PUNCT_RE.search(text):
        return False
    title_max_word_length = int(os.environ.get("UNSTRUCTURED_TITLE_MAX_WORD_LENGTH", title_max_word_length))
    if len(text.split(" ")) > title_max_word_length:
        return False
    non_alpha_threshold = float(os.environ.get("UNSTRUCTURED_TITLE_NON_ALPHA_THRESHOLD", non_alpha_threshold))
    if under_non_alpha_ratio(text, threshold=non_alpha_threshold):
        return False
    if text.endswith(","):
        return False
    if "eng" in languages and language_checks and not contains_english_word(text):
        return False
    if text.isnumeric():
        return False
    if sentence_count(text, min_length=sentence_min_length) > 1:
        return False
    return True


def is_bulleted_text(text: str) -> bool:
    return P.BULLET_RE.match(text.strip()) is not None


def contains_us_phone_number(text: str) -> bool:
    return P.US_PHONE_NUMBER_RE.search(text.strip()) is not None


def is_us_city_state_zip(text: str) -> bool:
    return P.US_CITY_STATE_ZIP_RE.match(text.strip()) is not None


def is_email_address(text: str) -> bool:
    return P.EMAIL_ADDRESS_RE.match(text.strip()) is not None


def is_possible_numbered_list(text: str) -> bool:
    return P.NUMBERED_LIST_RE.match(text.strip()) is not None
