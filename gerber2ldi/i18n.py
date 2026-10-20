# gerber2ldi/i18n.py
"""Two-language (en/ru) gettext setup.

``_`` is a function that asks the CURRENT translation on every call, not a
binding replaced by setup_i18n(). So ``from gerber2ldi.i18n import _`` works
no matter whether the importing module was loaded before or after
setup_i18n(), and nothing is installed into builtins (KiCadStamp rebinds a
module global + builtins._ and needs a per-test fixture against the language
leaking between tests under ``-n auto``).
"""
import gettext
import locale
import os
import sys
from pathlib import Path

DOMAIN = "gerber2ldi"

ROOT_DIR = Path(__file__).parent.parent
# Source checkout: <repo>/locales. Installed package: <package>/locales (if
# locales are ever shipped as package-data). First candidate that exists wins.
_LOCALE_CANDIDATES = (ROOT_DIR / "locales", Path(__file__).parent / "locales")
LOCALE_DIR = next((p for p in _LOCALE_CANDIDATES if p.is_dir()), _LOCALE_CANDIDATES[0])

_translation: gettext.NullTranslations = gettext.NullTranslations()


def _(message: str) -> str:
    """Translate ``message`` with the translation selected by setup_i18n()."""
    return _translation.gettext(message)


def detect_language() -> str:
    """'ru' or 'en' from the environment, the Windows UI language as a fallback.

    POSIX/gettext precedence: LANGUAGE > LC_ALL > LC_MESSAGES > LANG. A binary
    rule: anything not starting with 'ru' is English.
    """
    language_env = os.environ.get("LANGUAGE")
    if language_env:
        first = language_env.split(":")[0]
        if first:
            return "ru" if first.startswith("ru") else "en"

    lang_env = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
                or os.environ.get("LANG"))
    if lang_env:
        return "ru" if lang_env.startswith("ru") else "en"

    # Windows does not populate LANG/LC_* itself, unlike a POSIX shell.
    if sys.platform == "win32":
        try:
            import ctypes
            # LCID for Russian = 1049 (0x419)
            if ctypes.windll.kernel32.GetUserDefaultUILanguage() == 1049:
                return "ru"
        except Exception:
            pass
        try:
            loc = locale.getlocale()[0]
            if loc and loc.startswith("ru"):
                return "ru"
        except Exception:
            pass

    return "en"


def setup_i18n(lang: str | None = None) -> str:
    """Select the translation for ``lang`` (detected when None); returns the code."""
    global _translation
    lang = lang or detect_language()
    try:
        _translation = gettext.translation(
            DOMAIN, localedir=str(LOCALE_DIR), languages=[lang], fallback=True)
    except Exception:
        _translation = gettext.NullTranslations()
    return lang
