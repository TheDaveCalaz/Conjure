"""Repoint SQL generation for the guided workflow's Stage 4.

Conjure never talks to a database — it only ever produces text. These
functions build copy-paste-ready MySQL statements for AzerothCore's
acore_world schema, substituting in the real creature entries and the
real new DisplayID so nothing needs to be hand-derived.
"""

from .errors import ConjureError


def escape_sql_like(term: str) -> str:
    """Escape a string for safe use inside a single-quoted SQL LIKE pattern
    (handles names with an apostrophe, e.g. Vol'jin)."""
    return term.replace("\\", "\\\\").replace("'", "\\'")


def build_name_search_sql(search_term: str) -> str:
    if not search_term:
        raise ConjureError("Enter a creature name (or part of one) to search for.")
    escaped = escape_sql_like(search_term)
    return f"SELECT entry, name FROM creature_template WHERE name LIKE '%{escaped}%';"


def parse_entries(entries_str: str):
    if not entries_str or not entries_str.strip():
        raise ConjureError("Enter at least one creature entry (the numeric ID from creature_template).")
    parts = [p.strip() for p in entries_str.replace(";", ",").split(",") if p.strip()]
    entries = []
    for p in parts:
        if not p.isdigit():
            raise ConjureError(f"'{p}' is not a valid numeric creature entry.")
        entries.append(int(p))
    if not entries:
        raise ConjureError("Enter at least one creature entry (the numeric ID from creature_template).")
    return entries


def build_repoint_sql_block(search_term: str, entries_str: str, new_display_id: int) -> str:
    """Build the full 4-statement copy-paste SQL block for Stage 4.

    Statement 3 uses a subquery (rather than requiring the user to hand-copy
    the display IDs found by statement 2) so the whole block is generated
    from just the entries and the new DisplayID — no manual lookup step."""
    entries = parse_entries(entries_str)
    entries_sql = ", ".join(str(e) for e in entries)
    search_sql = build_name_search_sql(search_term) if search_term.strip() else (
        "-- (no name search term given — you already know the entry/entries)"
    )

    lines = [
        "-- 1) find the creature entry/entries:",
        search_sql,
        "",
        "-- 2) see which displays they use and whether shared:",
        "SELECT ct.entry, ctm.Idx, ctm.CreatureDisplayID FROM creature_template ct",
        "  JOIN creature_template_model ctm ON ctm.CreatureID = ct.entry",
        f"  WHERE ct.entry IN ({entries_sql}) ORDER BY ctm.CreatureDisplayID, ct.entry;",
        "",
        "-- 3) confirm none of those current displays are shared with creatures NOT in this list",
        "--    (never edit a display that's shared -- repoint by CreatureID instead if this returns rows):",
        "SELECT DISTINCT CreatureID FROM creature_template_model",
        f"  WHERE CreatureDisplayID IN (SELECT CreatureDisplayID FROM creature_template_model WHERE CreatureID IN ({entries_sql}))",
        f"  AND CreatureID NOT IN ({entries_sql});",
        "",
        "-- 4) repoint ONLY the target creature(s) to the new display (updates every Idx row for each):",
        f"UPDATE creature_template_model SET CreatureDisplayID = {new_display_id} WHERE CreatureID IN ({entries_sql});",
    ]
    return "\n".join(lines)
