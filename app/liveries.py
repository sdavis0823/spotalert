"""Curated special-livery registry — airline retro / heritage / special paint.

A feed can't tell you a tail wears a special scheme; a maintained list can.
Entries are keyed by REGISTRATION. Applying tags them base_interest=1 so they
always push, regardless of rarity. Matched by registration in scheduled
arrivals and (via the aircraft DB) in live traffic.

This set is compiled from published enthusiast livery lists (airportspotting.com,
simpleflying.com) plus Phoenix/Southwest-relevant state & heritage jets — all
REAL registrations, no invented tails. Airline special liveries change over time
(repaints, retirements), so treat as a living list and grow it with
POST /api/aircraft/tag as you spot new ones.

Format: (registration, category, [tags])
"""
CURATED_LIVERIES = [
    # ---- North America ----
    ("N768AX", "special", ["retro", "airborne-express"]),        # ABX Air 767
    ("N421QX", "special", ["retro", "horizon"]),                 # Alaska/Horizon Q400
    # American Airlines heritage fleet
    ("N742PS", "special", ["heritage", "psa"]),
    ("N744P", "special", ["heritage", "piedmont"]),
    ("N745VJ", "special", ["heritage", "allegheny"]),
    ("N838AW", "special", ["heritage", "america-west"]),
    ("N837AW", "special", ["heritage", "america-west"]),
    ("N578UW", "special", ["heritage", "us-airways"]),
    ("N905NN", "special", ["heritage", "astrojet"]),
    ("N915NN", "special", ["heritage", "twa"]),
    ("N916NN", "special", ["heritage", "reno-air"]),
    ("N917NN", "special", ["heritage", "aircal"]),
    ("N760MQ", "special", ["heritage", "tricolor"]),
    # Southwest state & special liveries
    ("N955WN", "special", ["state-livery", "arizona-one"]),
    ("N487WN", "special", ["state-livery", "maryland-one"]),
    ("N8620H", "special", ["state-livery", "tennessee-one"]),
    ("N214WN", "special", ["state-livery", "colorado-one"]),
    ("N711HK", "special", ["retro", "spirit-of-kitty-hawk"]),
    # Alaska specials
    ("N559AS", "special", ["special-livery", "more-to-love"]),
    ("N570AS", "special", ["special-livery", "salmon-thirty-salmon"]),
    # Air Canada / United
    ("C-FZUH", "special", ["retro", "transcanada"]),
    ("N475UA", "special", ["retro", "united"]),
    ("N75435", "special", ["heritage", "continental"]),
    # ---- Europe ----
    ("EI-DVM", "special", ["retro", "aer-lingus"]),
    ("VP-BNT", "special", ["retro", "aeroflot"]),
    ("9H-AEI", "special", ["retro", "air-malta"]),
    ("OE-LBO", "special", ["retro", "austrian"]),
    ("G-EUPJ", "special", ["retro", "bea-red-square"]),
    ("G-BNLY", "special", ["retro", "landor"]),            # BA 747
    ("G-BYGC", "special", ["retro", "boac"]),              # BA 747
    ("G-CIVB", "special", ["retro", "negus"]),             # BA 747
    ("D-AICH", "special", ["retro", "condor"]),
    ("D-ABUM", "special", ["retro", "condor"]),
    ("OY-RUT", "special", ["retro", "danish-air-transport"]),
    ("D-AIDV", "special", ["retro", "lufthansa"]),
    ("D-ABYT", "special", ["retro", "lufthansa"]),
    ("SP-LIM", "special", ["retro", "lot"]),
    ("OY-KBO", "special", ["retro", "sas"]),
    ("CS-TJR", "special", ["retro", "tap"]),
    ("YR-BGG", "special", ["retro", "tarom"]),
    ("TC-JNC", "special", ["retro", "turkish"]),
    # ---- Rest of world ----
    ("LV-GOO", "special", ["retro", "aerolineas-argentinas"]),
    ("N284AV", "special", ["retro", "avianca"]),
    ("4X-EDF", "special", ["retro", "el-al"]),
    ("PK-GHD", "special", ["retro", "garuda"]),
    ("PK-GFM", "special", ["retro", "garuda-1950s"]),
    ("PK-GFN", "special", ["retro", "garuda"]),
    ("PK-GIK", "special", ["retro", "garuda"]),
    ("A9C-FG", "special", ["retro", "gulf-air"]),
    ("9M-MXA", "special", ["special-livery", "malaysia-40th"]),
    ("OD-MRT", "special", ["retro", "mea"]),
    ("AP-BLA", "special", ["retro", "pia"]),
    ("AP-BLT", "special", ["retro", "pia"]),
    ("AP-BMG", "special", ["retro", "pia"]),
    ("VH-VXQ", "special", ["retro", "retro-roo-ii"]),      # Qantas
    ("VH-XZP", "special", ["retro", "retro-roo"]),         # Qantas
    ("TS-IOP", "special", ["retro", "tunisair"]),
    ("VT-ATV", "special", ["retro", "vistara"]),
]


_BY_REG = {reg.upper(): (category, tags) for reg, category, tags in CURATED_LIVERIES}


def lookup(reg: str):
    """Return (category, tags) for a curated special-livery tail, else None.
    Works without the DB — used to flag special liveries in scheduled arrivals."""
    return _BY_REG.get((reg or "").upper())


def apply(conn) -> int:
    """Tag every curated livery whose registration exists in the aircraft table.
    Returns how many matched. Matches purely by registration."""
    n = 0
    for reg, category, tags in CURATED_LIVERIES:
        cur = conn.execute(
            """UPDATE aircraft SET category=?, interest_tags=?, base_interest=1
               WHERE UPPER(registration)=?""",
            (category, ",".join(tags), reg.upper()))
        n += cur.rowcount
    return n
