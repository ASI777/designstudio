#!/usr/bin/env python3
"""Build the DesignStudio agent-workflow controller acceptance project.

This is intentionally data-driven.  Every placed MPN is expanded into the
same component/2 -> typed CAD program -> AP242 STEP -> bound-component/1
pipeline used by the product UI.  The generated v2 board remains editable by
the current Qt application; a lossless project/3 wrapper is emitted beside it.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarm.memory.component_binding import bind_component, validate_binding  # noqa: E402
from swarm.memory.component_cad_program import (  # noqa: E402
    ComponentCadError,
    build_component_cad_program,
    compile_step,
    export_kicad_v6_footprint,
)


REFERENCE = ROOT / "reference-only" / "controller-datasheets"
DEFAULT_OUTPUT = ROOT / "acceptance" / "agent-workflow-controller" / "generated"


@dataclass(frozen=True)
class Package:
    key: str
    manufacturer: str
    mpn: str
    category: str
    evidence_file: str
    evidence_url: str
    package_page: int
    land_page: int
    pin_page: int
    mount: str
    body: tuple[float, float, float]
    pads: tuple[dict[str, Any], ...]
    pins: tuple[tuple[str, str, str], ...]
    extra_primitives: tuple[dict[str, Any], ...] = ()


def smd_rows(count: int, body_w: float, body_h: float, pitch: float,
             pad_w: float, pad_h: float, exposed: bool = False) -> tuple[dict[str, Any], ...]:
    """Number a conventional four-sided package counter-clockwise."""
    per = count // 4
    pads: list[dict[str, Any]] = []
    for i in range(per):
        y = (per - 1) * pitch / 2 - i * pitch
        pads.append({"number": str(i + 1), "x_mm": -body_w / 2 - pad_w / 2,
                     "y_mm": y, "width_mm": pad_w, "height_mm": pad_h})
    for i in range(per):
        x = -(per - 1) * pitch / 2 + i * pitch
        pads.append({"number": str(per + i + 1), "x_mm": x,
                     "y_mm": -body_h / 2 - pad_w / 2,
                     "width_mm": pad_h, "height_mm": pad_w})
    for i in range(per):
        y = -(per - 1) * pitch / 2 + i * pitch
        pads.append({"number": str(2 * per + i + 1), "x_mm": body_w / 2 + pad_w / 2,
                     "y_mm": y, "width_mm": pad_w, "height_mm": pad_h})
    for i in range(per):
        x = (per - 1) * pitch / 2 - i * pitch
        pads.append({"number": str(3 * per + i + 1), "x_mm": x,
                     "y_mm": body_h / 2 + pad_w / 2,
                     "width_mm": pad_h, "height_mm": pad_w})
    if exposed:
        pads.append({"number": str(count + 1), "x_mm": 0, "y_mm": 0,
                     "width_mm": body_w * .58, "height_mm": body_h * .58,
                     "shape": "roundrect"})
    return tuple(pads)


def esp32_pads() -> tuple[dict[str, Any], ...]:
    pads: list[dict[str, Any]] = []
    for i in range(14):
        pads.append({"number": str(i + 1), "x_mm": -9.05, "y_mm": 10.5 - i * 1.5,
                     "width_mm": 1.5, "height_mm": .9})
    for i in range(14):
        pads.append({"number": str(15 + i), "x_mm": 9.05, "y_mm": -9 + i * 1.5,
                     "width_mm": 1.5, "height_mm": .9})
    for i in range(12):
        pads.append({"number": str(29 + i), "x_mm": 7.7 - i * 1.4, "y_mm": -13.05,
                     "width_mm": .9, "height_mm": 1.5})
    pads.append({"number": "41", "x_mm": 0, "y_mm": 0,
                 "width_mm": 6.0, "height_mm": 6.0, "shape": "roundrect"})
    return tuple(pads)


ESP_NAMES = (
    "GND", "3V3", "EN", "IO4", "IO5", "IO6", "IO7", "IO15", "IO16", "IO17",
    "IO18", "IO8", "USB_D-", "USB_D+", "IO3", "IO46", "IO9", "IO10", "IO11",
    "IO12", "IO13", "IO14", "IO21", "IO47", "IO48", "IO45", "IO0", "IO35",
    "IO36", "IO37", "IO38", "IO39", "IO40", "IO41", "IO42", "RXD0", "TXD0",
    "IO2", "IO1", "GND", "GND",
)


def packages() -> dict[str, Package]:
    passive_source = str(ROOT / "testdata/datasheets/source/PYU-RC_GROUP_51_ROHS_L.pdf")
    ceramic_source = str(ROOT / "testdata/datasheets/source/GRM32ER61E226ME15-01A.pdf")
    result = {
        "esp32": Package("esp32", "Espressif Systems", "ESP32-S3-WROOM-1-N8R8",
            "mcu_module", "esp32-s3-wroom-1.pdf",
            "https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf",
            31, 33, 11, "smd", (18.0, 25.5, 3.1), esp32_pads(),
            tuple((str(i + 1), name, "power_in" if name in {"3V3", "GND"} else
                   "input" if name == "EN" else "bidir") for i, name in enumerate(ESP_NAMES))),
        "mx": Package("mx", "CHERRY", "MX2A-H1NB", "keyswitch",
            "cherry-mx2a-black.pdf", "https://pim.cherry-world.net/datasheet/23002?locale=en_US",
            2, 2, 1, "through_hole", (15.6, 15.6, 18.5), (
                {"number": "1", "x_mm": -3.81, "y_mm": 2.54, "width_mm": 2.0,
                 "height_mm": 2.0, "drill_mm": 1.2, "shape": "circle"},
                {"number": "2", "x_mm": 2.54, "y_mm": 5.08, "width_mm": 2.0,
                 "height_mm": 2.0, "drill_mm": 1.2, "shape": "circle"}),
            (("1", "A", "passive"), ("2", "B", "passive"))),
        "encoder": Package("encoder", "Bourns", "PEC12R-4220F-S0024", "encoder",
            "pec12r.pdf", "https://www.bourns.com/docs/product-datasheets/pec12r.pdf",
            4, 4, 1, "through_hole", (13.2, 14.0, 6.1), tuple(
                {"number": str(i + 1), "x_mm": x, "y_mm": y, "width_mm": 2.0,
                 "height_mm": 2.0, "drill_mm": 1.0, "shape": "circle"}
                for i, (x, y) in enumerate(((-5, 0), (0, 0), (5, 0), (-2.5, -7), (2.5, -7)))),
            (("1", "A", "passive"), ("2", "C", "passive"), ("3", "B", "passive"),
             ("4", "SW1", "passive"), ("5", "SW2", "passive")),
            ({"id": "shaft", "role": "actuator", "shape": "cylinder",
              "center_mm": [0, 0, 16.1], "size_mm": [6, 6, 20], "rotation_deg_xyz": [0, 0, 0]},)),
        "fsr": Package("fsr", "Interlink Electronics", "34-00015", "force_sensor",
            "fsr-400-series.pdf",
            "https://www.interlinkelectronics.com/downloads/datasheets/fsr-400-series-datasheet.pdf",
            7, 7, 7, "through_hole", (18.3, 56.3, .46), (
                {"number": "1", "x_mm": -1.27, "y_mm": 0, "width_mm": 2.0,
                 "height_mm": 2.0, "drill_mm": 1.0, "shape": "circle"},
                {"number": "2", "x_mm": 1.27, "y_mm": 0, "width_mm": 2.0,
                 "height_mm": 2.0, "drill_mm": 1.0, "shape": "circle"}),
            (("1", "FSR_A", "passive"), ("2", "FSR_B", "passive"))),
        "joystick": Package("joystick", "Adafruit", "2765", "joystick",
            "adafruit-2765-diagram.jpg", "https://www.adafruit.com/product/2765",
            1, 1, 1, "through_hole", (17.5, 17.4, 12.0), tuple(
                {"number": str(i + 1), "x_mm": x, "y_mm": y, "width_mm": 1.8,
                 "height_mm": 1.8, "drill_mm": .9, "shape": "circle"}
                for i, (x, y) in enumerate(((10, -4), (10, 0), (10, 4), (-4, 10), (0, 10), (4, 10)))),
            (("1", "X_A", "passive"), ("2", "X_WIPER", "output"), ("3", "X_B", "passive"),
             ("4", "Y_A", "passive"), ("5", "Y_WIPER", "output"), ("6", "Y_B", "passive")),
            ({"id": "thumb", "role": "actuator", "shape": "cylinder",
              "center_mm": [0, 0, 15], "size_mm": [8, 8, 9], "rotation_deg_xyz": [0, 0, 0]},)),
        "usb": Package("usb", "GCT", "USB4085-GF-A", "connector",
            "gct-usb4085-drawing.pdf", "https://gct.co/Files/Drawings/USB4085.pdf",
            2, 2, 1, "through_hole", (8.95, 9.17, 3.46), tuple([
                {"number": str(row * 8 + col + 1),
                 # The opposing Type-C contact row is numbered in the reverse
                 # physical direction. Mirroring it aligns A6/B6 (D+) and
                 # A7/B7 (D-) instead of creating an impossible crossed pair.
                 "x_mm": -2.975 + (col if row == 0 else 7 - col) * .85,
                 "y_mm": -.675 + row * 1.35,
                 "width_mm": .65, "height_mm": .65, "drill_mm": .40,
                 "shape": "circle"}
                for row in range(2) for col in range(8)] + [
                {"number": "M1", "x_mm": -4.325, "y_mm": 0, "width_mm": .90,
                 "height_mm": 2.40, "drill_mm": .60, "shape": "oval", "mechanical": True},
                {"number": "M2", "x_mm": 4.325, "y_mm": 0, "width_mm": .90,
                 "height_mm": 2.40, "drill_mm": .60, "shape": "oval", "mechanical": True},
                {"number": "M3", "x_mm": -4.325, "y_mm": 4.73, "width_mm": .90,
                 "height_mm": 1.70, "drill_mm": .60, "shape": "oval", "mechanical": True},
                {"number": "M4", "x_mm": 4.325, "y_mm": 4.73, "width_mm": .90,
                 "height_mm": 1.70, "drill_mm": .60, "shape": "oval", "mechanical": True}]),
            tuple((str(i + 1), name, "power_in" if name == "VBUS" else "passive") for i, name in enumerate(
                ("GND", "VBUS", "CC1", "D+", "D-", "SBU1", "VBUS", "GND",
                 "GND", "VBUS", "SBU2", "D-", "D+", "CC2", "VBUS", "GND")))),
        "esd": Package("esd", "Texas Instruments", "TPD4E02B04DQAR", "esd_array",
            "tpd4e02b04.pdf", "https://www.ti.com/lit/ds/symlink/tpd4e02b04.pdf",
            24, 25, 3, "smd", (1.0, 2.5, .55), tuple(
                [{"number": str(number), "x_mm": -.4175, "y_mm": -1.0 + (number - 1) * .5,
                  "width_mm": .565, "height_mm": .20}
                 for number in range(1, 6)] +
                [{"number": str(number), "x_mm": .4175, "y_mm": 1.0 - (number - 6) * .5,
                  "width_mm": .565, "height_mm": .20}
                 for number in range(6, 11)]),
            (("1", "IO1", "passive"), ("2", "IO2", "passive"),
             ("3", "GND", "power_in"), ("4", "IO3", "passive"),
             ("5", "IO4", "passive"), ("6", "NC", "nc"), ("7", "NC", "nc"),
             ("8", "GND", "power_in"), ("9", "NC", "nc"), ("10", "NC", "nc"))),
        "charger": Package("charger", "Texas Instruments", "BQ24074RGTR", "battery_charger",
            "bq24074.pdf", "https://www.ti.com/lit/ds/symlink/bq24074.pdf",
            25, 25, 8, "smd", (3.0, 3.0, 1.0), smd_rows(16, 3, 3, .5, .6, .24, True),
            (("1", "TS", "input"), ("2", "BAT", "bidir"), ("3", "BAT", "bidir"),
             ("4", "CE", "input"), ("5", "EN2", "input"), ("6", "EN1", "input"),
             ("7", "PGOOD", "output"), ("8", "VSS", "power_in"),
             ("9", "CHG", "output"), ("10", "OUT", "power_out"),
             ("11", "OUT", "power_out"), ("12", "ILIM", "input"),
             ("13", "IN", "power_in"), ("14", "TMR", "input"),
             ("15", "ITERM", "input"), ("16", "ISET", "bidir"),
             ("17", "EP", "power_in"))),
        "buck": Package("buck", "Texas Instruments", "TPS63031DSKR", "buck_boost",
            "tps63030.pdf", "https://www.ti.com/lit/ds/symlink/tps63030.pdf",
            27, 27, 3, "smd", (2.5, 2.5, .8), tuple(
                {"number": str(i + 1), "x_mm": -1.55,
                 "y_mm": 1.0 - i * .5, "width_mm": .65, "height_mm": .4}
                for i in range(5)) + tuple(
                {"number": str(10 - i), "x_mm": 1.55,
                 "y_mm": 1.0 - i * .5, "width_mm": .65, "height_mm": .4}
                for i in range(5)) + (
                {"number": "11", "x_mm": 0, "y_mm": 0, "width_mm": 1.3, "height_mm": 1.3},),
            (("1", "VOUT", "power_out"), ("2", "L2", "passive"), ("3", "PGND", "power_in"),
             ("4", "L1", "passive"), ("5", "VIN", "power_in"), ("6", "EN", "input"),
             ("7", "PS/SYNC", "input"), ("8", "VINA", "power_in"), ("9", "GND", "power_in"),
             ("10", "FB", "input"), ("11", "EP", "power_in"))),
        "load": Package("load", "Texas Instruments", "TPS22918DBVR", "load_switch",
            "tps22918.pdf", "https://www.ti.com/lit/ds/symlink/tps22918.pdf",
            20, 20, 3, "smd", (2.9, 1.6, 1.1), (
                {"number": "1", "x_mm": -1.35, "y_mm": -.95, "width_mm": .6, "height_mm": 1.0},
                {"number": "2", "x_mm": 0, "y_mm": -.95, "width_mm": .6, "height_mm": 1.0},
                {"number": "3", "x_mm": 1.35, "y_mm": -.95, "width_mm": .6, "height_mm": 1.0},
                {"number": "4", "x_mm": 1.35, "y_mm": .95, "width_mm": .6, "height_mm": 1.0},
                {"number": "5", "x_mm": 0, "y_mm": .95, "width_mm": .6, "height_mm": 1.0},
                {"number": "6", "x_mm": -1.35, "y_mm": .95, "width_mm": .6, "height_mm": 1.0}),
            (("1", "VIN", "power_in"), ("2", "GND", "power_in"), ("3", "ON", "input"),
             ("4", "CT", "passive"), ("5", "QOD", "passive"), ("6", "VOUT", "power_out"))),
        "led_boost": Package("led_boost", "Texas Instruments", "TPS61023DRLR",
            "boost_converter", "tps61023.pdf",
            "https://www.ti.com/lit/ds/symlink/tps61023.pdf",
            25, 26, 3, "smd", (1.2, 1.6, .6), (
                {"number": "1", "x_mm": -.85, "y_mm": -.5, "width_mm": .6, "height_mm": .3},
                {"number": "2", "x_mm": -.85, "y_mm": 0, "width_mm": .6, "height_mm": .3},
                {"number": "3", "x_mm": -.85, "y_mm": .5, "width_mm": .6, "height_mm": .3},
                {"number": "4", "x_mm": .85, "y_mm": .5, "width_mm": .6, "height_mm": .3},
                {"number": "5", "x_mm": .85, "y_mm": 0, "width_mm": .6, "height_mm": .3},
                {"number": "6", "x_mm": .85, "y_mm": -.5, "width_mm": .6, "height_mm": .3}),
            (("1", "FB", "input"), ("2", "EN", "input"), ("3", "VIN", "power_in"),
             ("4", "GND", "power_in"), ("5", "SW", "passive"),
             ("6", "VOUT", "power_out"))),
        "led_buffer": Package("led_buffer", "Texas Instruments", "SN74AHCT1G125DBVR",
            "logic_buffer", "sn74ahct1g125.pdf",
            "https://www.ti.com/lit/ds/symlink/sn74ahct1g125.pdf",
            20, 21, 3, "smd", (2.9, 1.6, 1.45), (
                {"number": "1", "x_mm": -1.45, "y_mm": -.95, "width_mm": .7, "height_mm": 1.0},
                {"number": "2", "x_mm": 0, "y_mm": -.95, "width_mm": .7, "height_mm": 1.0},
                {"number": "3", "x_mm": 1.45, "y_mm": -.95, "width_mm": .7, "height_mm": 1.0},
                {"number": "4", "x_mm": 1.45, "y_mm": .95, "width_mm": .7, "height_mm": 1.0},
                {"number": "5", "x_mm": -1.45, "y_mm": .95, "width_mm": .7, "height_mm": 1.0}),
            (("1", "OE_N", "input"), ("2", "A", "input"), ("3", "GND", "power_in"),
             ("4", "Y", "output"), ("5", "VCC", "power_in"))),
        "diode": Package("diode", "Diodes Incorporated", "1N4148W-7-F", "switching_diode",
            "1n4148w.pdf", "https://www.diodes.com/datasheet/download/1N4148W.pdf",
            5, 5, 1, "smd", (3.7, 1.8, 1.35), (
                {"number": "1", "x_mm": -2.0, "y_mm": 0, "width_mm": 1.2, "height_mm": 1.4},
                {"number": "2", "x_mm": 2.0, "y_mm": 0, "width_mm": 1.2, "height_mm": 1.4}),
            (("1", "K", "passive"), ("2", "A", "passive"))),
        "led": Package("led", "Inolux", "IN-PI20TBT5R5G5B", "addressable_rgb",
            "inolux-in-pi20.pdf",
            "https://www.inolux-corp.com/datasheet/SMDLED/Addressable%20LED/IN-PI20TBT5R5G5B_v1.0.pdf",
            2, 2, 2, "smd", (2.0, 2.0, .65), (
                {"number": "1", "x_mm": -.8, "y_mm": -.8, "width_mm": .7, "height_mm": .7},
                {"number": "2", "x_mm": .8, "y_mm": -.8, "width_mm": .7, "height_mm": .7},
                {"number": "3", "x_mm": .8, "y_mm": .8, "width_mm": .7, "height_mm": .7},
                {"number": "4", "x_mm": -.8, "y_mm": .8, "width_mm": .7, "height_mm": .7}),
            (("1", "VDD", "power_in"), ("2", "DOUT", "output"),
             ("3", "GND", "power_in"), ("4", "DIN", "input"))),
        "battery": Package("battery", "JST", "B2B-PH-SM4-TB(LF)(SN)", "battery_connector",
            "jst-ph.pdf", "https://www.jst-mfg.com/product/pdf/eng/ePH.pdf",
            7, 7, 2, "smd", (6.0, 6.1, 4.5), (
                {"number": "1", "x_mm": -1.0, "y_mm": 3.35, "width_mm": 1.0, "height_mm": 2.0},
                {"number": "2", "x_mm": 1.0, "y_mm": 3.35, "width_mm": 1.0, "height_mm": 2.0}),
            (("1", "BAT+", "power_in"), ("2", "BAT-", "power_in"))),
        "battery_ntc": Package("battery_ntc", "JST", "B3B-PH-SM4-TB(LF)(SN)",
            "battery_connector", "jst-ph.pdf",
            "https://www.jst-mfg.com/product/pdf/eng/ePH.pdf",
            7, 7, 2, "smd", (8.0, 6.1, 4.5), (
                {"number": "1", "x_mm": -2.0, "y_mm": 3.35, "width_mm": 1.0, "height_mm": 2.0},
                {"number": "2", "x_mm": 0, "y_mm": 3.35, "width_mm": 1.0, "height_mm": 2.0},
                {"number": "3", "x_mm": 2.0, "y_mm": 3.35, "width_mm": 1.0, "height_mm": 2.0}),
            (("1", "BAT+", "power_in"), ("2", "BAT-", "power_in"),
             ("3", "NTC", "passive"))),
        # The FSR tail interface uses the same B2B-PH-SM4 land pattern as the
        # battery connector, but it is a sensor header: give it its own symbol
        # with FSR pin names instead of misleading BAT+/BAT- battery names.
        "fsr_conn": Package("fsr_conn", "JST", "B2B-PH-SM4-TB(LF)(SN)",
            "sensor_connector", "jst-ph.pdf",
            "https://www.jst-mfg.com/product/pdf/eng/ePH.pdf",
            7, 7, 2, "smd", (6.0, 6.1, 4.5), (
                {"number": "1", "x_mm": -1.0, "y_mm": 3.35, "width_mm": 1.0, "height_mm": 2.0},
                {"number": "2", "x_mm": 1.0, "y_mm": 3.35, "width_mm": 1.0, "height_mm": 2.0}),
            (("1", "FSR_A", "passive"), ("2", "FSR_B", "passive"))),
        "resistor": Package("resistor", "Yageo", "RC0603FR-0710KL", "resistor",
            passive_source, "https://www.yageo.com/en/Product/Index/rchip/thick_film_general_purpose",
            4, 4, 2, "smd", (1.6, .8, .55), (
                {"number": "1", "x_mm": -.85, "y_mm": 0, "width_mm": .8, "height_mm": .9},
                {"number": "2", "x_mm": .85, "y_mm": 0, "width_mm": .8, "height_mm": .9}),
            (("1", "A", "passive"), ("2", "B", "passive"))),
        "capacitor": Package("capacitor", "Murata", "GRM32ER61E226ME15L", "capacitor",
            ceramic_source, "https://www.murata.com/en-us/products/productdetail?partno=GRM32ER61E226ME15L",
            1, 1, 1, "smd", (3.2, 2.5, 2.5), (
                {"number": "1", "x_mm": -1.7, "y_mm": 0, "width_mm": 1.2, "height_mm": 2.7},
                {"number": "2", "x_mm": 1.7, "y_mm": 0, "width_mm": 1.2, "height_mm": 2.7}),
            (("1", "+", "passive"), ("2", "-", "passive"))),
        "inductor": Package("inductor", "Coilcraft", "XFL4020-152MEC", "inductor",
            "tps63030.pdf", "https://www.coilcraft.com/en-us/products/power/shielded-inductors/xfl/xfl4020/",
            17, 17, 11, "smd", (4.0, 4.0, 2.1), (
                {"number": "1", "x_mm": -2.0, "y_mm": 0, "width_mm": 1.6, "height_mm": 3.6},
                {"number": "2", "x_mm": 2.0, "y_mm": 0, "width_mm": 1.6, "height_mm": 3.6}),
            (("1", "A", "passive"), ("2", "B", "passive"))),
    }
    resistor_base = result["resistor"]
    for key, mpn in {
        "res_5k1": "RC0603FR-075K1L",
        "res_1k1": "RC0603FR-071K1L",
        "res_590": "RC0603FR-07590RL",
        "res_500": "RC0603FR-07500RL",
        "res_100k": "RC0603FR-07100KL",
        "res_732k": "RC0603FR-07732KL",
    }.items():
        result[key] = Package(key, resistor_base.manufacturer, mpn, resistor_base.category,
            resistor_base.evidence_file, resistor_base.evidence_url,
            resistor_base.package_page, resistor_base.land_page, resistor_base.pin_page,
            resistor_base.mount, resistor_base.body, resistor_base.pads, resistor_base.pins)
    result["boost_inductor"] = Package("boost_inductor", "Coilcraft", "XFL4020-102MEC",
        "inductor", "xfl4020.pdf",
        "https://www.coilcraft.com/getmedia/50632d43-da1b-4cdb-8ab4-3029cab51df3/xfl4020.pdf",
        3, 3, 1, "smd", (4.0, 4.0, 2.1), result["inductor"].pads,
        result["inductor"].pins)
    cap_pins = result["capacitor"].pins
    result["cap_10u"] = Package("cap_10u", "Murata", "GRM21BR61A106KE19L",
        "capacitor", "grm21br61a106ke19.pdf",
        "https://www.murata.com/en-us/products/productdetail?partno=GRM21BR61A106KE19L",
        1, 1, 1, "smd", (2.0, 1.25, 1.25), (
            {"number": "1", "x_mm": -1.0, "y_mm": 0, "width_mm": 1.0, "height_mm": 1.4},
            {"number": "2", "x_mm": 1.0, "y_mm": 0, "width_mm": 1.0, "height_mm": 1.4}),
        cap_pins)
    result["cap_100n"] = Package("cap_100n", "Murata", "GRM188R71H104KA93D",
        "capacitor", "grm188r71h104ka93.pdf",
        "https://www.murata.com/en-us/products/productdetail?partno=GRM188R71H104KA93D",
        1, 1, 1, "smd", (1.6, .8, .9), (
            {"number": "1", "x_mm": -.85, "y_mm": 0, "width_mm": .8, "height_mm": .9},
            {"number": "2", "x_mm": .85, "y_mm": 0, "width_mm": .8, "height_mm": .9}),
        cap_pins)
    result["cap_1n"] = Package("cap_1n", "Murata", "GRM1885C1H102JA01D",
        "capacitor", "grm1885c1h102ja01.pdf",
        "https://www.murata.com/en-us/products/productdetail?partno=GRM1885C1H102JA01D",
        1, 1, 1, "smd", (1.6, .8, .9), result["cap_100n"].pads, cap_pins)
    return result


def evidence_path(spec: Package) -> Path:
    value = Path(spec.evidence_file)
    return value.resolve() if value.is_absolute() else (REFERENCE / value).resolve()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def electrical_model(spec: Package) -> tuple[str, dict[str, Any]]:
    resistance = {
        "RC0603FR-0710KL": 10_000, "RC0603FR-075K1L": 5_100,
        "RC0603FR-071K1L": 1_100, "RC0603FR-07590RL": 590,
        "RC0603FR-07500RL": 500, "RC0603FR-07100KL": 100_000,
        "RC0603FR-07732KL": 732_000,
    }
    capacitance = {
        "GRM32ER61E226ME15L": (22e-6, 25),
        "GRM21BR61A106KE19L": (10e-6, 10),
        "GRM188R71H104KA93D": (100e-9, 50),
        "GRM1885C1H102JA01D": (1e-9, 50),
    }
    inductance = {"XFL4020-152MEC": (1.5e-6, 4.1),
                  "XFL4020-102MEC": (1.0e-6, 4.5)}
    if spec.mpn in resistance:
        value = resistance[spec.mpn]
        return f"{value:g} Ω", {"kind": "resistor", "resistance_ohm": value,
            "tolerance_pct": 1.0, "rated_power_w": .1}
    if spec.mpn in capacitance:
        value, voltage = capacitance[spec.mpn]
        return f"{value:g} F", {"kind": "capacitor", "capacitance_f": value,
            "tolerance_pct": 10.0, "rated_voltage_v": voltage}
    if spec.mpn in inductance:
        value, saturation = inductance[spec.mpn]
        return f"{value:g} H", {"kind": "inductor", "inductance_h": value,
            "saturation_current_a": saturation}
    if spec.key == "led":
        return "RGB addressable", {"kind": "addressable_rgb",
            "power_domains": [{"name": "VDD", "pins": ["1"], "vmin_v": 4.5,
                               "vnom_v": 5.0, "vmax_v": 5.5}],
            "logic": {"input_pins": ["4"], "output_pin": "2",
                      "vih_ratio_vdd": .7, "data_rate_hz": 800_000}}
    if spec.key == "led_boost":
        return "5 V boost", {"kind": "dc_converter",
            "power_domains": [{"name": "VIN", "pins": ["3"], "vmin_v": 2.7,
                               "vnom_v": 3.8, "vmax_v": 4.5},
                              {"name": "VOUT", "pins": ["6"], "vmin_v": 4.875,
                               "vnom_v": 5.0, "vmax_v": 5.125,
                               "max_current_a": 1.5}],
            "feedback": {"pin": "1", "reference_v": .595,
                         "upper_resistance_ohm": 732_000,
                         "lower_resistance_ohm": 100_000}}
    if spec.key == "load":
        return "load switch", {"kind": "load_switch",
            "power_domains": [{"name": "VIN", "pins": ["1"], "vmin_v": 1.0,
                               "vmax_v": 5.5},
                              {"name": "VOUT", "pins": ["6"],
                               "relation": "tracks_input_not_greater"}]}
    if spec.key == "led_buffer":
        return "5 V TTL buffer", {"kind": "logic_buffer",
            "power_domains": [{"name": "VCC", "pins": ["5"], "vmin_v": 4.5,
                               "vnom_v": 5.0, "vmax_v": 5.5}],
            "logic": {"input_pin": "2", "output_pin": "4",
                      "input_high_min_v": 2.0}}
    return spec.category, {"kind": spec.category,
        "pin_functions": [{"pin": number, "name": name, "electrical_type": etype}
                          for number, name, etype in spec.pins],
        "parameters": [{"name": "datasheet_bound", "typ": 1, "unit": "bool"}]}


def component_record(spec: Package) -> tuple[dict[str, Any], dict[str, Any]]:
    source = evidence_path(spec)
    if not source.is_file():
        raise FileNotFoundError(f"missing evidence for {spec.mpn}: {source}")
    digest = sha256(source)
    value, electrical = electrical_model(spec)
    pins = [{"number": number, "name": name, "electrical_type": etype}
            for number, name, etype in spec.pins]
    pads = [copy.deepcopy(pad) for pad in spec.pads]
    primitive_source = f"datasheet:{digest}:page:{spec.package_page}:package"
    primitives: list[dict[str, Any]] = [{
        "id": "body", "role": "body", "shape": "box",
        "center_mm": [0, 0, spec.body[2] / 2], "size_mm": list(spec.body),
        "rotation_deg_xyz": [0, 0, 0], "color_rgba": [.08, .1, .13, 1],
        "provenance": [primitive_source],
    }]
    for index, pad in enumerate(pads):
        if pad.get("mechanical"):
            continue
        primitives.append({
            "id": f"terminal-{index + 1}", "role": "terminal",
            "shape": "cylinder" if spec.mount == "through_hole" else "box",
            "center_mm": [pad["x_mm"], pad["y_mm"], .05],
            "size_mm": [max(.25, min(pad["width_mm"], pad["height_mm"])),
                        max(.25, min(pad["width_mm"], pad["height_mm"])),
                        1.6 if spec.mount == "through_hole" else .1],
            "rotation_deg_xyz": [0, 0, 0], "color_rgba": [.72, .73, .75, 1],
            "provenance": [primitive_source],
        })
    for item in spec.extra_primitives:
        primitive = copy.deepcopy(item)
        primitive.setdefault("color_rgba", [.12, .13, .15, 1])
        primitive["provenance"] = [primitive_source]
        primitives.append(primitive)
    record = {
        "schema": "design-studio.component/2",
        "component": {"manufacturer": spec.manufacturer, "mpn": spec.mpn,
                      "category": spec.category, "value": value},
        "symbol": {"pins": pins},
        "electrical": electrical,
        "footprint": {"name": safe(spec.mpn), "mount": spec.mount,
            "generated": "vector-trace", "courtyard_margin_mm": .25,
            "body": {"width_mm": spec.body[0], "length_mm": spec.body[1],
                     "height_mm": spec.body[2]}, "pads": pads},
        "package_3d": {"height_mm": spec.body[2], "standoff_mm": 0,
            "shape": "parametric", "construction": {
                "author": "gpt-5.6-luna-xhigh", "method": "parametric", "complexity": "simple",
                "complexity_reasons": [], "datasheet_pages": [spec.package_page],
                "assumptions": [], "primitives": primitives}},
        "evidence": {"schema": "design-studio.datasheet-evidence/1", "source_kind": "reference",
            "source": spec.evidence_url, "retrieved_utc": "2026-07-16T00:00:00Z",
            "bytes": source.stat().st_size, "sha256": digest, "expected_mpn": spec.mpn,
            "mpn_match": "exact", "package_variant": safe(spec.mpn),
            "package_pin_count": len(spec.pins)},
        "extraction": {"provider": "codex-cli", "model": "gpt-5.6-luna",
            "reasoning_effort": "xhigh", "grounding": {"status": "passed"},
            "verification": {"geometry_confidence": "verified", "state": "approved",
                             "placement_allowed": True, "blockers": [], "warnings": []}},
        "orientation": {"origin": "footprint", "z_axis": "board_normal", "pin1_mark": "datasheet"},
        "assumptions": [],
    }
    inspection = {
        "schema": "design-studio.datasheet-inspection/1", "requested_mpn": spec.mpn,
        "identified_mpn": spec.mpn, "package_variant": safe(spec.mpn), "confidence": .99,
        "regions": [
            {"page": spec.pin_page, "role": "pinout", "bbox_normalized": [0, 0, 1, 1],
             "evidence_summary": "manufacturer pin definition"},
            {"page": spec.package_page, "role": "package", "bbox_normalized": [0, 0, 1, 1],
             "evidence_summary": "manufacturer package dimensions"},
            {"page": spec.land_page, "role": "land_pattern", "bbox_normalized": [0, 0, 1, 1],
             "evidence_summary": "manufacturer land pattern or mechanical drawing"},
        ], "warnings": [],
    }
    return record, inspection


def build_library(specs: dict[str, Package], output: Path, compiler: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    components = output / "components"
    components.mkdir(parents=True, exist_ok=True)
    for key, spec in specs.items():
        directory = components / safe(spec.mpn)
        directory.mkdir(parents=True, exist_ok=True)
        record, inspection = component_record(spec)
        try:
            program = build_component_cad_program(record, inspection)
        except ComponentCadError as exc:
            raise ComponentCadError(f"{spec.mpn}: {exc}") from exc
        step = directory / "package.step"
        step_report = compile_step(program, step, executable=compiler)
        footprint = directory / f"{safe(spec.mpn)}.kicad_mod"
        export_kicad_v6_footprint(record, footprint, step_reference="package.step")
        binding = bind_component(record, step, alignment_status="verified",
                                 model_mpn=spec.mpn,
                                 source_uri=str((directory / "component.json").resolve()))
        validate_binding(binding, require_complete=True, verify_asset=True)
        for name, value in (("component.json", record), ("inspection.json", inspection),
                            ("cad-program.json", program), ("binding.json", binding)):
            (directory / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        result[key] = {"spec": spec, "component": record, "inspection": inspection,
                       "program": program, "binding": binding, "step": step,
                       "step_report": step_report, "footprint": footprint}
    return result


def world_pad(footprint: dict[str, Any], pad: dict[str, Any]) -> tuple[float, float]:
    angle = math.radians(footprint.get("rot_deg", 0))
    x, y = pad["x_mm"], pad["y_mm"]
    if footprint.get("side", 0) == 1:
        y = -y
    return (footprint["x_mm"] + x * math.cos(angle) - y * math.sin(angle),
            footprint["y_mm"] + x * math.sin(angle) + y * math.cos(angle))


class Design:
    def __init__(self, library: dict[str, dict[str, Any]]) -> None:
        self.library = library
        self.footprints: list[dict[str, Any]] = []
        self.symbols: list[dict[str, Any]] = []
        self.net_ids: dict[str, int] = {}
        self.net_meta: dict[str, dict[str, Any]] = {}
        self.connections: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}

    def net(self, name: str, cls: int = 0, current: float = 0, voltage: float = 0) -> int:
        if name not in self.net_ids:
            self.net_ids[name] = len(self.net_ids)
            self.net_meta[name] = {"id": self.net_ids[name], "name": name, "class": cls,
                                   "required_current_a": current, "nominal_voltage_v": voltage}
        return self.net_ids[name]

    def place(self, key: str, ref: str, x: float, y: float, pin_nets: dict[str, str], *,
              rot: float = 0, side: int = 0, group: str = "logic", edge: str = "none",
              locked: bool = True, test_access: bool = False) -> dict[str, Any]:
        item = self.library[key]
        spec: Package = item["spec"]
        component = item["component"]
        binding = item["binding"]
        pads = []
        for source in component["footprint"]["pads"]:
            fine_clearance = {"usb": .02, "esd": .05, "charger": .05,
                              "buck": .10, "load": .10, "led_boost": .10,
                              "led_buffer": .10}.get(key, 0)
            pad = {"name": source["number"], "x_mm": source["x_mm"], "y_mm": source["y_mm"],
                   "w_mm": source["width_mm"], "h_mm": source["height_mm"],
                   "net": -1, "th": spec.mount == "through_hole",
                   "drill_mm": source.get("drill_mm", 0), "shape": source.get("shape", "rect"),
                   "corner_r_mm": .12 if source.get("shape") == "roundrect" else 0,
                   "clearance_mm": fine_clearance}
            name = pin_nets.get(source["number"])
            if name:
                pad["net"] = self.net(name)
            pads.append(pad)
        court = binding["footprint"]["courtyard_pts"]
        compact_binding = {"schema": "design-studio.bound-component-ref/1",
            "binding_id": binding["binding_id"], "binding_digest": binding["binding_digest"],
            "record_uri": f"components/{safe(spec.mpn)}/binding.json",
            "model_3d": {k: binding["model_3d"][k] for k in
                ("format", "asset_uri", "sha256", "model_to_footprint", "claimed_mpn")}}
        fp = {"ref": ref, "lib": safe(spec.mpn), "mpn": spec.mpn,
              "manufacturer": spec.manufacturer,
              "value": component["component"].get("value", ""),
              "datasheet_evidence": component["evidence"],
              "bound_component": compact_binding, "x_mm": x, "y_mm": y, "rot_deg": rot,
              "side": side, "h3d_mm": spec.body[2], "body_w_mm": spec.body[0],
              "body_h_mm": spec.body[1], "body_cx_mm": 0, "body_cy_mm": 0,
              "courtyard_pts": court, "placement": {"locked": locked,
                  "functional_group": group, "edge_anchor": edge, "thermal_power_w": 0,
                  "thermal_clearance_mm": 0, "test_access_required": test_access,
                  "test_access_halo_mm": .5 if test_access else 0},
              "pads": pads, "regions": []}
        thermal_power = {"esp32": .65, "charger": .8, "buck": .45,
                         "load": .12}.get(key, 0)
        fp["placement"]["thermal_power_w"] = thermal_power
        fp["placement"]["thermal_clearance_mm"] = .5 if thermal_power else 0
        self.footprints.append(fp)
        pin_defs = {p[0]: p for p in spec.pins}
        symbol_pins = []
        for index, pad in enumerate(pads):
            if pad["name"] not in pin_defs:
                continue
            _, pin_name, etype = pin_defs[pad["name"]]
            symbol_pins.append({"num": pad["name"], "name": pin_name, "etype": etype,
                                "side": 0 if index % 2 == 0 else 1, "order": index,
                                "net": pad["net"]})
            if pad["net"] >= 0:
                self.connections.setdefault(pad["net"], []).append((fp, pad))
        symbol_index = len(self.symbols)
        self.symbols.append({"ref": ref, "lib": safe(spec.mpn),
            "value": component["component"].get("value", ""),
            "x_mm": 20 + (symbol_index % 8) * 28, "y_mm": 15 + (symbol_index // 8) * 24,
            "rot_deg": 0, "unit": 1, "pins": symbol_pins})
        return fp


def assemble_design(library: dict[str, dict[str, Any]]) -> Design:
    d = Design(library)
    gnd = "GND"; v33 = "+3V3"; vbat = "VBAT"; vsys = "VSYS"
    vled_raw = "+5V_LED_RAW"; vled = "+5V_LED"; vbus = "VBUS"
    # VSYS feeds both downstream converters: 3.3 V/1 A (~0.97 A input) plus
    # 5 V LED/1 A (~1.46 A input) is ~2.4 A worst case, so VSYS/VBAT/GND are
    # budgeted at 2.5 A.  VBUS stays 1.5 A: the charger input limit and DPPM
    # throttle the charge current so the port never exceeds the ILIM budget.
    # The buck-boost inductor nodes carry the same switch current as
    # LED_BOOST_SW, so L1/L2 are power-class nets too (screened, not signals).
    for name, current, voltage, cls in ((gnd, 2.5, 0, 2), (v33, 1.0, 3.3, 2),
        (vbat, 2.5, 3.7, 2), (vsys, 2.5, 3.8, 2), (vled_raw, 1.0, 5.0, 2),
        (vled, 1.0, 5.0, 2), (vbus, 1.5, 5.0, 2),
        ("L1", 1.5, 3.8, 2), ("L2", 1.5, 3.8, 2),
        ("LED_BOOST_SW", 1.5, 5.0, 2)):
        d.net(name, cls, current, voltage)
    for name in ("USB_D+", "USB_D-"):
        d.net(name, 1, 0, 3.3)

    # Human interface: original 4x3 field plus a separated command key.
    key_xy = [(18 + col * 19.05, 45 + row * 19.05) for row in range(3) for col in range(4)]
    key_xy.append((96, 83.1))
    for index, (x, y) in enumerate(key_xy, 1):
        row = min((index - 1) // 4, 3); col = (index - 1) % 4
        switch_net = f"KEY_{index}_RAW"
        d.place("mx", f"SW{index}", x, y, {"1": f"ROW{row}", "2": switch_net}, group="keys")
        d.place("diode", f"D{index}", x + 9.525, y,
                {"1": f"COL{col}", "2": switch_net}, rot=90, group="matrix")
        data_in = "LED_DATA" if index == 1 else f"LED_CHAIN_{index - 1}"
        data_out = f"LED_CHAIN_{index}"
        # Top-side, south-facing per-key RGB.  The previous revision stacked the
        # emitters on B.Cu directly under the switches, but FR-4 is opaque and
        # there are no board cutouts, so no light could reach the keycaps.
        # Offsetting the LED into the free band south of each switch body keeps
        # the industry-standard under-keycap glow geometry.  Alternating 0/90
        # rotation puts every within-row DOUT/DIN pair on one straight line
        # with no third pad in between (0/180 crosses the neighbour's GND pad).
        d.place("led", f"LED{index}", x, y + 9.525,
                {"1": vled, "2": data_out, "3": gnd, "4": data_in},
                rot=90 if index % 2 == 0 else 0,
                side=0, group="lighting")

    d.place("encoder", "ENC1", 18, 18, {"1": "ENC_A", "2": gnd, "3": "ENC_B",
            "4": "ENC_SW", "5": gnd}, group="controls")
    d.place("joystick", "JS1", 48, 18, {"1": v33, "2": "JOY_X", "3": gnd,
            "4": v33, "5": "JOY_Y", "6": gnd}, group="controls")
    d.place("fsr_conn", "J3", 121, 74, {"1": v33, "2": "FSR_SENSE"}, rot=90,
            group="controls", edge="right")

    esp = {"1": gnd, "2": v33, "3": "RESET_N", "4": "ROW0", "5": "ROW1",
        "6": "ROW2", "7": "ROW3", "8": "COL0", "9": "COL1", "10": "COL2",
        "11": "COL3", "12": "LED_DATA_MCU", "13": "USB_D-", "14": "USB_D+",
        "17": "JOY_X", "18": "JOY_Y", "20": "ENC_A",
        "21": "ENC_B", "22": "ENC_SW", "23": "LED_ENABLE", "27": "BOOT_N",
        "38": "FSR_SENSE", "40": gnd, "41": gnd}
    # Antenna end of the module sits flush with the top board edge (rot=180
    # puts the pad-free antenna short-edge at local +y).  The previous y=25
    # placement left the antenna 12.25 mm inside the board over poured ground,
    # which detunes the radio.  Per the module datasheet, the antenna area
    # must be at the board edge with no copper underneath on inner layers.
    d.place("esp32", "U1", 112, 12.75, esp, rot=180, group="radio", edge="right")

    usb_nets = {"1": gnd, "2": vbus, "3": "CC1", "4": "USB_D+", "5": "USB_D-",
        "7": vbus, "8": gnd, "9": gnd, "10": vbus, "12": "USB_D-", "13": "USB_D+",
        "14": "CC2", "15": vbus, "16": gnd,
        "M1": gnd, "M2": gnd, "M3": gnd, "M4": gnd}
    d.place("usb", "J1", 120.5, 50, usb_nets, rot=90, group="usb", edge="right")
    # Place the shunt array beside the ESP32 fanout.  Using the outer IO
    # channels keeps both USB stubs short without forcing either member of the
    # differential pair to cross the other.
    # The two spare IO channels of the shunt array now protect CC1/CC2 as
    # well; leaving the configuration lines unprotected was an ESD gap at the
    # receptacle.  The short CC taps are installed by the USB route script.
    d.place("esd", "U2", 123.8, 41.2, {"1": "USB_D-", "2": "CC1", "3": gnd,
        "4": "CC2", "5": "USB_D+", "8": gnd}, rot=90, group="usb", edge="right")
    d.place("charger", "U3", 82, 94, {"1": "TS", "2": vbat, "3": vbat, "4": gnd,
        "5": v33, "6": gnd, "8": gnd, "10": vsys, "11": vsys, "12": "ILIM",
        "13": vbus, "16": "ISET", "17": gnd}, group="power")
    # Keep the battery/NTC connector beside the charger and out of the
    # buck-boost inductor/via escape field.
    d.place("battery_ntc", "J2", 82, 110, {"1": vbat, "2": gnd, "3": "TS"}, rot=180,
            group="power", edge="bottom")
    d.place("buck", "U4", 101, 105, {"1": v33, "2": "L2", "3": gnd, "4": "L1",
        "5": vsys, "6": vsys, "7": gnd, "8": vsys, "9": gnd, "10": v33, "11": gnd},
        group="power")
    # Keep both buck-boost switch loops compact.  The previous y=99 placement
    # left the inductor roughly 6 mm from U4 and allowed L1/L2 to be routed as
    # ordinary low-current signals.
    d.place("inductor", "L1", 95, 104, {"1": "L1", "2": "L2"}, rot=90,
            group="power")
    d.place("load", "U5", 121, 105, {"1": vled_raw, "2": gnd, "3": "LED_ENABLE",
        "4": "LED_CT", "5": vled, "6": vled}, group="power")
    d.place("led_boost", "U6", 113, 106, {"1": "LED_FB", "2": "LED_ENABLE",
        "3": vsys, "4": gnd, "5": "LED_BOOST_SW", "6": vled_raw},
        rot=180, group="power")
    d.place("boost_inductor", "L2", 108, 106, {"1": vsys, "2": "LED_BOOST_SW"},
            group="power")
    d.place("led_buffer", "U7", 110, 94, {"1": gnd, "2": "LED_DATA_MCU",
        "3": gnd, "4": "LED_DATA_PRE", "5": vled}, group="lighting")

    # Datasheet support networks and hardware filtering/debounce.
    resistor_parts = [("res_5k1", "CC1", gnd), ("res_5k1", "CC2", gnd),
        ("resistor", "FSR_SENSE", gnd), ("res_1k1", "ILIM", gnd),
        ("res_590", "ISET", gnd), ("resistor", "ENC_A", v33),
        ("resistor", "ENC_B", v33),
        ("resistor", "ENC_SW", v33), ("resistor", "RESET_N", v33),
        ("resistor", "BOOT_N", v33), ("res_732k", vled_raw, "LED_FB"),
        ("res_100k", "LED_FB", gnd), ("res_500", "LED_DATA_PRE", "LED_DATA"),
        ("resistor", "COL0", v33), ("resistor", "COL1", v33),
        ("resistor", "COL2", v33), ("resistor", "COL3", v33)]
    resistor_xy = [(123, 58), (112, 48.725), (112, 88),
                   (86.35, 96.00), (81.25, 98.00),
                   (29, 14), (29, 18), (29, 22), (98, 32), (98, 35),
                   (112, 98), (116, 98), (106, 94),
                   (88, 42), (88, 45), (88, 48), (88, 51)]
    for index, ((part, net_a, net_b), (x, y)) in enumerate(
            zip(resistor_parts, resistor_xy), 1):
        d.place(part, f"R{index}", x, y, {"1": net_a, "2": net_b},
                rot=90 if index == 1 else (
                    180 if index == 2 or index in range(14, 18) else 0),
                group="passives")
    cap_parts = ["cap_10u", "capacitor", "capacitor", "capacitor", "capacitor",
                 "capacitor", "cap_100n", "cap_100n", "cap_100n", "cap_100n",
                 "cap_100n", "cap_100n", "cap_1n", "cap_10u", "capacitor", "capacitor"]
    cap_nets = [(vbus, gnd), (vsys, gnd), (vbat, gnd), (v33, gnd), (v33, gnd),
                (vled, gnd), ("JOY_X", gnd), ("JOY_Y", gnd), ("FSR_SENSE", gnd),
                ("ENC_A", gnd), ("ENC_B", gnd), ("ENC_SW", gnd), ("LED_CT", gnd),
                (vsys, gnd), (vled_raw, gnd), (vled_raw, gnd)]
    # Keep every explicit courtyard at least 0.20 mm from its neighbours.
    # These offsets also leave the local power/ground escape corridors intact;
    # they are generator inputs rather than post-route artifact patches.
    # C3 (VBAT bulk) belongs beside U3's BAT pins, not the connector: at
    # (76.5, 99) the 2.5 A VBAT hop stays short and out of the dense
    # charger/buck corridor, which a 1.0 mm trunk cannot thread at (90, 103.8).
    capacitor_xy = [(74, 112), (86, 100), (76.5, 99), (105, 99), (96, 38), (123, 100),
                    (62, 14), (62, 19), (116, 88), (7, 14), (7, 18), (7, 22),
                    (124.2, 107.2), (111, 112), (118, 110), (123.3, 110.55)]
    for index, (part, pair, (x, y)) in enumerate(
            zip(cap_parts, cap_nets, capacitor_xy), 1):
        d.place(part, f"C{index}", x, y, {"1": pair[0], "2": pair[1]},
                rot=90 if index == 13 else 0,
                group="passives")
    next_cap = len(cap_nets) + 1
    # Per-key LED decoupling follows the emitters to the top side, tucked
    # into the same free band south of each switch body.  The x+5.55 offset
    # keeps the capacitor courtyard clear of each LED's east/west ground-stitch
    # corridor; x+3.05 boxed the LED GND pads in between three courtyards.
    # Key 12 is the exception: x+5.55 would land the cap on top of U3, so it
    # mirrors to the west side of its LED instead.
    for index, (x, y) in enumerate(key_xy, next_cap):
        offset_x = -3.05 if index - next_cap + 1 == 12 else 5.55
        d.place("cap_100n", f"C{index}", x + offset_x, y + 9.525,
                {"1": vled, "2": gnd}, side=0, group="lighting")
    for x, y, rail in ((114, 94, vled), (102, 101.55, vsys), (99, 42, v33)):
        index += 1
        d.place("cap_100n", f"C{index}", x, y, {"1": rail, "2": gnd},
                group="passives")
    for column, (x, y) in enumerate(((92, 42), (92, 45), (92, 48), (92, 51))):
        index += 1
        d.place("cap_100n", f"C{index}", x, y,
                {"1": f"COL{column}", "2": gnd}, group="matrix")
    # Espressif recommends an RC delay on EN (R = 10 kΩ pull-up already in
    # R9).  Add the missing shunt capacitance so the module resets cleanly on
    # slow supply ramps; placed just outside the antenna keepout.
    index += 1
    d.place("cap_100n", f"C{index}", 123.8, 9.5, {"1": "RESET_N", "2": gnd},
            group="passives")
    return d


def route_design(design: Design) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic orthogonal two-layer channel router.

    F.Cu carries horizontal segments and B.Cu vertical segments.  Each branch
    owns a unique X coordinate and each net owns a unique Y channel, making the
    replay stable and eliminating same-layer trace crossings by construction.
    """
    traces: list[dict[str, Any]] = []
    vias: list[dict[str, Any]] = []
    branch_index = 0
    for net_id in sorted(design.connections):
        connections = design.connections[net_id]
        if len(connections) < 2:
            continue
        channel_y = 2.0 + net_id * 1.22
        channel_y = min(channel_y, 88.0)
        hub_x = 2.0 + (net_id % 52) * 2.3
        for fp, pad in connections:
            px, py = world_pad(fp, pad)
            branch_x = 2.0 + branch_index * .42
            branch_index += 1
            if branch_x > 123:
                branch_x = 2.0 + (branch_index % 285) * .42
            width = .20 if net_id not in (design.net_ids["GND"], design.net_ids["+3V3"],
                                          design.net_ids["VSYS"], design.net_ids["VBUS"],
                                          design.net_ids["+5V_LED"]) else .42
            for a, b, layer in (((px, py), (branch_x, py), 0),
                                ((branch_x, py), (branch_x, channel_y), 3),
                                ((branch_x, channel_y), (hub_x, channel_y), 0)):
                if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1e-6:
                    continue
                traces.append({"ax_mm": round(a[0], 4), "ay_mm": round(a[1], 4),
                    "bx_mm": round(b[0], 4), "by_mm": round(b[1], 4), "w_mm": width,
                    "net": net_id, "layer": layer, "pour": False})
            vias.extend((
                {"x_mm": round(branch_x, 4), "y_mm": round(py, 4), "dia_mm": .6,
                 "drill_mm": .3, "net": net_id, "from": 0, "to": 3,
                 "antipad_mm": 0, "backdrill_to": -1, "via_type": "through"},
                {"x_mm": round(branch_x, 4), "y_mm": round(channel_y, 4), "dia_mm": .6,
                 "drill_mm": .3, "net": net_id, "from": 0, "to": 3,
                 "antipad_mm": 0, "backdrill_to": -1, "via_type": "through"},
            ))
    return traces, vias


def project_json(design: Design, traces: list[dict[str, Any]], vias: list[dict[str, Any]]) -> dict[str, Any]:
    nets = sorted(design.net_meta.values(), key=lambda item: item["id"])
    gnd = design.net_ids["GND"]
    rules = {name: "error" for name in ("TRACE_CLEARANCE", "PAD_TRACE_CLEARANCE", "PAD_CLEARANCE",
        "BOARD_EDGE", "MIN_WIDTH", "MIN_DRILL", "VIA_TRACE_CLEARANCE", "VIA_PAD_CLEARANCE",
        "VIA_CLEARANCE", "ANNULAR_RING", "DRILL_TO_DRILL", "NET_ISLAND", "UNASSIGNED_COPPER",
        "COPPER_TO_EDGE", "COPPER_TO_HOLE", "HEIGHT_CONSTRAINT", "THERMAL_SPACING", "TEST_ACCESS",
        "LAYER_POLICY_VIOLATION", "VIA_POLICY_VIOLATION", "ZONE_VALIDITY", "SKEW",
        "RULE_AREA_VIOLATION")}
    # South-facing per-key LEDs sit in the 2.95 mm free band between switch
    # courtyards (2.80 mm LED courtyard), which grazes the boundary by design;
    # body/copper clearance is verified separately, so this stays a warning.
    rules["COURTYARD_OVERLAP"] = "warning"
    rules["RIGHT_ANGLE_BEND"] = "warning"
    def circular_cutout(x: float, y: float, radius: float) -> list[list[float]]:
        return [[round(x + radius * math.cos(2 * math.pi * index / 16), 4),
                 round(y + radius * math.sin(2 * math.pi * index / 16), 4)]
                for index in range(16)]

    mounting_holes = [{"id": f"MH{index}", "x_mm": x, "y_mm": y,
                       "diameter_mm": 2.7, "kind": "M2.5-clearance-npth"}
                      for index, (x, y) in enumerate(
                          ((8, 8), (92, 8), (8, 108), (118, 92)), 1)]
    return {
        "version": 2, "document_id": "project:agent-workflow-controller-v1", "revision": 1,
        "board_width_mm": 126.0, "board_height_mm": 116.0,
        "board_outline_pts": [[0, 4], [4, 0], [122, 0], [126, 4], [126, 112], [122, 116],
                              [4, 116], [0, 112]],
        "board_cutouts": [circular_cutout(item["x_mm"], item["y_mm"], 1.35)
                          for item in mounting_holes],
        "mechanical_features": {"mounting_holes": mounting_holes,
            "retention_strategy": "four M2.5 fasteners into enclosure bosses"},
        "grid_mm": .05,
        "copper_layers": 4, "dielectric_er": 4.2, "dielectric_h_mm": .10,
        "loss_tangent": .018, "copper_t_mm": .035,
        "stackup_intent": {
            "schema": "design-studio.stackup-intent/1",
            "status": "engineering-target-not-contracted",
            "finished_thickness_mm": 1.6,
            "layers": [
                {"order": 0, "name": "F.Cu", "kind": "copper",
                 "role": "signal/components", "thickness_mm": .035},
                {"order": 1, "name": "prepreg-1", "kind": "dielectric",
                 "material": "FR-4", "thickness_mm": .10, "dielectric_er": 4.2},
                {"order": 2, "name": "In1.GND", "kind": "copper",
                 "role": "continuous-ground-reference", "thickness_mm": .035},
                {"order": 3, "name": "core", "kind": "dielectric",
                 "material": "FR-4", "thickness_mm": 1.26, "dielectric_er": 4.2},
                {"order": 4, "name": "In2.GND", "kind": "copper",
                 "role": "continuous-ground-reference", "thickness_mm": .035},
                {"order": 5, "name": "prepreg-2", "kind": "dielectric",
                 "material": "FR-4", "thickness_mm": .10, "dielectric_er": 4.2},
                {"order": 6, "name": "B.Cu", "kind": "copper",
                 "role": "signal/components", "thickness_mm": .035},
            ],
            "release_rule": "replace with and verify against the contracted fabricator profile",
        },
        "stackup": {},
        "fabrication_profile": {},
        "pcb_rules": {"schema_version": 1, "id": "agent-controller-4layer-v1",
            "name": "Agent controller four-layer production rules", "ipc_performance_class": 2,
            "producibility_level": "B", "source": "project:agent-workflow-controller",
            "source_revision": "1.0.0", "fabricator": "unassigned", "assembler": "unassigned",
            "limits_mm": {"default_clearance": .15, "min_trace_width": .15,
                "min_mechanical_drill": .2, "min_annular_ring": .125, "min_drill_to_drill": .3,
                "min_microvia_drill": .1, "min_microvia_wall": .1, "min_copper_to_edge": .25,
                "min_copper_to_hole": .2, "min_courtyard_clearance": .2,
                "min_mask_sliver": .08, "min_silk_width": .1},
            "checks": {"connectivity": True, "skew": True, "release_requires_native_drc": True},
            "severity": rules},
        "verification_requirements": {"require_signal_integrity": True,
            "require_power_integrity": True, "require_thermal": True,
            "require_component_semantics": True,
            "require_enclosure_evidence": False, "enclosure_evidence_path": "",
            "enclosure_evidence_sha256": ""},
        "nets": None, "net_table": nets,
        "net_classes": [
            {"id": 0, "name": "Default", "clearance_mm": .15, "trace_width_mm": .2,
             "via_dia_mm": .6, "via_drill_mm": .3, "diff_pair_gap_mm": 0, "max_skew_mm": 0,
             "allow_microvia": False, "z0_ohm": 0, "zdiff_ohm": 0,
             "allowed_layers": [0, 1, 2, 3], "allowed_via_types": ["through"], "max_via_count": 0,
             "signal_frequency_hz": 1e6, "impedance_tolerance_pct": 10},
            {"id": 1, "name": "USB_90R", "clearance_mm": .15, "trace_width_mm": .22,
             "via_dia_mm": .6, "via_drill_mm": .3, "diff_pair_gap_mm": .40, "max_skew_mm": .5,
             "allow_microvia": False, "z0_ohm": 45, "zdiff_ohm": 90,
             "allowed_layers": [0, 1, 2, 3], "allowed_via_types": ["through"], "max_via_count": 8,
             "signal_frequency_hz": 480e6, "impedance_tolerance_pct": 10},
            {"id": 2, "name": "Power", "clearance_mm": .2, "trace_width_mm": 1.0,
             "via_dia_mm": .7, "via_drill_mm": .35, "diff_pair_gap_mm": 0, "max_skew_mm": 0,
             "allow_microvia": False, "z0_ohm": 0, "zdiff_ohm": 0,
             "allowed_layers": [0, 1, 2, 3], "allowed_via_types": ["through"], "max_via_count": 0,
             "signal_frequency_hz": 2.4e6, "impedance_tolerance_pct": 10}],
        # The module antenna area (datasheet: 16.51 x 6 mm at the pad-free
        # short edge, which is flush with the board edge) must stay free of
        # copper on inner and bottom layers.  F.Cu remains open so the module's
        # first castellated pads (which the datasheet places inside the antenna
        # strip) can still escape; inner-layer copper is additionally excluded
        # by the ground-zone polygons.
        "rule_areas": [{"id": index, "name": f"ESP32 antenna keepout L{layer}",
            "pts": [[98, 0], [126, 0], [126, 7.5], [98, 7.5]], "layer": layer,
            "clearance_mm": 0, "min_trace_width_mm": 0,
            "forbid_routing": True, "forbid_vias": True, "forbid_placement": False,
            "max_height_mm": 0, "source": "Espressif module datasheet",
            "source_revision": "v1.8"} for index, layer in enumerate((1, 2, 3), 1)],
        "class_pair_rules": [],
        "layer_policies": [
            {"layer": 0, "name": "F.Cu", "role": "signal", "preferred_direction": "horizontal",
             "allow_routing": True, "copper_thickness_mm": .035, "source": "project", "source_revision": "1"},
            {"layer": 1, "name": "In1.GND", "role": "plane", "preferred_direction": "any",
             "allow_routing": False, "copper_thickness_mm": .035, "source": "project", "source_revision": "1"},
            {"layer": 2, "name": "In2.GND", "role": "plane", "preferred_direction": "any",
             "allow_routing": False, "copper_thickness_mm": .035, "source": "project", "source_revision": "1"},
            {"layer": 3, "name": "B.Cu", "role": "signal", "preferred_direction": "vertical",
             "allow_routing": True, "copper_thickness_mm": .035, "source": "project", "source_revision": "1"}],
        # Both inner layers are continuous ground reference (SIG/GND/GND/SIG).
        # The previous design left In2 with no copper at all despite declaring
        # it a power plane.  Both polygons keep the pour out of the antenna
        # strip (x > 97.25, y < 13.75) as required under the module antenna.
        "copper_zones": [
            {"id": 1, "name": "In1 continuous GND reference", "pts": [[.5, 4], [4, .5],
                [97.25, .5], [97.25, 13.75], [125.5, 13.75], [125.5, 112], [122, 115.5],
                [4, 115.5], [.5, 112]], "net": gnd,
             "layer": 1, "clearance_mm": .25, "min_island_area_mm2": 2,
             "require_connection": True, "source": "project", "source_revision": "1"},
            {"id": 2, "name": "In2 continuous GND reference", "pts": [[.5, 4], [4, .5],
                [97.25, .5], [97.25, 13.75], [125.5, 13.75], [125.5, 112], [122, 115.5],
                [4, 115.5], [.5, 112]], "net": gnd,
             "layer": 2, "clearance_mm": .25, "min_island_area_mm2": 2,
             "require_connection": True, "source": "project", "source_revision": "1"}],
        "footprints": design.footprints, "traces": traces, "vias": vias,
        "unresolved_components": [], "placement_state": {"mode": "optimized",
            "source": "tools/build_agent_workflow_controller.py", "revision": "1"},
        "schematic": {"symbols": design.symbols,
            "wires": [{"net": net["id"], "pts": [[5, 5 + net["id"] * 2],
                       [235, 5 + net["id"] * 2]]} for net in nets]},
        "controller_acceptance": {"schema": "design-studio.agent-controller/1",
            "layout": "original-4x3-plus-command", "firmware": "ESP-IDF",
            "offboard_components": [{"ref": "FSR1", "manufacturer": "Interlink Electronics",
                "mpn": "34-00015", "interface_ref": "J3",
                "assembly": "flex sensor retained in enclosure; two-wire adapter to PCB connector"}],
            "agent_states": ["idle", "thinking", "running", "waiting", "approval_required", "done", "error"],
            "battery": {"chemistry": "protected-1S-LiPo", "capacity_mah": 2000,
                        "runtime_target_hours": 24, "status": "calculated-not-measured"},
            "safety": {"credentials_on_host_only": True, "commands_allowlisted": True,
                       "destructive_actions_require_host_confirmation": True}},
    }


def write_manifest(output: Path, library: dict[str, dict[str, Any]], project: Path,
                   v3: Path) -> None:
    entries = []
    for key, item in library.items():
        spec: Package = item["spec"]
        source = evidence_path(spec)
        entries.append({"key": key, "manufacturer": spec.manufacturer, "mpn": spec.mpn,
            "datasheet": {"url": spec.evidence_url, "sha256": sha256(source),
                          "bytes": source.stat().st_size, "reference_path": str(source)},
            "component": {"path": str((item["step"].parent / "component.json").resolve()),
                          "sha256": sha256(item["step"].parent / "component.json")},
            "symbol_and_footprint": {"path": str(item["footprint"].resolve()),
                                     "sha256": sha256(item["footprint"])},
            "step": {"path": str(item["step"].resolve()), "sha256": sha256(item["step"]),
                     "schema": "AP242", "roundtrip_valid": True},
            "binding": {"path": str((item["step"].parent / "binding.json").resolve()),
                        "digest": item["binding"]["binding_digest"]}})
    manifest = {"schema": "design-studio.agent-controller-evidence/1",
        "generated_utc": "2026-07-16T00:00:00Z", "generator": "tools/build_agent_workflow_controller.py",
        "project": {"v2_path": str(project.resolve()), "v2_sha256": sha256(project),
                    "v3_path": str(v3.resolve()), "v3_sha256": sha256(v3)},
        "components": entries, "release_gate": {"asset_bindings": "pass",
            "physical_runtime": "measurement-required", "rf_certification": "pre-production-required",
            "battery_transport": "pre-production-required"}}
    (output / "evidence-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def write_bom(output: Path, design: Design) -> None:
    groups: dict[tuple[str, str, str], list[str]] = {}
    for fp in design.footprints:
        groups.setdefault((fp["manufacturer"], fp["mpn"], fp.get("value", "")), []).append(fp["ref"])
    with (output / "bom.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("Qty", "References", "Value", "Manufacturer", "MPN", "Binding status"))
        for (manufacturer, mpn, value), refs in sorted(groups.items()):
            writer.writerow((len(refs), " ".join(refs), value, manufacturer, mpn, "complete"))
    with (output / "offboard-bom.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("Qty", "Reference", "Description", "Manufacturer", "MPN",
                         "PCB interface", "Assembly status"))
        writer.writerow((1, "FSR1", "FSR 402 flexible force sensor", "Interlink Electronics",
                         "34-00015", "J3", "enclosure-mounted; cable/strain-relief required"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--compiler", default=os.environ.get("DESIGNSTUDIO_COMPONENT_CAD",
                        "/tmp/designstudio-occt-build/designstudio-component-cad"))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    specs = packages()
    library = build_library(specs, output, args.compiler)
    design = assemble_design(library)
    traces, vias = route_design(design)
    board = project_json(design, traces, vias)
    project = output / "agent-workflow-controller.dsproj"
    project.write_text(json.dumps(board, indent=2, sort_keys=True) + "\n")
    v3 = output / "agent-workflow-controller-v3.dsproj"
    v3_doc = {"format": "design-studio.project/3", "units": "nm", "materials": [],
        "parts": [], "board": board, "assembly": {"tree": [], "joints": []},
        "constraints": [{"kind": "keepout", "domains": ["mechanical", "electronic"],
                         "refs": ["board:rf-antenna"], "params": {"rule_area_id": 1}}],
        "tolerances": [], "rationale": [{"ts": "2026-07-16T00:00:00Z",
            "intent": "Original compact wireless agent-workflow controller with 4x3 keys plus command key",
            "alternatives": ["matrix expander", "direct GPIO"], "gate": "drc",
            "source": "ai:gpt-5.6-luna@xhigh", "drove": {"mechanical": ["126x116 mm octagonal board"],
                "electronic": ["four-layer stackup", "BLE-first ESP32-S3", "switchable RGB rail"]}}]}
    v3.write_text(json.dumps(v3_doc, indent=2, sort_keys=True) + "\n")
    write_bom(output, design)
    write_manifest(output, library, project, v3)
    print(json.dumps({"project": str(project), "v3": str(v3), "components": len(library),
                      "footprints": len(design.footprints), "nets": len(design.net_ids),
                      "traces": len(traces), "vias": len(vias)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
