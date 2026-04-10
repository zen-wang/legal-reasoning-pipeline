"""
Coarse section splitter for judicial opinion text.

Splits opinion text into sections (HEADER, BACKGROUND, ANALYSIS, CONCLUSION, OTHER)
to reduce LLM input size by stripping court headers, party lists, and boilerplate.

Designed for robustness:
    - If no clear section boundaries found, returns full text as single ANALYSIS section
    - Handles both district court (explicit headings) and appellate (Roman numerals) formats
    - Never fails or returns empty output
"""

from __future__ import annotations

import re
from typing import Literal, NamedTuple

SectionType = Literal["HEADER", "BACKGROUND", "ANALYSIS", "CONCLUSION", "OTHER"]


class Section(NamedTuple):
    """A section of opinion text with its classification."""

    section_type: SectionType
    heading: str         # the heading text that triggered this section (may be empty)
    text: str            # the section content
    start_char: int      # character offset in original text
    end_char: int        # character offset end


class OpinionSections(NamedTuple):
    """Result of splitting an opinion into sections."""

    sections: list[Section]
    raw_text: str        # original full text (preserved for fallback)


# ---------------------------------------------------------------------------
# Heading patterns
# ---------------------------------------------------------------------------

# Patterns that indicate section boundaries (compiled once)
_HEADING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # ALL-CAPS section headings (most reliable)
    ("caps_heading", re.compile(
        r"^\s{0,20}("
        r"BACKGROUND|FACTUAL BACKGROUND|FACTS|STATEMENT OF FACTS|"
        r"PROCEDURAL BACKGROUND|PROCEDURAL HISTORY|"
        r"DISCUSSION|ANALYSIS|LEGAL ANALYSIS|LEGAL STANDARD|"
        r"STANDARD OF REVIEW|STANDARDS OF REVIEW|"
        r"CONCLUSION|DISPOSITION|ORDER|"
        r"OPINION|SUMMARY|INTRODUCTION"
        r")\s*$",
        re.MULTILINE | re.IGNORECASE,
    )),
    # Roman numeral headings: "I.", "II.", "III.", etc.
    ("roman", re.compile(
        r"^\s{0,20}((?:I{1,4}V?|VI{0,3})\.)\s+(.+)$",
        re.MULTILINE,
    )),
    # Lettered headings: "A.", "B.", "C." (at start of line, uppercase)
    ("letter", re.compile(
        r"^\s{0,20}([A-F]\.)\s+(.+)$",
        re.MULTILINE,
    )),
]

# Classify heading text into section types
_BACKGROUND_KEYWORDS = re.compile(
    r"background|facts|procedural history|introduction|statement",
    re.IGNORECASE,
)
_ANALYSIS_KEYWORDS = re.compile(
    r"discussion|analysis|legal standard|standard of review|opinion|merits",
    re.IGNORECASE,
)
_CONCLUSION_KEYWORDS = re.compile(
    r"conclusion|disposition|order|so ordered|judgment",
    re.IGNORECASE,
)


def _classify_heading(heading: str) -> SectionType:
    """Classify a heading string into a section type."""
    if _CONCLUSION_KEYWORDS.search(heading):
        return "CONCLUSION"
    if _BACKGROUND_KEYWORDS.search(heading):
        return "BACKGROUND"
    if _ANALYSIS_KEYWORDS.search(heading):
        return "ANALYSIS"
    return "OTHER"


# ---------------------------------------------------------------------------
# Header detection (court boilerplate at the start)
# ---------------------------------------------------------------------------

# Court header typically ends before the first substantive heading or
# after the attorney listing. We look for patterns like "OPINION" or
# the first Roman numeral heading.
_HEADER_END_PATTERNS = [
    re.compile(r"^\s{0,20}(?:OPINION|MEMORANDUM|ORDER)\s*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s{0,20}I\.\s+", re.MULTILINE),
    re.compile(r"^\s{0,20}(?:BACKGROUND|INTRODUCTION|FACTS)\s*$", re.MULTILINE | re.IGNORECASE),
]

_MAX_HEADER_CHARS = 5000  # header shouldn't exceed this


def _find_header_end(text: str) -> int:
    """Find where the court header/boilerplate ends and substance begins."""
    search_region = text[:_MAX_HEADER_CHARS]

    for pattern in _HEADER_END_PATTERNS:
        m = pattern.search(search_region)
        if m:
            return m.start()

    # Fallback: look for first paragraph break after 500+ chars
    m = re.search(r"\n\s*\n", text[500:2000])
    if m:
        return 500 + m.start()

    return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def split_sections(text: str) -> OpinionSections:
    """
    Split opinion text into coarse sections.

    Strategy:
        1. Detect header (court boilerplate) at the start
        2. Find section boundaries from heading patterns
        3. Classify each section as BACKGROUND, ANALYSIS, CONCLUSION, or OTHER
        4. If no boundaries found, return full text as single ANALYSIS section

    Returns OpinionSections with the list of classified sections and raw text.
    """
    if not text or not text.strip():
        return OpinionSections(
            sections=[Section("ANALYSIS", "", text or "", 0, len(text or ""))],
            raw_text=text or "",
        )

    # Step 1: Find header boundary
    header_end = _find_header_end(text)

    # Step 2: Find all heading positions after the header
    boundaries: list[tuple[int, str, SectionType]] = []

    for pattern_name, pattern in _HEADING_PATTERNS:
        for m in pattern.finditer(text, pos=header_end):
            heading_text = m.group(0).strip()
            pos = m.start()

            if pattern_name == "roman":
                # For Roman numeral, use the descriptive text after the numeral
                heading_text = m.group(2).strip() if m.lastindex and m.lastindex >= 2 else heading_text
            elif pattern_name == "letter":
                heading_text = m.group(2).strip() if m.lastindex and m.lastindex >= 2 else heading_text

            section_type = _classify_heading(heading_text)
            boundaries.append((pos, heading_text, section_type))

    # Deduplicate overlapping boundaries (keep earliest per position)
    boundaries.sort(key=lambda b: b[0])
    deduped: list[tuple[int, str, SectionType]] = []
    for b in boundaries:
        if not deduped or b[0] - deduped[-1][0] > 50:
            deduped.append(b)
    boundaries = deduped

    # Step 3: Build sections
    sections: list[Section] = []

    # Add header section if present
    if header_end > 100:
        sections.append(Section("HEADER", "", text[:header_end], 0, header_end))

    if not boundaries:
        # No clear section boundaries — return full body as ANALYSIS
        body_start = header_end if header_end > 100 else 0
        sections.append(Section("ANALYSIS", "", text[body_start:], body_start, len(text)))
        return OpinionSections(sections=sections, raw_text=text)

    # Add section before first boundary (if not already header)
    if boundaries[0][0] > header_end + 100:
        sections.append(Section(
            "OTHER", "",
            text[header_end:boundaries[0][0]],
            header_end, boundaries[0][0],
        ))

    # Add each section between boundaries
    for i, (pos, heading, stype) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
        section_text = text[pos:end]
        sections.append(Section(stype, heading, section_text, pos, end))

    return OpinionSections(sections=sections, raw_text=text)


def get_analysis_text(opinion_sections: OpinionSections) -> str:
    """Extract opinion text for LLM input — everything except the court header boilerplate."""
    parts: list[str] = [
        s.text for s in opinion_sections.sections if s.section_type != "HEADER"
    ]
    return "\n".join(parts)


def get_section_summary(opinion_sections: OpinionSections) -> dict[str, int]:
    """Return char counts per section type (for debugging)."""
    summary: dict[str, int] = {}
    for s in opinion_sections.sections:
        summary[s.section_type] = summary.get(s.section_type, 0) + len(s.text)
    return summary
