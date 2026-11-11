"""What leaves Qampus with a reply (qone-docs#55, threat I on TB6): before the conversation is sent
to the provider, emails, Colombian document numbers (6 to 10 digits, also written 1.075.234.567)
and phone numbers (+57 and 10 digits, or a 10-digit mobile that starts with 3) become `[redacted]`.
Codes like ISW-602, years like 2026, times and peso amounts with `$` are kept: the advisor needs
them. A pure function: the stored message keeps what the student wrote."""

import re

REDACTED = "[redacted]"
_BEFORE = r"(?<![\w.$-])(?<!\$ )"  # not inside a code, a UUID, a decimal or a peso amount
_AFTER = r"(?![\w-]|[.,]\d)"
_PATTERNS = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),  # email
    re.compile(r"\+57[\s.-]?(?:\d[\s.-]?){9}\d" + _AFTER),  # +57 and the 10-digit number
    re.compile(_BEFORE + r"3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}" + _AFTER),  # mobile
    re.compile(_BEFORE + r"(?:\d{6,10}|\d{1,3}(?:\.\d{3}){2,3})" + _AFTER),  # document number
)


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text
