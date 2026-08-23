"""Pin-role normalizer — vendor pin names → a small set of canonical roles.

Decouples vendor naming (VDD / VCC / AVCC / DVDD …) from functional intent so a
heterogeneous IC fleet can be verified against unified rules.  Inspired by
PCBSchemaGen's ~36 pin-role types (arXiv:2602.00510).

A canonical role is a (domain, polarity) pair, e.g. ("power", "rail_3v3") or
("signal", "i2c").  We keep the set deliberately small and rule-based — no model
call — so it is deterministic and fast.
"""
from __future__ import annotations
import re

# Canonical role constants
POWER_GND   = "power.gnd"
POWER_RAIL  = "power.rail"        # generic supply input
POWER_OUT   = "power.out"         # regulator/converter output
POWER_REF   = "power.ref"         # reference / bias
SIG_I2C     = "signal.i2c"
SIG_SPI     = "signal.spi"
SIG_UART    = "signal.uart"
SIG_USB     = "signal.usb"
SIG_CLK     = "signal.clock"
SIG_RESET   = "signal.reset"
SIG_DDR     = "signal.ddr"
SIG_DIFF    = "signal.diff"
SIG_ANALOG  = "signal.analog"
SIG_GPIO    = "signal.gpio"
SIG_GEN     = "signal.generic"
NC          = "nc"

# (compiled regex on UPPERCASED pin name, role) — first match wins, order matters.
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(GND|VSS|VSSQ|AGND|PGND|DGND|GROUND)\b|^EP$|^PAD$"), POWER_GND),
    (re.compile(r"\b(VOUT|VO\d?|SW_OUT|OUT_V)\b"),                       POWER_OUT),
    (re.compile(r"\b(VREF|VBIAS|VCM|VTT|BIAS|REFGND)\b"),               POWER_REF),
    (re.compile(r"\b(VCC|VDD|VBUS|VIN|VBAT|VPP|VDDQ|VDDA|VDDIO|AVDD|DVDD|"
                r"3V3|5V0?|1V8|1V2|2V5|V1P8|V1P0|IN_\d+V|VM|VDRAIN|VS)\b"), POWER_RAIL),
    (re.compile(r"\b(SDA|SCL)\b"),                                       SIG_I2C),
    (re.compile(r"\b(MISO|MOSI|SCK|SCLK|SSEL|NSS|CS_?N?|SDO|SDI)\b"),    SIG_SPI),
    (re.compile(r"\b(TXD?|RXD?|UART|CTS|RTS)\b"),                        SIG_UART),
    (re.compile(r"\b(USB_?DP|USB_?DM|DP|DM|D\+|D-)\b"),                  SIG_USB),
    (re.compile(r"\b(XIN|XOUT|OSC|CLK\w*|\w*CLK|REFCLK|MCLK|BCLK)\b"),   SIG_CLK),
    (re.compile(r"\b(RESET|RST_?N?|NRST|POR|MR_?N?)\b"),                 SIG_RESET),
    (re.compile(r"\b(DQ\d*|DQS\w*|D[QM]\w*|BA\d|CK[ET]?|ODT|RAS|CAS|WE_?N?)\b"), SIG_DDR),
    (re.compile(r"\b(TD[_P]?[PM]|RD[_P]?[PM]|TX[PM]|RX[PM]|\w+_[PM]$|"
                r"\w+\+$|\w+-$|LVDS\w*)\b"),                             SIG_DIFF),
    (re.compile(r"\b(AIN\d*|AOUT|ADC\w*|DAC\w*|ANALOG|SENSE|ISEN\w*|VSEN\w*)\b"), SIG_ANALOG),
    (re.compile(r"\b(GP\w*\d|PA\d|PB\d|PC\d|PD\d|IO\d+|GPIO\w*)\b"),     SIG_GPIO),
    (re.compile(r"\b(NC|DNC|RSVD|RESERVED)\b"),                          NC),
]

# electrical_type from component/2 → coarse role hint when name rules miss.
_ETYPE_HINT = {
    "power_in":  POWER_RAIL,
    "power_out": POWER_OUT,
    "input":     SIG_GEN,
    "output":    SIG_GEN,
    "bidir":     SIG_GEN,
    "passive":   SIG_GEN,
    "nc":        NC,
}


def normalize(pin_name: str, electrical_type: str = "") -> str:
    """Return the canonical role for a pin given its name (+ optional etype)."""
    u = (pin_name or "").upper().strip()
    for rx, role in _RULES:
        if rx.search(u):
            # A power_in named e.g. "GND" must stay gnd; rules already ordered.
            return role
    # Name didn't match a rule — fall back to the datasheet's electrical_type.
    return _ETYPE_HINT.get((electrical_type or "").lower(), SIG_GEN)


def is_power(role: str) -> bool:
    return role.startswith("power.") and role != POWER_GND


def is_ground(role: str) -> bool:
    return role == POWER_GND


def is_signal(role: str) -> bool:
    return role.startswith("signal.")
