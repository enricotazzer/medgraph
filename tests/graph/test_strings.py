"""The patient page's interface text: every key in both languages, and every key it uses."""

import re
from pathlib import Path

from medgraph.cohort import SYNTHETIC_BANNER
from medgraph.graph import view

STATIC = Path(view.__file__).parent / "static"
VARIABLE = re.compile(r"\{(\w+)\}")


def test_both_languages_have_every_key_with_the_same_variables() -> None:
    strings = view.strings()
    assert set(strings) == {"en", "it"}
    assert set(strings["en"]) == set(strings["it"])
    for key, english in strings["en"].items():
        italian = strings["it"][key]
        assert english.strip()
        assert italian.strip()
        assert set(VARIABLE.findall(english)) == set(VARIABLE.findall(italian)), key


def test_every_key_the_page_uses_exists() -> None:
    keys = set(view.strings()["en"])
    script = (STATIC / "viewer.js").read_text(encoding="utf-8")
    page = (STATIC / "viewer.html").read_text(encoding="utf-8")
    used = set(re.findall(r'\bt\("([\w.\-]+)"', script))
    used |= set(re.findall(r'data-t(?:-aria|-placeholder)?="([\w.\-]+)"', page))
    used |= set(re.findall(r'"data-t": "([\w.\-]+)"', script))
    # Keys built from data: kinds, statuses, rule names, chart names, list names.
    for prefix in ("kind.", "st.", "rule.", "chart.", "gender."):
        assert any(k.startswith(prefix) for k in keys), prefix
    assert used - keys == set()


def test_month_and_weekday_lists_have_twelve_and_seven_names() -> None:
    for strings in view.strings().values():
        assert len(strings["months"].split(",")) == 12
        assert len(strings["months.short"].split(",")) == 12
        assert len(strings["weekdays"].split(",")) == 7
        assert len(strings["weekdays.short"].split(",")) == 7


def test_the_page_recognises_the_synthetic_banner_to_translate_it() -> None:
    assert view.strings()["en"]["banner.synthetic"] == SYNTHETIC_BANNER
