from __future__ import annotations

import re
from dataclasses import dataclass

# Prefill of the clinic and Exam 1 / Exam 2 in Intake Review from the fax
# text. Comprehend has no structured "exam ordered" field, so the exam only
# appears inside summaries and document names. These are SUGGESTIONS: the
# reviewer sees them preselected and confirms; nothing is saved until they do.
# Deliberately conservative: a wrong prefill is worse than none.

# Words that do not tell exams apart.
GENERIC_TOKENS = frozenset(
    {
        "us",
        "ultrasound",
        "ultrasonography",
        "sonogram",
        "exam",
        "scan",
        "study",
        "imaging",
        "with",
        "without",
        "and",
        "of",
        "the",
        "for",
        "a",
        "w",
        "wo",
    }
)

# Words that refine an exam; they help choose between near-identical types
# (Complete vs Limited) but are not required to be present.
QUALIFIER_TOKENS = frozenset(
    {"complete", "limited", "bilateral", "bilat", "unilateral", "left", "right", "lt", "rt"}
)

_TOKEN = re.compile(r"[a-z0-9]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])\s+|\n+")
MAX_SNIPPET_CHARS = 300


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _stem(token: str) -> str:
    # Plural / possessive tolerance: "kidneys" == "kidney".
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _stems(text: str) -> set[str]:
    return {_stem(t) for t in tokens(text)}


def _sentences(texts: list[str]) -> list[str]:
    result: list[str] = []

    for text in texts:
        result.extend(part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip())

    return result


@dataclass(frozen=True)
class ExamMatch:
    type_id: int
    name: str
    fax_text: str
    score: float


@dataclass(frozen=True)
class ClinicMatch:
    calendar_id: int
    name: str
    reason: str


def suggest_exams(
    texts: list[str],
    exam_types: list[tuple[int, str]],
    limit: int = 2,
) -> list[ExamMatch]:
    """Picks up to `limit` exam types the fax text mentions.

    A type matches when every distinguishing word in its name (not generic like
    "ultrasound", not a qualifier like "limited") appears in the text. Among
    types that differ only by qualifiers (Complete / Limited), the one whose
    qualifiers also appear wins and the others are dropped.
    """
    texts = [t for t in texts if t]

    if not texts:
        return []

    text_stems = set().union(*(_stems(t) for t in texts))
    normalized = " ".join(" ".join(tokens(t)) for t in texts)

    best_by_signature: dict[frozenset[str], ExamMatch] = {}

    for type_id, name in exam_types:
        name_tokens = tokens(name)
        significant = {
            _stem(t) for t in name_tokens if t not in GENERIC_TOKENS and t not in QUALIFIER_TOKENS
        }

        if not significant or not significant <= text_stems:
            continue

        qualifiers = {_stem(t) for t in name_tokens if t in QUALIFIER_TOKENS}
        qualifier_hits = len(qualifiers & text_stems)
        qualifier_misses = len(qualifiers) - qualifier_hits

        full_name = " ".join(name_tokens)
        score = (
            len(significant) * 10
            + qualifier_hits * 3
            - qualifier_misses * 2
            + (5 if full_name and full_name in normalized else 0)
        )

        match = ExamMatch(type_id, name, _snippet(texts, significant), float(score))
        current = best_by_signature.get(frozenset(significant))

        if current is None or (match.score, -match.type_id) > (current.score, -current.type_id):
            best_by_signature[frozenset(significant)] = match

    ranked = sorted(best_by_signature.values(), key=lambda m: (-m.score, m.type_id))

    # A word set contained in a more specific match is not a second exam
    # ("abdominal" is part of "abdominal aorta").
    chosen: list[ExamMatch] = []

    for match in ranked:
        match_sig = frozenset(
            _stem(t)
            for t in tokens(match.name)
            if t not in GENERIC_TOKENS and t not in QUALIFIER_TOKENS
        )

        if any(
            match_sig
            < frozenset(
                _stem(t)
                for t in tokens(other.name)
                if t not in GENERIC_TOKENS and t not in QUALIFIER_TOKENS
            )
            for other in chosen
        ):
            continue

        chosen.append(match)

        if len(chosen) == limit:
            break

    return chosen


def _snippet(texts: list[str], significant: set[str]) -> str:
    """The sentence in the fax text that mentions the exam."""
    for sentence in _sentences(texts):
        if significant <= _stems(sentence):
            return sentence[:MAX_SNIPPET_CHARS]

    first = next(iter(sorted(significant)))

    for sentence in _sentences(texts):
        if first in _stems(sentence):
            return sentence[:MAX_SNIPPET_CHARS]

    return ""


def suggest_clinic(
    texts: list[str],
    calendars: list[tuple[int, str]],
) -> ClinicMatch | None:
    """Suggests a clinic only when the text names one unambiguously:

      * the clinic is written as a location ("Dallas Location"), or
      * the clinic's place name sits right next to "ScanX" ("ScanX Dallas"),
        with the generic words "location" / "clinic" ignored.

    Clinics are stored with the word "Location" removed ("Dallas"), so a bare
    city name is NOT enough: a referring practice's own address is full of
    them. Two clinics matching means no suggestion.
    """
    texts = [t for t in texts if t]
    normalized = " ".join(" ".join(tokens(t)) for t in texts)

    matches: dict[int, ClinicMatch] = {}

    for calendar_id, name in calendars:
        core = [t for t in tokens(name) if t not in {"location", "clinic", "scanx", "the"}]
        core_phrase = " ".join(core)

        if core_phrase and re.search(rf"\b{re.escape(core_phrase)} location\b", normalized):
            matches[calendar_id] = ClinicMatch(calendar_id, name, "Named on the fax")
            continue

        # The place must sit right next to "ScanX" ("ScanX Fairview",
        # "ScanX Health Dallas"), not merely share a sentence with it: the
        # referring practice's own city is often in the same sentence.
        adjacent = (
            rf"\bscanx(?: (?:health|clinic|imaging|llc))* {re.escape(core_phrase)}\b",
            rf"\b{re.escape(core_phrase)} scanx\b",
        )

        if core_phrase and any(re.search(pattern, normalized) for pattern in adjacent):
            matches[calendar_id] = ClinicMatch(calendar_id, name, "ScanX location named on the fax")

    return next(iter(matches.values())) if len(matches) == 1 else None
