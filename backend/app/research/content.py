"""Conservative rejection of pages made only of navigation/legal boilerplate."""

import re

LABELS = {
    "about",
    "press",
    "copyright",
    "contact us",
    "creator",
    "advertise",
    "developers",
    "terms",
    "privacy",
    "policy & safety",
    "how youtube works",
    "test new features",
    "sign in",
    "log in",
    "cookie policy",
    "privacy policy",
    "terms of service",
    "home",
}


def boilerplate_only(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return True

    def navigation(line):
        return line.casefold() in LABELS or bool(
            re.fullmatch(r"[©\s]*\d{4}\s+.{1,60}(?:LLC|Inc\.?|rights reserved\.?)", line, re.I)
        )

    # Do not strip or reject substantive content merely because it mentions privacy.
    return all(navigation(line) for line in lines)
