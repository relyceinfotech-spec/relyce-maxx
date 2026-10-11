"""Structural completion checks, not a claim of semantic correctness."""

import re

from app.core.errors import ModelIncompleteResponse


def validate_answer(answer: str):
    fence = None
    for line in answer.splitlines():
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if not match:
            continue
        marker, rest = match.groups()
        if fence is None:
            fence = marker
        elif marker[0] == fence[0] and len(marker) >= len(fence) and not rest.strip():
            fence = None
    if fence is not None:
        raise ModelIncompleteResponse("Unclosed code block; answer withheld")
    tail = answer.rstrip()
    if re.search(r"\[(?:S|K|P)\d*$", tail) or re.search(
        r"\b(?:because|such as|for example|and then)\s*[:,]?$", tail, re.I
    ):
        raise ModelIncompleteResponse("Dangling sentence or citation; answer withheld")
