"""Tier 0: deterministic matching of a semantic target against visual elements (no LLM).

Scores every element by text similarity to the target's queries (slot value, expected label,
text quoted/named in the description, role keywords), adds spatial evidence (region hint,
proximity to an anchor element for targets like "Add button for {{item}}"), and reports whether
the best candidate is confident and unambiguous.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..models.flow import Target
from ..models.segment import Element, MatchResult

# Keywords that label an element with a given semantic role.
ROLE_KEYWORDS: dict[str, list[str]] = {
    "search_field": ["search", "restaurant name", "search for", "what are you looking for", "dish"],
    "search_button": ["search"],
    "add_item": ["add"],
    "see_all": ["see all", "view all"],
    "cart": ["view cart", "go to cart", "cart", "checkout"],
    "continue": ["continue", "next", "proceed"],
    "confirm": ["confirm", "ok", "done", "apply", "save"],
    "dismiss": ["not now", "skip", "close", "cancel", "maybe later", "no thanks", "later"],
    "back_button": ["back"],
    "address": ["address", "deliver to", "home", "work"],
    "quantity_increase": ["+"],
    "place_order": ["place order", "pay", "proceed to pay", "confirm order", "make payment", "swipe to pay",
                    "slide to pay"],
}
# Roles whose visible label comes from ROLE_KEYWORDS while the slot value only locates the right
# element nearby (e.g. the ADD button on the card of the requested item).
LABEL_ROLES = {"add_item", "see_all", "cart", "continue", "confirm", "dismiss", "quantity_increase"}

_DESC_PATTERNS = [
    re.compile(r"[\"“”'‘’]([^\"“”]{2,80})[\"“”]"),
    re.compile(r"\b(?:matching|named|called|labell?ed|titled|saying|with text)\s+(.{2,80}?)\s*$", re.I),
]
_STOP = {"the", "a", "an", "for", "of", "to", "on", "in", "button", "field", "tap", "card", "item", "result",
         "matching", "named", "called", "associated", "with", "selected", "icon", "option", "and", "or", "row"}


def norm(text: str | None) -> str:
    """Casefold, strip accents/punctuation, unify apostrophes: "Domino’s - Pizza!" -> "dominos pizza"."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    t = re.sub(r"['’‘`´]", "", t)
    t = t.replace("&", " and ")
    t = re.sub(r"[^\w+]+", " ", t)
    return " ".join(t.split())


def _contains_seq(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def text_score(query: str, text: str) -> float:
    """Similarity of a normalized query to a normalized element text, in [0, 1]."""
    if not query or not text:
        return 0.0
    if query == text:
        return 1.0
    qt, tt = query.split(), text.split()
    if _contains_seq(tt, qt):  # element text contains the whole query
        return 0.82 + 0.16 * min(1.0, len(query) / len(text))
    best = 0.0
    if len(text) >= 3 and _contains_seq(qt, tt):  # element text is part of the query
        best = 0.60 + 0.25 * len(text) / len(query)
    ratio = SequenceMatcher(None, query, text).ratio()
    partial = 0.0
    n = len(qt)
    for i in range(max(1, len(tt) - n + 1)):  # query vs same-length token windows of the text
        partial = max(partial, SequenceMatcher(None, query, " ".join(tt[i:i + n])).ratio())
    fuzzy = max(ratio, partial * (0.95 if len(tt) > n else 1.0))
    if fuzzy >= 0.75:
        best = max(best, 0.9 * fuzzy)
    coverage = len(set(qt) & set(tt)) / len(set(qt))
    return max(best, 0.6 * coverage)


def keyword_score(keyword: str, text: str) -> float:
    kw, tt = keyword.split(), text.split()
    if not kw or not tt:
        return 0.0
    if tt == kw:
        return 0.95
    if tt[:len(kw)] == kw:
        return 0.88
    if _contains_seq(tt, kw):
        return 0.80
    return 0.0


@dataclass
class Queries:
    primary: list[str] = field(default_factory=list)  # label the element should carry
    anchor: list[str] = field(default_factory=list)   # label of a nearby element (relational targets)
    keywords: list[str] = field(default_factory=list)  # role keywords (weaker evidence)


def build_queries(target: Target, slots: dict[str, object]) -> Queries:
    """Target (already slot-substituted) -> what text to look for."""
    q = Queries()
    slot_value = slots.get(target.slot) if target.slot else None
    slot_text = norm(str(slot_value)) if slot_value not in (None, "") else ""
    role = (target.semantic_role or "").lower()
    keywords = [norm(k) for k in ROLE_KEYWORDS.get(role, [])]

    label = norm(target.text) if target.text else ""
    if role in LABEL_ROLES or (label and slot_text and label != slot_text):
        # Relational target: find the labelled element near the slot's element.
        q.primary = [label] if label else []
        q.keywords = keywords
        if slot_text:
            q.anchor = [slot_text]
    else:
        if label:
            q.primary.append(label)
        if slot_text and slot_text not in q.primary:
            q.primary.append(slot_text)
        if not q.primary:
            for pat in _DESC_PATTERNS:
                for m in pat.finditer(target.description):
                    phrase = norm(m.group(1))
                    if phrase and phrase not in q.primary:
                        q.primary.append(phrase)
        q.keywords = keywords
    if not q.primary and not q.keywords:
        words = [w for w in norm(target.description).split() if w not in _STOP and len(w) > 2]
        q.keywords = [" ".join(words)] + words if words else []
    return q


def _region_of(el: Element, height: int) -> str:
    cy = el.center[1]
    return "top" if cy < height / 3 else "middle" if cy < 2 * height / 3 else "bottom"


def _overlaps(a: Element, b: Element) -> bool:
    """True when one box (nearly) contains the other: the same control seen twice."""
    def inside(i, o):
        ix = max(0, min(i[2], o[2]) - max(i[0], o[0]))
        iy = max(0, min(i[3], o[3]) - max(i[1], o[1]))
        area = max(1, (i[2] - i[0]) * (i[3] - i[1]))
        return ix * iy / area >= 0.8
    return inside(a.bbox, b.bbox) or inside(b.bbox, a.bbox)


def wrapped_texts(elements: list[Element], width: int) -> dict[int, str]:
    """Element index -> its text joined with the left-aligned line right below it, for titles that
    OCR splits over two lines ("Classic Herbed Chicken &" / "Capsicum")."""
    out: dict[int, str] = {}
    texts = [e for e in elements if e.text]
    for e in texts:
        line_h = max(1, e.bbox[3] - e.bbox[1])
        # Same column, directly below, and a line of similar height (not a chip above a card).
        below = [f for f in texts if f.index != e.index and abs(f.bbox[0] - e.bbox[0]) <= 0.03 * width
                 and -0.2 * line_h <= f.bbox[1] - e.bbox[3] <= 0.6 * line_h
                 and 0.6 <= (f.bbox[3] - f.bbox[1]) / line_h <= 1.6]
        if below:
            nxt = min(below, key=lambda f: f.bbox[1])
            out[e.index] = f"{e.text} {nxt.text}"
    return out


def _best_text_score(query: str, el: Element, wrapped: dict[int, str]) -> float:
    s = text_score(query, norm(el.text))
    if el.index in wrapped:
        s = max(s, text_score(query, norm(wrapped[el.index])))
    return s


def _score_label(el: Element, queries: Queries, wrapped: dict[int, str]) -> tuple[float, str]:
    t = norm(el.text)
    if not t:
        return 0.0, ""
    best, why = 0.0, ""
    for query in queries.primary:
        s = _best_text_score(query, el, wrapped)
        if s > best:
            best, why = s, f"text~'{query}'"
    for kw in queries.keywords:
        s = keyword_score(kw, t) * (0.9 if queries.primary else 1.0)
        if s > best:
            best, why = s, f"keyword '{kw}'"
    return best, why


def match_deterministic(target: Target, elements: list[Element], slots: dict[str, object], *,
                        width: int | None, height: int | None, confident: float, margin: float,
                        min_candidate: float, max_candidates: int = 25) -> MatchResult:
    queries = build_queries(target, slots)
    height = height or max((e.bbox[3] for e in elements), default=1)
    width = width or max((e.bbox[2] for e in elements), default=1)
    # Ignore the status bar (clock, battery), which is never a target.
    status_bar = 0.025 * height

    wrapped = wrapped_texts(elements, width)
    anchor: Element | None = None
    if queries.anchor:
        scored_anchor = [(max(_best_text_score(a, e, wrapped) for a in queries.anchor), e)
                         for e in elements if e.text]
        scored_anchor = [p for p in scored_anchor if p[0] >= confident]
        if scored_anchor:
            anchor = max(scored_anchor, key=lambda p: (p[0], -p[1].index))[1]

    scored: list[tuple[float, Element, str]] = []
    for el in elements:
        if el.bbox[3] <= status_bar:
            continue
        s, why = _score_label(el, queries, wrapped)
        if s <= 0:
            continue
        if target.region:
            s += 0.04 if _region_of(el, height) == target.region else -0.10
        if queries.anchor:
            if anchor is None:
                s *= 0.7  # the item the button belongs to isn't visible
            elif el.index != anchor.index:
                ax, ay = anchor.center
                ex, ey = el.center
                dy = ey - ay
                # Buttons sit on the item's card, usually beside or below its name (horizontal
                # offset matters little); elements above the name belong to the previous card.
                dist = abs(dy) + 0.15 * abs(ex - ax) + (0.15 * height if dy < -0.05 * height else 0)
                s *= 1.0 - 0.35 * min(1.0, dist / (0.5 * height))
                why += f", near anchor #{anchor.index}"
        s = max(0.0, min(1.0, s))
        if s >= min_candidate:
            scored.append((s, el, why))

    scored.sort(key=lambda p: (-p[0], p[1].index))
    candidates = [{"index": e.index, "text": e.text, "type": e.type, "score": round(s, 3), "why": why}
                  for s, e, why in scored[:max_candidates]]
    if not scored:
        return MatchResult(found=False, method="none", reason="no element text matches the target",
                           candidates=[])

    best_s, best_el, best_why = scored[0]

    def same_target(e: Element) -> bool:
        # Nested boxes, or the same label repeated right next to it (title + logo text of one card).
        if _overlaps(e, best_el):
            return True
        return bool(norm(e.text)) and norm(e.text) == norm(best_el.text) and \
            abs(e.center[1] - best_el.center[1]) <= 0.08 * height

    rival = next(((s, e) for s, e, _ in scored[1:] if not same_target(e)), None)
    ambiguous = rival is not None and best_s - rival[0] < margin
    if best_s >= confident and not ambiguous:
        x, y = best_el.center
        return MatchResult(found=True, index=best_el.index, confidence=round(best_s, 3), method="deterministic",
                           x=x, y=y, candidates=candidates, reason=f"matched #{best_el.index} ({best_why})")
    reason = (f"ambiguous: #{best_el.index} {best_s:.2f} vs #{rival[1].index} {rival[0]:.2f}" if ambiguous
              else f"best candidate #{best_el.index} scored {best_s:.2f} < {confident:.2f}")
    return MatchResult(found=False, method="none", ambiguous=ambiguous, confidence=round(best_s, 3),
                       candidates=candidates, reason=reason)


def bbox_center(bbox: tuple[int, int, int, int] | list[int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bbox
    return (x1 + x2) // 2, (y1 + y2) // 2


def point_in_screen(x: float, y: float, width: int, height: int) -> bool:
    return 0 <= x < width and 0 <= y < height
