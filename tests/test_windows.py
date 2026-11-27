from datetime import date

from suva.windows import challenge_window, dev_window, months_back


def test_months_back_clamps_day():
    assert months_back(date(2026, 11, 27), 3) == date(2026, 8, 27)
    assert months_back(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert months_back(date(2026, 1, 15), 3) == date(2025, 10, 15)


def test_challenge_window_widened():
    w = challenge_window(date(2026, 11, 27))
    assert w.published_from == date(2026, 8, 27)
    assert w.start == date(2026, 6, 13) and w.end == date(2026, 11, 27)


def test_dev_window_is_xlsx_period():
    w = dev_window()
    assert (w.start, w.end) == (date(2025, 10, 1), date(2026, 3, 31))
