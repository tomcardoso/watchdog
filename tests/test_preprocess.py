"""Tests for preprocess helpers that don't require Docling to be installed."""

from watchdog.pipeline.preprocess import is_garbled


def test_garbled_clean_text():
    assert not is_garbled("This is a normal sentence with words and spaces.")


def test_garbled_empty_string():
    # Empty text is not considered garbled — no text layer at all is a separate check
    assert not is_garbled("")


def test_garbled_symbol_heavy():
    assert is_garbled("©®™†‡§¶•∞≠≈∂∑∏√∫")


def test_garbled_mixed_borderline():
    # 50% alphanumeric — well below the 0.75 default threshold
    assert is_garbled("abc©©©")


def test_garbled_numbers_and_spaces_count_as_readable():
    assert not is_garbled("12345 67890 page 4 of 12")
