"""Curated special-livery registry — the growable part of the moat.

A feed can't tell you a tail wears a special scheme; a maintained list can.
Entries are keyed by REGISTRATION (not hex) so they attach to the correct real
airframe after you load the OpenSky identity DB (which maps real hex -> reg).
Applying tags them base_interest=1 so they always push, regardless of rarity.

This is a STARTER set focused on frames that frequent KPHX (an American hub and
a Southwest city) plus a few famous ones. TREAT AS ILLUSTRATIVE — verify against
current fleets and expand from community special-livery trackers, or grow it
live via POST /api/aircraft/tag as you spot them.

Format: (registration, category, [tags])
"""
CURATED_LIVERIES = [
    # Phoenix-relevant heritage / state liveries
    ("N837AW", "special", ["heritage", "america-west", "arizona"]),   # American A319 America West
    ("N955WN", "special", ["state-livery", "arizona-one"]),           # Southwest 737-700 Arizona One
    ("N915NN", "special", ["heritage", "twa"]),                       # American 737-800 TWA
    ("N744P",  "special", ["heritage", "piedmont"]),                  # American Piedmont heritage
    ("N742PS", "special", ["heritage", "psa"]),                       # American PSA heritage
    # Famous liveries that visit major hubs
    ("N559AS", "special", ["special-livery", "more-to-love"]),        # Alaska
    ("N570AS", "special", ["special-livery", "salmon-thirty-salmon"]),# Alaska salmon jet
    ("N487WN", "special", ["state-livery", "maryland-one"]),          # Southwest
    ("N8620H", "special", ["state-livery", "tennessee-one"]),         # Southwest
    ("N214WN", "special", ["state-livery", "colorado-one"]),          # Southwest
]


_BY_REG = {reg.upper(): (category, tags) for reg, category, tags in CURATED_LIVERIES}


def lookup(reg: str):
    """Return (category, tags) for a curated special-livery tail, else None.
    Works without the DB — used to flag special liveries in scheduled arrivals."""
    return _BY_REG.get((reg or "").upper())


def apply(conn) -> int:
    """Tag every curated livery whose registration already exists in the
    aircraft table (i.e. after the identity DB is loaded). Returns how many
    matched. Never clobbers a hex; matches purely by registration."""
    n = 0
    for reg, category, tags in CURATED_LIVERIES:
        cur = conn.execute(
            """UPDATE aircraft SET category=?, interest_tags=?, base_interest=1
               WHERE UPPER(registration)=?""",
            (category, ",".join(tags), reg.upper()))
        n += cur.rowcount
    return n
