"""Curated knowledge base — the differentiator.

A feed gives you a hex, a tail, and a type code. Knowing that a given tail wears
a retro or commemorative livery, is a warbird, a fire tanker, an engine
testbed, or a notable government/military frame requires a *maintained* database.
This is the same curated layer that powers JetTip's "AvGeek AI"; ours is larger
and, crucially, includes the blocked military/gov/private frames JetTip drops.

Illustrative registrations/liveries — extend freely. Fields:
  icao24, registration, typecode, model, operator, category, tags, base_interest
categories: airliner|cargo|special|warbird|tanker|testbed|military|gov|private|ga
"""

AIRPORTS = [
    ("KPHX", "PHX", "Phoenix Sky Harbor Intl", "Phoenix", 33.4342, -112.0116),
    ("KSEA", "SEA", "Seattle-Tacoma Intl", "Seattle", 47.4490, -122.3093),
    ("KBFI", "BFI", "Boeing Field / King County Intl", "Seattle", 47.5300, -122.3020),
    ("KPAE", "PAE", "Paine Field (Everett)", "Everett", 47.9063, -122.2820),
    ("KLAX", "LAX", "Los Angeles Intl", "Los Angeles", 33.9416, -118.4085),
    ("KVNY", "VNY", "Van Nuys", "Los Angeles", 34.2098, -118.4899),
    ("KJFK", "JFK", "John F. Kennedy Intl", "New York", 40.6413, -73.7781),
    ("KBOS", "BOS", "Boston Logan Intl", "Boston", 42.3656, -71.0096),
    ("KIAD", "IAD", "Washington Dulles Intl", "Washington", 38.9531, -77.4565),
    ("KSFO", "SFO", "San Francisco Intl", "San Francisco", 37.6188, -122.3750),
    ("KMIA", "MIA", "Miami Intl", "Miami", 25.7959, -80.2870),
    ("CYYZ", "YYZ", "Toronto Pearson Intl", "Toronto", 43.6777, -79.6248),
    ("CYVR", "YVR", "Vancouver Intl", "Vancouver", 49.1967, -123.1815),
]

# ---- common airliners (frequent, should NOT alert) ----
COMMON = [
    ("a1b2c1", "N401AS", "B739", "Boeing 737-900ER", "Alaska Airlines", "airliner", "", 0),
    ("a1b2c2", "N402AS", "B739", "Boeing 737-900ER", "Alaska Airlines", "airliner", "", 0),
    ("a1b2c3", "N701AL", "A321", "Airbus A321neo", "American Airlines", "airliner", "", 0),
    ("a1b2c4", "N802DN", "A359", "Airbus A350-900", "Delta Air Lines", "airliner", "", 0),
    ("a1b2c5", "N38950", "B38M", "Boeing 737 MAX 8", "United Airlines", "airliner", "", 0),
    ("a1b2c6", "N960NN", "B738", "Boeing 737-800", "American Airlines", "airliner", "", 0),
    ("a1b2c7", "C-FGDX", "B77W", "Boeing 777-300ER", "Air Canada", "airliner", "", 0),
    ("a1b2c8", "N120SY", "E75L", "Embraer 175", "SkyWest / Alaska", "airliner", "", 0),
]

# ---- special / commemorative liveries (always notable) ----
SPECIAL_LIVERIES = [
    ("a2c001", "N559AS", "B739", "Boeing 737-900ER", "Alaska Airlines", "special", "special-livery,more-to-love", 1),
    ("a2c002", "N493WN", "B737", "Boeing 737-700", "Southwest Airlines", "special", "special-livery,shamu-one", 1),
    ("a2c003", "N624AG", "B77W", "Boeing 777-300ER", "Emirates", "special", "special-livery,expo", 1),
    ("a2c004", "JA602J", "B763", "Boeing 767-300ER", "ANA", "special", "special-livery,retro", 1),
    ("a2c005", "N171DZ", "B764", "Boeing 767-400ER", "Delta Air Lines", "special", "special-livery,ol-glory", 1),
    ("a2c006", "N842VA", "A320", "Airbus A320", "Alaska (Virgin America)", "special", "special-livery,heritage", 1),
    ("a2c007", "N537AS", "B739", "Boeing 737-900ER", "Alaska Airlines", "special", "special-livery,honoring-those-who-serve", 1),
    ("a2c008", "C-GKUG", "B77W", "Boeing 777-300ER", "Air Canada", "special", "special-livery,trans-canada-retro", 1),
    ("a2c009", "N8642E", "B738", "Boeing 737-800", "Southwest Airlines", "special", "special-livery,tennessee-one", 1),
    ("a2c010", "N516NK", "A320", "Airbus A320", "Spirit Airlines", "special", "special-livery,50th", 1),
    ("a2c011", "9V-SWH", "B77W", "Boeing 777-300ER", "Singapore Airlines", "special", "special-livery,retro", 1),
    ("a2c012", "N301DQ", "A339", "Airbus A330-900", "Delta Air Lines", "special", "special-livery,team-usa", 1),
]

# ---- rare wide-body / unusual scheduled visitors (rarity-driven) ----
RARE_VISITORS = [
    ("a3d001", "A6-EUA", "A388", "Airbus A380-800", "Emirates", "airliner", "widebody-rare", 0),
    ("a3d002", "B-2020", "B748", "Boeing 747-8F", "Air China Cargo", "cargo", "widebody-rare", 0),
    ("a3d003", "A6-BLK", "B788", "Boeing 787-8", "Etihad Airways", "airliner", "widebody-rare", 0),
    ("a3d004", "HL8361", "B77L", "Boeing 777F", "Korean Air Cargo", "cargo", "", 0),
    ("a3d005", "D-AIMA", "A388", "Airbus A380-800", "Lufthansa", "airliner", "widebody-rare", 0),
]

# ---- BLOCKED military / government (JetTip hides — we surface) ----
MILITARY_GOV = [
    ("ae1001", "01-0041", "C17", "Boeing C-17A Globemaster III", "US Air Force", "military", "heavy-lift", 1),
    ("ae1002", "165833", "C130", "Lockheed C-130T Hercules", "US Navy", "military", "", 1),
    ("adfe01", "92-9000", "VC25", "Boeing VC-25A (Air Force One)", "US Air Force", "gov", "vip,notable", 1),
    ("ae2001", "168980", "P8", "Boeing P-8A Poseidon", "US Navy", "military", "maritime-patrol", 1),
    ("ae2002", "62-3540", "KC135", "Boeing KC-135R Stratotanker", "US Air Force", "military", "tanker-mil", 1),
    ("ae2003", "16-46019", "KC46", "Boeing KC-46A Pegasus", "US Air Force", "military", "tanker-mil", 1),
    ("ae2004", "10-0213", "C40", "Boeing C-40C Clipper", "US Air Force", "gov", "vip", 1),
    ("ae2005", "165829", "E6", "Boeing E-6B Mercury", "US Navy", "military", "notable,doomsday", 1),
    ("ae2006", "79-0002", "E3", "Boeing E-3 Sentry (AWACS)", "US Air Force", "military", "notable,awacs", 1),
    ("ae2007", "163000", "C130", "Lockheed KC-130J Hercules", "US Marines", "military", "", 1),
    ("c0ffee", "16-6801", "CL60", "Bombardier E-11A (BACN)", "US Air Force", "military", "notable", 1),
]

# ---- warbirds / historic ----
WARBIRDS = [
    ("a4e001", "N251RJ", "P51", "North American P-51D Mustang", "Private", "warbird", "warbird", 1),
    ("a4e002", "N7227C", "DC3", "Douglas DC-3", "Historic Flight Foundation", "warbird", "warbird,classic", 1),
    ("a4e003", "N3193G", "B17", "Boeing B-17G Flying Fortress", "Collings Foundation", "warbird", "warbird,bomber", 1),
    ("a4e004", "N9323Z", "B25", "North American B-25 Mitchell", "Private", "warbird", "warbird,bomber", 1),
    ("a4e005", "NL2249", "P40", "Curtiss P-40 Warhawk", "Private", "warbird", "warbird", 1),
    ("a4e006", "N747DC", "SPIT", "Supermarine Spitfire", "Private", "warbird", "warbird,rare", 1),
]

# ---- aerial fire tankers ----
TANKERS = [
    ("a5f001", "N473NA", "B34", "BAe-146 Airtanker", "Neptune Aviation", "tanker", "firefighting", 1),
    ("a5f002", "N612AX", "DC10", "McDonnell Douglas DC-10 (Tanker 912)", "10 Tanker Air Carrier", "tanker", "firefighting,vlats", 1),
    ("a5f003", "N23TJ", "MD87", "MD-87 Airtanker", "Erickson Aero Tanker", "tanker", "firefighting", 1),
    ("a5f004", "N392AC", "C130", "Lockheed C-130 (MAFFS)", "Coulson Aviation", "tanker", "firefighting", 1),
]

# ---- engine / flight testbeds ----
TESTBEDS = [
    ("a7b001", "N747GE", "B744", "Boeing 747 GE Engine Testbed", "General Electric", "testbed", "engine-testbed", 1),
    ("a7b002", "N787BX", "B788", "Boeing 787 (test/ecoDemonstrator)", "Boeing", "testbed", "flight-test,eco", 1),
    ("a7b003", "N604RR", "B748", "Boeing 747 RR Engine Testbed", "Rolls-Royce", "testbed", "engine-testbed", 1),
    ("a7b004", "N281RH", "GLF2", "Honeywell Engine Testbed (Gulfstream)", "Honeywell", "testbed", "engine-testbed", 1),
]

# ---- notable / blocked large private jets ----
PRIVATE = [
    ("a6a001", "N1KE", "GLF6", "Gulfstream G650", "Blocked Owner (Nike)", "private", "large-private,blocked,celebrity", 1),
    ("a6a002", "N628TS", "GLF5", "Gulfstream G550", "Blocked Owner", "private", "large-private,blocked", 1),
    ("a6a003", "N887WM", "GLF6", "Gulfstream G650ER", "Blocked Owner", "private", "large-private,blocked", 1),
    ("a6a004", "N272BG", "GLEX", "Bombardier Global Express", "Blocked Owner", "private", "large-private,blocked", 1),
    ("a6a005", "M-YBBJ", "BBJ", "Boeing BBJ (737)", "Private", "private", "bizliner,large-private", 1),
    ("a6a006", "VP-BXX", "A319", "Airbus ACJ319", "Private", "private", "bizliner,large-private", 1),
]

ALL_AIRCRAFT = (COMMON + SPECIAL_LIVERIES + RARE_VISITORS + MILITARY_GOV
                + WARBIRDS + TANKERS + TESTBEDS + PRIVATE)
