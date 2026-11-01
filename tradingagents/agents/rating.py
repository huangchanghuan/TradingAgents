"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision; its free-text fallback is read here)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.

``extract_rating`` returns ``None`` when no rating can be found, and every
caller turns that into ``REVIEW`` rather than a tradeable position: a decision
nobody can read is not a Hold, and a Hold recorded in its place is quoted back to
the next run as a call that was never made (#1170).
"""

from __future__ import annotations

import re
import unicodedata

# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

# Signal emitted when the model's decision has no recognizable rating. It is not
# a tradeable position: it flags output that needs a human/re-run rather than
# silently degrading to Hold. Callers that map the signal onto the 5-tier enum
# (e.g. ``PortfolioRating(signal)``) should guard with ``is_review`` first.
RATING_REVIEW = "REVIEW"

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# Chinese renderings (see agents.labels): a Chinese-language report writes
# "评级：增持", so the parser must read those back to the canonical values.
_ZH_RATING_ALIASES: dict[str, str] = {
    "买入": "buy",
    "增持": "overweight",
    "持有": "hold",
    "减持": "underweight",
    "卖出": "sell",
}

def _canonical(value: str) -> str | None:
    """A written rating word to its canonical capitalized form, else None."""
    low = value.lower()
    if low in _ZH_RATING_ALIASES:
        return _ZH_RATING_ALIASES[low].capitalize()
    if low in _RATING_SET:
        return low.capitalize()
    return None

# Matches "Rating: X" / "rating - X" / "Rating — **X**" / "评级：X" — tolerates
# markdown bold wrappers and any dash or colon a model writes as the separator.
# "rating" must start a word, so "Operating margin: Sell-side" is not a label.
_RATING_LABEL_RE = re.compile(
    r"(?<![a-z])(?:rating\b|评级)[^:\-\u2010-\u2015]*[:\-\u2010-\u2015][\s*]*([\w一-鿿]+)",
    re.IGNORECASE)

# The decision's own rating line, in the shape the Portfolio Manager is asked to
# open with ("- **Rating**: X", "**Final Rating**: X", "## Our rating - X",
# "**评级**：增持"): an optional list marker, emphasis and heading marks, and
# only words naming the decision itself before "rating". "Consensus rating:
# Buy" or "Trader's rating: Buy" is someone else's rating. The Chinese branch
# tolerates the same emphasis/heading marks between the keyword and the
# separator as the English one, so a Chinese decision's opening line short-
# circuits exactly like an English one.
_OWN_QUALIFIER = r"(?:(?:final|our|overall|my|recommended|updated|revised|new|current)\s+)*"
_RATING_LINE_RE = re.compile(
    r"\s*(?P<item>(?:[-+*\u2022]|\d+[.)])\s+)?[\s*_#]*" + _OWN_QUALIFIER
    + r"(?:rating[^\w:\-\u2010-\u2015]*|评级[\s*_#]*)[:\-\u2010-\u2015][\s*]*(?P<value>[\w一-鿿]+)",
    re.IGNORECASE,
)

# A line presenting the scale rather than a decision ("Rating Scale: Buy, ...").
_RATING_SCALE_RE = re.compile(r"rating\s*(scale|options|legend)", re.IGNORECASE)

def extract_rating(text: str) -> str | None:
    """Extract a 5-tier rating from its label, or ``None`` if there is none.

    Reads an explicit "Rating: X" label (tolerant of markdown bold) in the
    NFKC-normalized text, so fullwidth punctuation like ``Rating：Overweight``
    matches as ASCII does: the decision's opening rating line, else its own
    rating lines or, failing those, every label, when they agree.
    """
    if not text:
        return None
    norm = unicodedata.normalize("NFKC", text)

    # The decision is asked to open with its rating, so a first line (after any
    # headings) in that shape is the call. A list item there may also be a quote
    # heading a list of other parties' ratings, so it counts with the decision's
    # other rating lines, which leave out list items; failing those, every label
    # counts. Either way they must agree: ratings that differ, with nothing
    # marking which one is the call, are no call (#1170). Lines presenting the
    # scale itself are a legend the model echoed, not a call.
    lines = [line for line in norm.splitlines() if line.strip() and not _RATING_SCALE_RE.search(line)]
    first = next((line for line in lines if not line.lstrip().startswith("#")
                  or _RATING_LINE_RE.match(line)), "")
    m = _RATING_LINE_RE.match(first)
    opening = m if m and _canonical(m.group("value")) else None
    if opening and not opening.group("item"):
        return _canonical(opening.group("value"))

    own = [_canonical(opening.group("value"))] if opening else []
    labels = []
    for line in lines:
        m = _RATING_LINE_RE.match(line)
        if m and not m.group("item") and _canonical(m.group("value")):
            own.append(_canonical(m.group("value")))
        labels += [v for v in (_canonical(w) for w in _RATING_LABEL_RE.findall(line)) if v]
    # Without a label there is no call to read: a rating word in the prose may be
    # one the text argues against ("not a Sell"), and reading it reports a
    # direction nobody decided.
    found = [v for v in own or labels if v]
    return found[0] if found and len(set(found)) == 1 else None


def parse_rating(text: str, default: str = RATING_REVIEW) -> str:
    """Extract a 5-tier rating, or ``REVIEW`` when the decision has none.

    For callers that need a string for every decision, such as the memory log's
    entry tag. The default is the review sentinel, never a tradeable rating.
    """
    rating = extract_rating(text)
    return rating if rating is not None else default


def run_rating(final_state: dict) -> str:
    """A finished run's rating: the Portfolio Manager's own, else read from its decision.

    The fallback serves a state without ``final_rating``, such as a run an older
    version completed and a checkpoint hands back unchanged.
    """
    return final_state.get("final_rating") or parse_rating(final_state.get("final_trade_decision", ""))


def is_review(signal: str) -> bool:
    """Whether a signal is the non-tradeable REVIEW sentinel (#1170)."""
    return signal == RATING_REVIEW
