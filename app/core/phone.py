from __future__ import annotations

import re

_MASK_CHARS = re.compile(r"[xX*#•]")


def normalize_e164(raw: str | None) -> str | None:
    """Normalises a phone/fax number to E.164, assuming US/Canada (+1) when no
    country code is given.

    Returns None for blanks, masked numbers (e.g. "(214) 648-3XXX") and
    anything that is not a valid-looking NANP or "+"-prefixed international
    number. Never raises.
    """
    if not raw:
        return None

    value = raw.strip()

    # Strip a trailing extension ("x123", "ext. 4") before looking for masks,
    # otherwise the "x" would read as a masked digit.
    value = re.sub(r"(?i)\s*(?:ext\.?|extension|x)\s*\d{1,6}\s*$", "", value)

    if _MASK_CHARS.search(value):
        return None

    digits = re.sub(r"\D", "", value)

    if value.startswith("+"):
        return f"+{digits}" if 8 <= len(digits) <= 15 else None

    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]

    if len(digits) != 10:
        return None

    # NANP: area code and exchange cannot start with 0 or 1.
    if digits[0] in "01" or digits[3] in "01":
        return None

    return f"+1{digits}"
