"""Tests for gerber2ldi/i18n.py and the en/ru catalogs.

Adapted from KiCadStamp's tests/utils/test_i18n.py: language precedence, the
catalog completeness gate (every _() string of the shipped source is in both
catalogs, the compiled ru .mo matches the .po) and the msgstr integrity rules
that `pybabel compile` does not check ({name}-style placeholders).
"""
import ast
import gettext
import re
import string
from pathlib import Path

from gerber2ldi.i18n import _, detect_language, setup_i18n

ROOT = Path(__file__).resolve().parent.parent
EN_PO = ROOT / "locales" / "en" / "LC_MESSAGES" / "gerber2ldi.po"
RU_PO = ROOT / "locales" / "ru" / "LC_MESSAGES" / "gerber2ldi.po"
RU_MO = RU_PO.with_suffix(".mo")

# The shipped packages — the same scope as pyproject.toml's packages.find.
SHIPPED_DIRS = (ROOT / "gerber2ldi", ROOT / "gui")

# A string that is in the catalogs for as long as the CLI has a description.
KNOWN_MSGID = "Laser direct imaging of photoresist from Gerber files."
KNOWN_RU = "Засветка фоторезиста лазером по файлам Gerber."

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)")
_LOCALE_VARS = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")


def _set_locale(monkeypatch, **env):
    for name in _LOCALE_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


class TestDetectLanguagePrecedence:
    """LANGUAGE > LC_ALL > LC_MESSAGES > LANG, default English."""

    def test_lang_ru(self, monkeypatch):
        _set_locale(monkeypatch, LANG="ru_RU.UTF-8")
        assert detect_language() == "ru"

    def test_lang_non_ru(self, monkeypatch):
        _set_locale(monkeypatch, LANG="de_DE.UTF-8")
        assert detect_language() == "en"

    def test_nothing_set_defaults_to_english(self, monkeypatch):
        _set_locale(monkeypatch)
        assert detect_language() == "en"

    def test_lc_all_overrides_lang(self, monkeypatch):
        _set_locale(monkeypatch, LC_ALL="ru_RU.UTF-8", LANG="en_US.UTF-8")
        assert detect_language() == "ru"

    def test_lc_messages_before_lang(self, monkeypatch):
        _set_locale(monkeypatch, LC_MESSAGES="ru_RU.UTF-8")
        assert detect_language() == "ru"

    def test_language_takes_priority_over_everything(self, monkeypatch):
        _set_locale(monkeypatch, LANGUAGE="ru:en", LC_ALL="en_US.UTF-8",
                    LANG="en_US.UTF-8")
        assert detect_language() == "ru"

    def test_language_only_first_entry_matters(self, monkeypatch):
        _set_locale(monkeypatch, LANGUAGE="en:ru", LC_ALL="ru_RU.UTF-8")
        assert detect_language() == "en"


class TestSetupI18n:

    def test_ru_translates_a_known_string(self):
        setup_i18n("ru")
        assert _(KNOWN_MSGID) == KNOWN_RU

    def test_en_leaves_a_known_string_in_english(self):
        setup_i18n("en")
        assert _(KNOWN_MSGID) == KNOWN_MSGID

    def test_underscore_imported_before_setup_follows_it(self):
        """The reason `_` is a function: a binding taken at import time (the
        `from ... import _` at the top of this file) must see a later
        setup_i18n(), whatever the import order."""
        setup_i18n("ru")
        assert _(KNOWN_MSGID) == KNOWN_RU
        setup_i18n("en")
        assert _(KNOWN_MSGID) == KNOWN_MSGID

    def test_unknown_locale_falls_back_without_raising(self, monkeypatch):
        _set_locale(monkeypatch, LANG="fr_FR.UTF-8")
        assert setup_i18n() == "en"
        assert _(KNOWN_MSGID) == KNOWN_MSGID


def _unescape_po_string(raw: str) -> str:
    """One or more adjacent "..." PO lines -> the decoded string. Not
    unicode_escape — that mangles the Cyrillic of the ru catalog."""
    body = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', raw))
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            n = body[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(n, n))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _po_entries(po_path: Path):
    """(msgid, msgstr) for every entry with BOTH sides non-empty. Multi-line
    msgids are parsed whole (i18troubles.md, trap 1)."""
    text = po_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'msgid ((?:"(?:[^"\\]|\\.)*"\n?)+)msgstr ((?:"(?:[^"\\]|\\.)*"\n?)+)')
    for m in pattern.finditer(text):
        mid = _unescape_po_string(m.group(1))
        mstr = _unescape_po_string(m.group(2))
        if mid and mstr:
            yield mid, mstr


def _po_all_msgids(po_path: Path) -> set:
    """Every msgid, translated or not (an empty msgstr falls back to English);
    obsolete #~ entries excluded."""
    text = po_path.read_text(encoding="utf-8")
    text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("#~"))
    out: set = set()
    for m in re.finditer(r'^msgid ((?:"(?:[^"\\]|\\.)*"\n?)+)', text, re.M):
        mid = _unescape_po_string(m.group(1))
        if mid:
            out.add(mid)
    return out


def _source_msgids() -> set:
    """Every _("...") literal in the shipped source, via ast — implicit string
    concatenation is folded by the parser (i18troubles.md, trap 2)."""
    msgs: set = set()
    for base in SHIPPED_DIRS:
        for py in base.rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "_" and node.args):
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        msgs.add(arg.value)
    return msgs


class TestCatalogCompleteness:

    def test_scan_is_not_blind(self):
        """Guards the tests below against a moved package or a path typo
        passing over an empty scan."""
        assert KNOWN_MSGID in _source_msgids()

    def test_every_source_string_is_in_en_catalog(self):
        missing = sorted(_source_msgids() - _po_all_msgids(EN_PO))
        assert not missing, f"strings missing from {EN_PO.relative_to(ROOT)}: {missing}"

    def test_every_source_string_is_in_ru_catalog(self):
        missing = sorted(_source_msgids() - _po_all_msgids(RU_PO))
        assert not missing, f"strings missing from {RU_PO.relative_to(ROOT)}: {missing}"

    def test_compiled_ru_mo_is_in_sync_with_po(self):
        """The .mo is what users read: every translated .po entry must be in
        it with the SAME text (a .po edit never recompiled ships the old
        wording; a compile without --use-fuzzy drops entries — trap 3)."""
        with open(RU_MO, "rb") as f:
            catalog = getattr(gettext.GNUTranslations(f), "_catalog", {})
        assert catalog, "the compiled .mo has an empty catalog"
        missing = []
        stale = []
        for mid, mstr in _po_entries(RU_PO):
            if mid not in catalog:
                missing.append(mid)
            elif catalog[mid] != mstr:
                stale.append((mid[:60], mstr[:60], catalog[mid][:60]))
        assert not missing, (
            "translated entries missing from the compiled .mo — run "
            "`pybabel compile -d locales -D gerber2ldi --use-fuzzy`: " + repr(missing))
        assert not stale, (
            "the compiled .mo carries an older translation than the .po — "
            "(msgid, .po, .mo): " + repr(stale))


class TestLocaleCatalogIntegrity:
    """What `pybabel compile` does not reject: {name}-style placeholders."""

    def test_ru_msgstr_is_valid_format_string(self):
        broken = []
        for mid, mstr in _po_entries(RU_PO):
            try:
                list(string.Formatter().parse(mstr))
            except ValueError as e:
                broken.append((mid[:70], mstr[:70], str(e)))
        assert not broken, f"malformed format string(s) (msgid, msgstr, error): {broken}"

    def test_ru_msgstr_placeholders_are_a_subset_of_msgid(self):
        """An invented placeholder raises KeyError at the .format() call."""
        invented = []
        for mid, mstr in _po_entries(RU_PO):
            extra = set(_PLACEHOLDER_RE.findall(mstr)) - set(_PLACEHOLDER_RE.findall(mid))
            if extra:
                invented.append((mid[:70], mstr[:70], sorted(extra)))
        assert not invented, f"msgstr invents placeholder(s): {invented}"

    def test_ru_msgstr_does_not_drop_a_msgid_placeholder(self):
        """A dropped placeholder does not crash — the value silently
        disappears from the message (KiCadStamp, 2026-09-13)."""
        lost = []
        for mid, mstr in _po_entries(RU_PO):
            dropped = set(_PLACEHOLDER_RE.findall(mid)) - set(_PLACEHOLDER_RE.findall(mstr))
            if dropped:
                lost.append((mid[:70], mstr[:70], sorted(dropped)))
        assert not lost, f"msgstr drops placeholder(s): {lost}"
