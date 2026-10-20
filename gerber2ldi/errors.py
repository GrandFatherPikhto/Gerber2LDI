# gerber2ldi/errors.py
"""Expected failures: the thing the user did wrong, not a bug in us.

The CLI prints the message as ONE line and returns a non-zero code, with no
traceback (plan §2.4): a missing file or a broken profile is an answer, not a
crash. Anything else — an unexpected exception — is a bug and must keep its
traceback, so nothing here is ever raised as a blanket `except`.
"""


class Gerber2LDIError(Exception):
    """Base of every expected refusal. The message is user-facing."""


class InputFileError(Gerber2LDIError):
    """The input file cannot be read: missing, not a Gerber/Excellon, broken."""


class ProfileError(Gerber2LDIError):
    """The machine profile or its calibration file is wrong — with the field named."""
