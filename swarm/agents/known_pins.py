"""Fallback pin database for common ICs when datasheet extraction fails.

Used by the design loop to supplement component/2 JSONs that have empty pin lists.
Covers the most common synth/embedded components.
"""

# MPN prefix → pin list (name, number, type)
# type: power_in | power_out | input | output | io | nc
KNOWN_PINS: dict[str, list[dict]] = {

    # ── Display ──────────────────────────────────────────────────────────────
    "SSD1306": [
        {"name": "VDD",  "number": "1",  "type": "power_in"},
        {"name": "GND",  "number": "2",  "type": "power_in"},
        {"name": "SDA",  "number": "3",  "type": "io"},
        {"name": "SCL",  "number": "4",  "type": "input"},
        {"name": "RES",  "number": "5",  "type": "input"},
        {"name": "DC",   "number": "6",  "type": "input"},
        {"name": "CS",   "number": "7",  "type": "input"},
    ],
    "WEA012864": [
        {"name": "VCC",  "number": "1",  "type": "power_in"},
        {"name": "GND",  "number": "2",  "type": "power_in"},
        {"name": "SDA",  "number": "3",  "type": "io"},
        {"name": "SCL",  "number": "4",  "type": "input"},
        {"name": "RES",  "number": "5",  "type": "input"},
    ],

    # ── Audio DAC ─────────────────────────────────────────────────────────────
    "PCM5102A": [
        {"name": "VDD",   "number": "1",  "type": "power_in"},
        {"name": "GND",   "number": "2",  "type": "power_in"},
        {"name": "AGND",  "number": "3",  "type": "power_in"},
        {"name": "AVDD",  "number": "4",  "type": "power_in"},
        {"name": "DIN",   "number": "5",  "type": "input"},
        {"name": "BCK",   "number": "6",  "type": "input"},
        {"name": "LRCK",  "number": "7",  "type": "input"},
        {"name": "SCK",   "number": "8",  "type": "input"},
        {"name": "XSMT",  "number": "9",  "type": "input"},
        {"name": "FMT0",  "number": "10", "type": "input"},
        {"name": "OUTL",  "number": "11", "type": "output"},
        {"name": "OUTR",  "number": "12", "type": "output"},
    ],
    "PCM5102": [  # alias
        {"name": "VDD",  "number": "1",  "type": "power_in"},
        {"name": "GND",  "number": "2",  "type": "power_in"},
        {"name": "AGND", "number": "3",  "type": "power_in"},
        {"name": "AVDD", "number": "4",  "type": "power_in"},
        {"name": "DIN",  "number": "5",  "type": "input"},
        {"name": "BCK",  "number": "6",  "type": "input"},
        {"name": "LRCK", "number": "7",  "type": "input"},
        {"name": "OUTL", "number": "11", "type": "output"},
        {"name": "OUTR", "number": "12", "type": "output"},
    ],

    # ── LiPo Charger ──────────────────────────────────────────────────────────
    "MCP73831": [
        {"name": "VDD",   "number": "1",  "type": "power_in"},
        {"name": "GND",   "number": "2",  "type": "power_in"},
        {"name": "VBAT",  "number": "3",  "type": "power_out"},
        {"name": "PROG",  "number": "4",  "type": "input"},
        {"name": "STAT",  "number": "5",  "type": "output"},
    ],

    # ── LDO Regulators ───────────────────────────────────────────────────────
    "AP2112K": [
        {"name": "VIN",  "number": "1",  "type": "power_in"},
        {"name": "GND",  "number": "2",  "type": "power_in"},
        {"name": "EN",   "number": "3",  "type": "input"},
        {"name": "VOUT", "number": "4",  "type": "power_out"},
        {"name": "NC",   "number": "5",  "type": "nc"},
    ],
    "AP2112": [
        {"name": "VIN",  "number": "1",  "type": "power_in"},
        {"name": "GND",  "number": "2",  "type": "power_in"},
        {"name": "EN",   "number": "3",  "type": "input"},
        {"name": "VOUT", "number": "4",  "type": "power_out"},
    ],

    # ── USB ESD Protection ────────────────────────────────────────────────────
    "USBLC6-2SC6": [
        {"name": "IO1",  "number": "1",  "type": "io"},
        {"name": "GND",  "number": "2",  "type": "power_in"},
        {"name": "IO2",  "number": "3",  "type": "io"},
        {"name": "VBUS", "number": "4",  "type": "power_in"},
        {"name": "IO3",  "number": "5",  "type": "io"},
        {"name": "GND2", "number": "6",  "type": "power_in"},
    ],
    "USBLC6": [
        {"name": "DM_IN",  "number": "1",  "type": "io"},
        {"name": "GND",    "number": "2",  "type": "power_in"},
        {"name": "DP_IN",  "number": "3",  "type": "io"},
        {"name": "VBUS",   "number": "4",  "type": "power_in"},
        {"name": "DP_OUT", "number": "5",  "type": "io"},
        {"name": "DM_OUT", "number": "6",  "type": "io"},
    ],

    # ── SPI Flash ─────────────────────────────────────────────────────────────
    "W25Q128": [
        {"name": "CS",   "number": "1",  "type": "input"},
        {"name": "DO",   "number": "2",  "type": "output"},
        {"name": "WP",   "number": "3",  "type": "input"},
        {"name": "GND",  "number": "4",  "type": "power_in"},
        {"name": "DI",   "number": "5",  "type": "input"},
        {"name": "CLK",  "number": "6",  "type": "input"},
        {"name": "HOLD", "number": "7",  "type": "input"},
        {"name": "VCC",  "number": "8",  "type": "power_in"},
    ],
    "W25Q64": [
        {"name": "CS",  "number": "1",  "type": "input"},
        {"name": "DO",  "number": "2",  "type": "output"},
        {"name": "WP",  "number": "3",  "type": "input"},
        {"name": "GND", "number": "4",  "type": "power_in"},
        {"name": "DI",  "number": "5",  "type": "input"},
        {"name": "CLK", "number": "6",  "type": "input"},
        {"name": "VCC", "number": "8",  "type": "power_in"},
    ],

    # ── Rotary Encoder ────────────────────────────────────────────────────────
    "PEC11R": [
        {"name": "A",   "number": "1",  "type": "output"},
        {"name": "GND", "number": "2",  "type": "power_in"},
        {"name": "B",   "number": "3",  "type": "output"},
        {"name": "SW1", "number": "4",  "type": "output"},
        {"name": "SW2", "number": "5",  "type": "output"},
    ],

    # ── Crystal Oscillator ────────────────────────────────────────────────────
    "CX3225SB": [
        {"name": "IN",  "number": "1",  "type": "input"},
        {"name": "GND", "number": "2",  "type": "power_in"},
        {"name": "OUT", "number": "3",  "type": "output"},
        {"name": "VCC", "number": "4",  "type": "power_in"},
    ],

    # ── Capacitors (generic) ─────────────────────────────────────────────────
    "CL05B104": [
        {"name": "1", "number": "1", "type": "io"},
        {"name": "2", "number": "2", "type": "io"},
    ],
    "CL10A475": [
        {"name": "1", "number": "1", "type": "io"},
        {"name": "2", "number": "2", "type": "io"},
    ],

    # ── USB-C Connector ───────────────────────────────────────────────────────
    "217B-CA04": [
        {"name": "VBUS",  "number": "1",  "type": "power_in"},
        {"name": "GND",   "number": "2",  "type": "power_in"},
        {"name": "DP",    "number": "3",  "type": "io"},
        {"name": "DM",    "number": "4",  "type": "io"},
        {"name": "CC1",   "number": "5",  "type": "io"},
        {"name": "CC2",   "number": "6",  "type": "io"},
        {"name": "SHIELD","number": "7",  "type": "power_in"},
    ],

    # ── 3.5mm Audio Jack ─────────────────────────────────────────────────────
    "SJ-3524": [
        {"name": "TIP",   "number": "1",  "type": "io"},
        {"name": "RING",  "number": "2",  "type": "io"},
        {"name": "SLEEVE","number": "3",  "type": "power_in"},
        {"name": "SW",    "number": "4",  "type": "output"},
    ],
    "SJ-352": [
        {"name": "TIP",   "number": "1",  "type": "io"},
        {"name": "RING",  "number": "2",  "type": "io"},
        {"name": "SLEEVE","number": "3",  "type": "power_in"},
    ],
}


def lookup(mpn: str) -> list[dict] | None:
    """Return known pins for an MPN, matching by prefix. Returns None if unknown."""
    mpn_upper = mpn.upper().replace("-", "").replace("_", "").replace(" ", "")
    for key, pins in KNOWN_PINS.items():
        key_norm = key.upper().replace("-", "").replace("_", "")
        if mpn_upper.startswith(key_norm):
            return pins
    return None


def supplement(component: dict) -> dict:
    """Fill in missing pins from the known-pins database.

    Mutates and returns the component dict.
    """
    mpn = (component.get("component") or {}).get("mpn", "")
    if not mpn:
        return component

    existing_pins = (component.get("symbol") or {}).get("pins") or []
    if existing_pins:
        return component  # Already has pins, don't overwrite

    known = lookup(mpn)
    if known:
        component.setdefault("symbol", {})["pins"] = known
        # Also fill minimal electrical info if missing
        power_pins = [p["name"] for p in known if p["type"] == "power_in"]
        if power_pins and not (component.get("electrical") or {}).get("power_domains"):
            component.setdefault("electrical", {}).setdefault("power_domains", [])

    return component
