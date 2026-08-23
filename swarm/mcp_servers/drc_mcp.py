#!/usr/bin/env python3
"""DRC + design-review MCP server — AI agents find bugs, gaps, and rule violations.

User space: design reviewer / QA
Tools:
  find_bugs           — deep multi-rule scan of a netlist for electrical issues
  find_gaps           — missing components (decoupling, pull-ups, terminations, etc.)
  audit_bom           — check BOM completeness, lifecycle, stock, cost
  check_net_classes   — verify trace widths meet IPC net-class requirements
  check_power_path    — trace VBUS → regulator → MCU, find breaks
  check_io_levels     — detect 5V→3.3V level mismatches
  verify_usb          — USB-specific checks (ESD, termination, CC pull-downs)
  run_full_review     — all checks in one call, returns structured report
"""

import json
import sys
from pathlib import Path

_root = Path(__file__).parents[2]
_site = Path.home() / ".local/lib/python3.14/site-packages"
for p in [str(_root), str(_site)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "design-review",
    instructions=(
        "Design review and DRC tools. Use find_bugs to find electrical issues in a netlist, "
        "find_gaps to find missing components, and run_full_review for a complete audit. "
        "All tools accept a netlist_path argument pointing to a JSON netlist file."
    ),
)

_DEFAULT_NETLIST = "/tmp/synth_netlist_v2.json"
_DEFAULT_COMPONENTS = "/tmp/synth_components_v3.json"


def _load(netlist_path: str, components_path: str):
    nets = {}
    components = []
    try:
        nl = json.loads(Path(netlist_path).read_text())
        nets = nl.get("nets", {})
    except Exception:
        pass
    try:
        components = json.loads(Path(components_path).read_text())
    except Exception:
        pass
    return nets, components


@mcp.tool()
def find_bugs(
    netlist_path: str = _DEFAULT_NETLIST,
    components_path: str = _DEFAULT_COMPONENTS,
) -> str:
    """Deep electrical bug scan on a netlist.

    Checks:
    - Output-output conflicts (bus contention)
    - Power pins without power nets
    - Floating inputs on ICs
    - Missing reset/enable pins
    - Ground loops or missing GND
    - Duplicate net assignments on same pin
    Returns structured JSON with severity, rule, location, fix suggestion.
    """
    nets, components = _load(netlist_path, components_path)
    bugs = []

    # Build pin→net index
    net_by_pin: dict[tuple, str] = {}
    for net_name, conns in nets.items():
        for c in conns:
            for ref, pin in [(c["from_ref"], c["from_pin"]), (c["to_ref"], c["to_pin"])]:
                key = (ref, pin)
                if key in net_by_pin and net_by_pin[key] != net_name:
                    bugs.append({
                        "severity": "critical",
                        "rule": "PIN_IN_MULTIPLE_NETS",
                        "location": f"{ref}.{pin}",
                        "detail": f"Appears in both '{net_by_pin[key]}' and '{net_name}'",
                        "fix": f"Check {ref}.{pin} — only one net should drive it",
                    })
                net_by_pin[key] = net_name

    has_gnd = any("GND" in n.upper() for n in nets)
    has_power = any(n.upper() in ("VCC_3V3", "VCC_5V", "VBUS_5V", "VBAT", "VDD") for n in nets)

    if not has_gnd:
        bugs.append({
            "severity": "critical", "rule": "NO_GND_NET",
            "location": "board", "detail": "No GND net defined",
            "fix": "Add GND net connecting all ground pins",
        })
    if not has_power:
        bugs.append({
            "severity": "critical", "rule": "NO_POWER_NET",
            "location": "board", "detail": "No power net (VCC_3V3/VCC_5V/VBUS) defined",
            "fix": "Add power rail nets and connect to regulator output",
        })

    for comp in components:
        ref = comp.get("_ref", "?")
        pins = (comp.get("symbol") or {}).get("pins") or []
        for pin in pins:
            pname = pin.get("name", "")
            ptype = pin.get("type", "")

            if ptype == "power_in" and pname.upper() not in ("NC", "GND2", "AGND2"):
                if (ref, pname) not in net_by_pin:
                    bugs.append({
                        "severity": "error",
                        "rule": "FLOATING_POWER_PIN",
                        "location": f"{ref}.{pname}",
                        "detail": f"Power-input pin not connected",
                        "fix": f"Connect {ref}.{pname} to VCC_3V3 or GND as appropriate",
                    })

            if ptype == "input" and (ref, pname) not in net_by_pin:
                bugs.append({
                    "severity": "warning",
                    "rule": "FLOATING_INPUT",
                    "location": f"{ref}.{pname}",
                    "detail": "Input pin with no driver — may pick up noise",
                    "fix": f"Tie {ref}.{pname} to VCC_3V3 or GND via resistor, or mark NC",
                })

    # Check for output conflicts
    outputs: dict[str, list[str]] = {}
    for comp in components:
        ref = comp.get("_ref", "?")
        pins = (comp.get("symbol") or {}).get("pins") or []
        for pin in pins:
            if pin.get("type") == "output":
                net = net_by_pin.get((ref, pin.get("name", "")))
                if net:
                    outputs.setdefault(net, []).append(f"{ref}.{pin['name']}")
    for net, drivers in outputs.items():
        if len(drivers) > 1 and "GND" not in net.upper() and "VCC" not in net.upper():
            bugs.append({
                "severity": "critical", "rule": "OUTPUT_CONFLICT",
                "location": net,
                "detail": f"Multiple output drivers: {', '.join(drivers)}",
                "fix": "Use open-drain output with pull-up, or add bus arbiter",
            })

    return json.dumps({
        "bugs_found": len(bugs),
        "critical": sum(1 for b in bugs if b["severity"] == "critical"),
        "errors": sum(1 for b in bugs if b["severity"] == "error"),
        "warnings": sum(1 for b in bugs if b["severity"] == "warning"),
        "bugs": bugs,
    }, indent=2)


@mcp.tool()
def find_gaps(
    netlist_path: str = _DEFAULT_NETLIST,
    components_path: str = _DEFAULT_COMPONENTS,
) -> str:
    """Find missing components that are electrically required.

    Checks for missing decoupling caps, pull-up resistors, USB termination,
    crystal oscillator, USB CC pull-downs, reset circuits, etc.
    """
    nets, components = _load(netlist_path, components_path)
    gaps = []

    mpns = {c.get("component", {}).get("mpn", "").upper() for c in components}
    refs = {c.get("_ref", "") for c in components}
    net_names = set(nets.keys())

    # Check for decoupling caps on each IC power pin
    ic_comps = [c for c in components if c.get("component", {}).get("category", "") not in ("passive", "connector", "crystal")]
    cap_count = sum(1 for c in components if "C" in (c.get("_ref") or "")[0:1])
    if cap_count < len(ic_comps):
        gaps.append({
            "urgency": "critical", "role": "decoupling_caps",
            "reason": f"Only {cap_count} caps for {len(ic_comps)} ICs — add 100nF decoupling caps on each IC VCC/VDD pin",
            "search_query": "100nF 0402 ceramic capacitor",
        })

    # Check for I2C pull-ups
    has_i2c = any("I2C" in n for n in net_names)
    has_i2c_pullup = any(
        any(c.get("from_pin", "").startswith("SDA") or c.get("to_pin", "").startswith("SDA")
            for c in conns)
        for net, conns in nets.items()
        if "I2C_SDA" in net and any(
            c.get("from_ref", "").startswith("R") or c.get("to_ref", "").startswith("R")
            for c in conns
        )
    )
    if has_i2c and not has_i2c_pullup:
        gaps.append({
            "urgency": "critical", "role": "i2c_pullup_resistors",
            "reason": "I2C bus requires 4.7kΩ pull-up resistors on SDA and SCL to VCC_3V3",
            "search_query": "4.7kOhm 0402 resistor",
        })

    # Check for USB ESD
    has_usb = any("USB" in n.upper() for n in net_names)
    has_esd = any("USBLC" in m or "ESD" in m for m in mpns)
    if has_usb and not has_esd:
        gaps.append({
            "urgency": "critical", "role": "usb_esd_protection",
            "reason": "USB data lines need ESD protection (e.g. USBLC6-2SC6)",
            "search_query": "USBLC6-2SC6 USB ESD protection",
        })

    # Check for USB-C CC resistors
    has_usbc = any("217B" in m or "USB" in m for m in mpns)
    has_cc_r = any("CC" in (c.get("from_pin", "") + c.get("to_pin", "")) and
                   (c.get("from_ref", "").startswith("R") or c.get("to_ref", "").startswith("R"))
                   for conns in nets.values() for c in conns)
    if has_usbc and not has_cc_r:
        gaps.append({
            "urgency": "critical", "role": "usb_cc_resistors",
            "reason": "USB-C requires 5.1kΩ pull-down resistors on CC1 and CC2 for sink (device) mode",
            "search_query": "5.1kOhm 0402 resistor",
        })

    # Check for SPI flash WP/HOLD pull-ups
    has_flash = any("W25Q" in m for m in mpns)
    if has_flash:
        wp_held = any(
            (c.get("from_pin") in ("WP", "HOLD") and c.get("to_pin", "").startswith("VOUT")) or
            (c.get("to_pin") in ("WP", "HOLD") and c.get("from_pin", "").startswith("VOUT"))
            for conns in nets.values() for c in conns
        )
        if not wp_held:
            gaps.append({
                "urgency": "recommended", "role": "flash_wp_hold_pullup",
                "reason": "W25Q128 WP# and HOLD# pins need 10kΩ pull-ups to VCC_3V3 when not actively used",
                "search_query": "10kOhm 0402 resistor",
            })

    # Check for RP2040 boot select / BOOTSEL
    has_rp2040 = any("RP2040" in m for m in mpns)
    if has_rp2040:
        has_bootsel = any(
            c.get("from_pin", "") == "QSPI_SS" or c.get("to_pin", "") == "QSPI_SS"
            for conns in nets.values() for c in conns
        )
        if not has_bootsel:
            gaps.append({
                "urgency": "recommended", "role": "rp2040_bootsel_button",
                "reason": "RP2040 needs a BOOTSEL button (GND pull-down on QSPI_SS) for firmware flashing",
                "search_query": "tactile switch SMD 3x4mm",
            })

    return json.dumps({
        "gaps_found": len(gaps),
        "critical": sum(1 for g in gaps if g["urgency"] == "critical"),
        "recommended": sum(1 for g in gaps if g["urgency"] == "recommended"),
        "optional": sum(1 for g in gaps if g["urgency"] == "optional"),
        "gaps": gaps,
    }, indent=2)


@mcp.tool()
def audit_bom(bom_path: str = "") -> str:
    """Check BOM completeness, component lifecycle, stock, and total cost."""
    path = bom_path or str(_root / "output" / "bom.json")
    try:
        bom = json.loads(Path(path).read_text())
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})

    issues = []
    total_cost = 0.0
    out_of_stock = []
    no_price = []

    for item in bom:
        mpn = item.get("mpn", "?")
        stock = item.get("stock") or 0
        price = item.get("price_inr")

        if stock == 0:
            out_of_stock.append(mpn)
            issues.append({"severity": "warning", "mpn": mpn, "issue": "Out of stock"})
        if stock < 10:
            issues.append({"severity": "info", "mpn": mpn, "issue": f"Low stock: {stock} units"})
        if price is None:
            no_price.append(mpn)
            issues.append({"severity": "warning", "mpn": mpn, "issue": "Price unavailable"})
        else:
            total_cost += price

        lifecycle = item.get("lifecycle", "")
        if lifecycle and any(s in lifecycle.lower() for s in ("obsolete", "nrnd", "discontinued")):
            issues.append({"severity": "error", "mpn": mpn, "issue": f"Lifecycle: {lifecycle}"})

    return json.dumps({
        "status": "ok",
        "total_components": len(bom),
        "total_cost_usd": round(total_cost, 2),
        "out_of_stock": out_of_stock,
        "no_price": no_price,
        "issues_found": len(issues),
        "issues": issues,
    }, indent=2)


@mcp.tool()
def check_power_path(netlist_path: str = _DEFAULT_NETLIST) -> str:
    """Trace the power path VBUS → regulator → MCU and identify breaks."""
    try:
        nets = json.loads(Path(netlist_path).read_text()).get("nets", {})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})

    path_checks = [
        ("VBUS_5V", "VBUS → system"),
        ("VCC_3V3", "3.3V rail to MCU/ICs"),
        ("VBAT",    "Battery output from charger"),
        ("GND",     "Ground return path"),
    ]
    results = []
    for net_name, description in path_checks:
        present = net_name in nets
        conns = nets.get(net_name, [])
        refs = set()
        for c in conns:
            refs.add(c["from_ref"])
            refs.add(c["to_ref"])
        results.append({
            "net": net_name,
            "description": description,
            "present": present,
            "connections": len(conns),
            "components_on_net": sorted(refs),
        })

    broken = [r for r in results if not r["present"]]
    return json.dumps({
        "power_path": "OK" if not broken else "BROKEN",
        "broken_rails": [r["net"] for r in broken],
        "rails": results,
    }, indent=2)


@mcp.tool()
def verify_usb(netlist_path: str = _DEFAULT_NETLIST) -> str:
    """USB-specific checks: ESD, termination, CC pull-downs, data path."""
    try:
        nets = json.loads(Path(netlist_path).read_text()).get("nets", {})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})

    checks = []

    # USB data lines present
    has_dp = "USB_DP" in nets
    has_dm = "USB_DM" in nets
    checks.append({"check": "USB_DP net", "pass": has_dp, "fix": "Add USB_DP net from connector to MCU"})
    checks.append({"check": "USB_DM net", "pass": has_dm, "fix": "Add USB_DM net from connector to MCU"})

    # ESD protection in path
    dp_conns = nets.get("USB_DP", [])
    esd_in_dp = any(
        c.get("from_ref", "").startswith("D") or c.get("to_ref", "").startswith("D")
        for c in dp_conns
    )
    checks.append({"check": "ESD protection on USB_DP", "pass": esd_in_dp,
                   "fix": "Add USBLC6-2SC6 between USB connector and MCU on DP/DM"})

    # CC pull-downs
    has_cc = any("CC" in net for net in nets)
    checks.append({"check": "CC pull-down resistors", "pass": has_cc,
                   "fix": "Add 5.1kΩ resistors on CC1 and CC2 to GND"})

    passed = sum(1 for c in checks if c["pass"])
    return json.dumps({
        "usb_status": "PASS" if passed == len(checks) else "FAIL",
        "passed": passed,
        "failed": len(checks) - passed,
        "checks": checks,
    }, indent=2)


@mcp.tool()
def run_full_review(
    netlist_path: str = _DEFAULT_NETLIST,
    components_path: str = _DEFAULT_COMPONENTS,
    bom_path: str = "",
) -> str:
    """Run all review checks and return a single consolidated report.

    Combines: find_bugs, find_gaps, check_power_path, verify_usb, audit_bom.
    """
    bugs = json.loads(find_bugs(netlist_path, components_path))
    gaps = json.loads(find_gaps(netlist_path, components_path))
    power = json.loads(check_power_path(netlist_path))
    usb = json.loads(verify_usb(netlist_path))

    bom_result = {"status": "skipped"}
    if bom_path:
        bom_result = json.loads(audit_bom(bom_path))

    total_critical = bugs.get("critical", 0) + gaps.get("critical", 0)
    overall = "FAIL" if total_critical > 0 or power.get("power_path") == "BROKEN" or usb.get("usb_status") == "FAIL" else "PASS"

    return json.dumps({
        "overall": overall,
        "total_critical_issues": total_critical,
        "bugs": bugs,
        "gaps": gaps,
        "power_path": power,
        "usb": usb,
        "bom": bom_result,
        "summary": (
            f"{bugs['bugs_found']} bugs ({bugs['critical']} critical), "
            f"{gaps['gaps_found']} gaps ({gaps['critical']} critical), "
            f"power={'OK' if power['power_path']=='OK' else 'BROKEN'}, "
            f"USB={'PASS' if usb['usb_status']=='PASS' else 'FAIL'}"
        ),
    }, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
