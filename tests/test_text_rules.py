from __future__ import annotations

import pytest

from tryworks.cleaners import core as cleaners
from tryworks.partition import text_type as tt


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The company reported strong growth in the third quarter.", True),
        ("Click the button below to continue", True),
        ("Questions go to the procurement desk.", True),
        ("The report shows growth", True),
        ("Revenue increased by 12 percent", True),
        ("Orders ship today. Returns are free.", True),
        ("Annual Report 2025", False),
        ("annual report and change log", False),
        ("Table of Contents", False),
        ("SHIPPING POLICY", False),
        ("1234", False),
        ("------BREAK------", False),
        ("", False),
    ],
)
def test_narrative_text(text, expected):
    assert tt.is_possible_narrative_text(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Introduction", True),
        ("Quarterly Operations Memo", True),
        ("This sentence is long enough. And so is this second sentence here.", False),
        ("ENDS WITH A PERIOD.", False),
        ("Dear Sir,", False),
        ("2026", False),
        ("one two three four five six seven eight nine ten eleven twelve thirteen", False),
    ],
)
def test_title(text, expected):
    assert tt.is_possible_title(text) is expected


def test_title_word_limit_can_be_raised_by_environment(monkeypatch):
    long_title = "one two three four five six seven eight nine ten eleven twelve thirteen"
    monkeypatch.setenv("UNSTRUCTURED_TITLE_MAX_WORD_LENGTH", "20")
    assert tt.is_possible_title(long_title)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("• item", True), ("- item", True), ("▪ item", True), ("-5 degrees", False), ("**bold**", False), ("item", False)],
)
def test_bulleted_text(text, expected):
    assert tt.is_bulleted_text(text) is expected


def test_small_detectors():
    assert tt.is_email_address("jane.doe@example.co.uk")
    assert not tt.is_email_address("mail jane@example.com")
    assert tt.is_us_city_state_zip("Doylestown, PA 18901")
    assert tt.is_us_city_state_zip("DOYLESTOWN, PENNSYLVANIA 18901")
    assert not tt.is_us_city_state_zip("Rotterdam, 3011 AA")
    assert tt.contains_us_phone_number("Call 867-5309 today")
    assert tt.is_possible_numbered_list("2) Second step")
    assert not tt.is_possible_numbered_list("1.2 Scope")


def test_sentence_splitting_respects_abbreviations():
    assert tt.sent_tokenize("Dr. Smith went to Washington. He met the U.S. president! Was it fun? yes.") == [
        "Dr. Smith went to Washington.",
        "He met the U.S. president!",
        "Was it fun? yes.",
    ]
    assert tt.sentence_count("Short. This one has five words.", min_length=5) == 1


def test_ratios():
    assert tt.under_non_alpha_ratio("12,345.67 !!!")
    assert not tt.under_non_alpha_ratio("mostly letters 1")
    assert tt.exceeds_cap_ratio("Risk Factors And Other Items")
    assert not tt.exceeds_cap_ratio("Risk factors are described below")


@pytest.mark.parametrize(
    ("fn", "given", "expected"),
    [
        (cleaners.clean_bullets, "● An excellent point!", "An excellent point!"),
        (cleaners.clean_bullets, "No bullet here", "No bullet here"),
        (cleaners.clean_ordered_bullets, "1.1 A very important point", "A very important point"),
        (cleaners.clean_ordered_bullets, "3.14159 is pi", "3.14159 is pi"),
        (cleaners.clean_extra_whitespace, "ITEM 1.     BUSINESS\n", "ITEM 1. BUSINESS"),
        (cleaners.clean_dashes, "ITEM 1. -BUSINESS", "ITEM 1.  BUSINESS"),
        (cleaners.clean_trailing_punctuation, "ITEM 1.", "ITEM 1"),
        (cleaners.clean_ligatures, "The beneﬁts of ﬂow", "The benefits of flow"),
        (cleaners.clean_non_ascii_chars, "\x88This text contains\xa0non-ascii", "This text containsnon-ascii"),
        (cleaners.replace_unicode_quotes, "‘single’ and “double”", "'single' and \"double\""),
        (cleaners.remove_punctuation, "“Hello,” world!", "Hello world"),
        (cleaners.replace_mime_encodings, "5 w=E2=80=99s", "5 w’s"),
    ],
)
def test_cleaners(fn, given, expected):
    assert fn(given) == expected


def test_paragraph_grouping():
    wrapped = "The big red fox\nis walking down the lane.\n\nAt the end of the lane\nthe fox met a bear."
    assert cleaners.group_broken_paragraphs(wrapped) == (
        "The big red fox is walking down the lane.\n\nAt the end of the lane the fox met a bear."
    )
    address = "Keel & Rope\n12 Dock Road\nRotterdam"
    assert cleaners.group_broken_paragraphs(address) == "Keel & Rope\n\n12 Dock Road\n\nRotterdam"
    bullets = "• The big red fox\nis walking.\n• At the end of the lane\nthe fox met a bear."
    assert cleaners.group_broken_paragraphs(bullets) == (
        "• The big red fox is walking.\n\n• At the end of the lane the fox met a bear."
    )
    assert cleaners.auto_paragraph_grouper("one line\nanother line\nthird line") == "one line\n\nanother line\n\nthird line"


def test_clean_combines_options():
    assert cleaners.clean("• THE END.  ", bullets=True, trailing_punctuation=True, lowercase=True) == "the end"
    assert cleaners.clean_prefix("SUMMARY: This is the best summary", r"(SUMMARY|DESC):") == "This is the best summary"
    assert cleaners.clean_postfix("The end! END", r"(END|STOP)", ignore_case=True) == "The end!"
