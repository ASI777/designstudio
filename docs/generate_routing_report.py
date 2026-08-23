#!/usr/bin/env python3
"""Generate the AutoRoute root-cause analysis PDF using PyMuPDF (fitz)."""

import fitz  # PyMuPDF
from datetime import date

OUTPUT = "docs/autoroute_root_cause_analysis.pdf"

# ── Colour palette ─────────────────────────────────────────────────────────────
BG          = (0.08, 0.08, 0.12)
CARD        = (0.13, 0.13, 0.20)
ACCENT      = (0.27, 0.55, 1.00)   # blue
RED         = (1.00, 0.33, 0.33)
ORANGE      = (1.00, 0.65, 0.20)
GREEN       = (0.25, 0.85, 0.50)
YELLOW      = (1.00, 0.90, 0.20)
TEXT        = (0.93, 0.93, 0.97)
SUBTEXT     = (0.65, 0.65, 0.75)
CODE_BG     = (0.06, 0.06, 0.10)
CODE_TEXT   = (0.60, 0.90, 0.60)

W, H = 595, 842   # A4 points

def rgb(t): return t  # fitz takes (r,g,b) tuples directly

def new_page(doc):
    page = doc.new_page(width=W, height=H)
    page.draw_rect(fitz.Rect(0, 0, W, H), color=None, fill=BG, overlay=False)
    return page

FONT      = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_MONO = "Courier"

def heading(page, text, y, size=13, color=ACCENT):
    page.insert_text((40, y), text, fontsize=size, color=color, fontname=FONT_BOLD)
    return y + size + 4

def body(page, text, y, size=9, color=TEXT, x=40, max_width=515):
    """Word-wrap body text and return new y position."""
    words = text.split()
    line, lines = [], []
    char_w = size * 0.55
    max_chars = int(max_width / char_w)
    for w in words:
        if sum(len(s) + 1 for s in line) + len(w) > max_chars:
            lines.append(" ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(" ".join(line))
    for ln in lines:
        if y > H - 50:
            return y
        page.insert_text((x, y), ln, fontsize=size, color=color, fontname=FONT)
        y += size + 3
    return y + 2

def code_block(page, lines_text, y, x=48):
    """Render a monospace code block with dark background."""
    size = 7.5
    line_h = size + 3
    block_h = len(lines_text) * line_h + 8
    page.draw_rect(fitz.Rect(x - 4, y - 4, W - 40, y + block_h),
                   color=None, fill=CODE_BG)
    for ln in lines_text:
        page.insert_text((x, y + size), ln, fontsize=size,
                         color=CODE_TEXT, fontname=FONT_MONO)
        y += line_h
    return y + 12

def rule_card(page, num, title, file_ref, severity_color, desc_lines,
              code_lines, fix_lines, y):
    """Draw a single bug card. Returns new y."""
    card_h = 14 + len(desc_lines) * 12 + len(code_lines) * 11 + len(fix_lines) * 12 + 22
    if y + card_h > H - 40:
        return None   # signal: need new page

    page.draw_rect(fitz.Rect(36, y, W - 36, y + card_h),
                   color=severity_color, fill=CARD, width=1.5)

    # badge
    page.draw_rect(fitz.Rect(36, y, 68, y + card_h),
                   color=None, fill=severity_color)
    page.insert_text((39, y + card_h / 2 + 4), num, fontsize=9,
                     color=(1, 1, 1), fontname=FONT_BOLD)

    tx = 76
    cy = y + 10
    page.insert_text((tx, cy), title, fontsize=9.5,
                     color=TEXT, fontname=FONT_BOLD)
    cy += 12
    page.insert_text((tx, cy), file_ref, fontsize=7.5,
                     color=SUBTEXT, fontname=FONT_MONO)
    cy += 12

    for dl in desc_lines:
        page.insert_text((tx, cy), dl, fontsize=8, color=TEXT, fontname=FONT)
        cy += 11

    if code_lines:
        cy += 2
        page.draw_rect(fitz.Rect(tx - 2, cy - 2, W - 42, cy + len(code_lines) * 10 + 4),
                       color=None, fill=CODE_BG)
        for cl in code_lines:
            page.insert_text((tx + 2, cy + 8), cl, fontsize=7,
                             color=CODE_TEXT, fontname=FONT_MONO)
            cy += 10
        cy += 6

    page.insert_text((tx, cy), "FIX:", fontsize=8, color=GREEN, fontname=FONT_BOLD)
    cy += 11
    for fl in fix_lines:
        page.insert_text((tx + 6, cy), fl, fontsize=8, color=GREEN, fontname=FONT)
        cy += 11

    return y + card_h + 8

def priority_table(page, rows, y):
    cols = [36, 120, 300, 440, W - 36]
    headers = ["Priority", "Fix", "Impact", "File"]
    row_h = 16

    # header row
    page.draw_rect(fitz.Rect(36, y, W - 36, y + row_h), color=None, fill=ACCENT)
    for i, h in enumerate(headers):
        page.insert_text((cols[i] + 3, y + 11), h, fontsize=8,
                         color=(0.05, 0.05, 0.08), fontname=FONT_BOLD)
    y += row_h

    for ri, row in enumerate(rows):
        fill = (0.11, 0.11, 0.17) if ri % 2 == 0 else CARD
        page.draw_rect(fitz.Rect(36, y, W - 36, y + row_h), color=None, fill=fill)
        p_color = RED if row[0] == "P0" else (ORANGE if row[0] == "P1" else SUBTEXT)
        page.insert_text((cols[0] + 3, y + 11), row[0], fontsize=8,
                         color=p_color, fontname=FONT_BOLD)
        for i in range(1, 4):
            page.insert_text((cols[i] + 3, y + 11), row[i], fontsize=7.5,
                             color=TEXT, fontname=FONT)
        y += row_h
    return y + 10

# ══════════════════════════════════════════════════════════════════════════════
doc = fitz.open()

# ── PAGE 1: Cover ─────────────────────────────────────────────────────────────
p1 = new_page(doc)

# title block
p1.draw_rect(fitz.Rect(0, 0, W, 5), color=None, fill=ACCENT)
p1.draw_rect(fitz.Rect(0, H - 5, W, H), color=None, fill=ACCENT)

p1.insert_text((40, 90), "Design Studio", fontsize=28,
               color=ACCENT, fontname=FONT_BOLD)
p1.insert_text((40, 122), "AutoRoute Root-Cause Analysis", fontsize=18,
               color=TEXT, fontname=FONT_BOLD)
p1.insert_text((40, 148), "Why the router output is garbage vs Altium / KiCad / Flux.ai",
               fontsize=10, color=SUBTEXT, fontname=FONT)

p1.draw_rect(fitz.Rect(40, 158, W - 40, 159), color=ACCENT, fill=ACCENT)

p1.insert_text((40, 178), f"Date: {date.today().isoformat()}     "
               f"Project: single_axis_FOC_Field_Oriented_Control_SD2",
               fontsize=8, color=SUBTEXT, fontname=FONT)

# executive summary box
p1.draw_rect(fitz.Rect(36, 200, W - 36, 320), color=ACCENT, fill=CARD, width=1)
p1.insert_text((46, 217), "EXECUTIVE SUMMARY", fontsize=9,
               color=ACCENT, fontname=FONT_BOLD)

summary = [
    "Three structural bugs in CoreBridge.cpp are responsible for nearly all routing quality",
    "problems. They are independent of the A* engine (which is correct) and can each be",
    "fixed in under 20 lines of code:",
    "",
    "  BUG 1  Star topology — all pads connect back to pad[0] instead of MST.",
    "         Produces the starburst fan pattern visible in every screenshot.",
    "",
    "  BUG 2  All pads forced to layer 0 — bottom-side SMD pads get an instant via,",
    "         flooding the board with unnecessary layer-change overhead.",
    "",
    "  BUG 3  Expansion budget 100x too low (40K vs 4M default) + via cost 6x too",
    "         cheap (0.5mm vs 3.0mm), causing routes to abort early and jump layers freely.",
]
sy = 230
for s in summary:
    p1.insert_text((46, sy), s, fontsize=8.2, color=TEXT, fontname=FONT)
    sy += 11

# severity legend
p1.insert_text((40, 340), "SEVERITY LEGEND", fontsize=8, color=SUBTEXT, fontname=FONT_BOLD)
for label, col, xo in [("P0 — Critical (breaks routing)", RED, 0),
                        ("P1 — High (quality / throughput)", ORANGE, 200),
                        ("P2 — Medium (polish)", SUBTEXT, 400)]:
    p1.draw_rect(fitz.Rect(40 + xo, 350, 54 + xo, 362), color=None, fill=col)
    p1.insert_text((58 + xo, 361), label, fontsize=8, color=TEXT, fontname=FONT)

# screenshot callouts
p1.insert_text((40, 390), "EVIDENCE FROM SCREENSHOTS", fontsize=9,
               color=ACCENT, fontname=FONT_BOLD)
ss_items = [
    ("04:09:43", "Close-up: traces collide at random angles, no layer discipline — via cost too low"),
    ("04:10:28", "Mid-zoom: fan pattern emanating from single hub point — star topology confirmed"),
    ("04:10:46", "Full board: every ratsnest line converges to starburst centre — MST missing"),
    ("pcb_layout.png", "Separate board (RP2040 synth): same starburst radiating from one pad"),
]
sy = 408
for ts, desc in ss_items:
    p1.draw_rect(fitz.Rect(40, sy - 1, 108, sy + 9), color=None, fill=(0.2, 0.2, 0.3))
    p1.insert_text((42, sy + 8), ts, fontsize=7, color=YELLOW, fontname=FONT_MONO)
    p1.insert_text((114, sy + 8), desc, fontsize=8, color=TEXT, fontname=FONT)
    sy += 16

p1.insert_text((40, H - 30), "Page 1 of 3  —  Design Studio AutoRoute Analysis  —  CONFIDENTIAL",
               fontsize=7, color=SUBTEXT, fontname=FONT)

# ── PAGE 2: Bug Cards ─────────────────────────────────────────────────────────
p2 = new_page(doc)
y2 = 30
p2.insert_text((40, y2), "ROOT CAUSE DETAILS", fontsize=14,
               color=ACCENT, fontname=FONT_BOLD)
y2 += 22

bugs = [
    {
        "num": "B1",
        "title": "Star Topology — Missing Minimum Spanning Tree",
        "ref": "CoreBridge.cpp:151-153",
        "sev": RED,
        "desc": [
            "Routing strategy: for every net, route pad[0] → pad[1], pad[0] → pad[2], ..., pad[0] → pad[N].",
            "This means all N-1 connections share one endpoint. Optimal strategy is MST: connect the",
            "closest pair, then the next closest unreached pad, producing a chain/tree not a star.",
            "Impact: ~40% excess wire length, massive congestion at hub pad, the starburst pattern.",
        ],
        "code": [
            "// CURRENT (broken)",
            "auto& start = pts[0];",
            "for (int i = 1; i < pts.size(); ++i) { route(start, pts[i]); }",
        ],
        "fix": [
            "Build MST with Prim's algorithm on pad positions before routing.",
            "Route each MST edge instead of hub-spoke edges.",
            "~40 lines in CoreBridge.cpp — no changes to router.cpp needed.",
        ],
    },
    {
        "num": "B2",
        "title": "All Pads Hardcoded to Layer 0",
        "ref": "CoreBridge.cpp:133",
        "sev": RED,
        "desc": [
            "Every pad (SMD top, SMD bottom, through-hole, BGA) is assigned layer 0 regardless",
            "of which side the component is placed on. The router then inserts a via immediately",
            "on departure for every bottom-side pad, creating a via at every SMD pad on L7.",
        ],
        "code": [
            "// CURRENT (broken)",
            "netPads[pad.netId].append({{px, py}, 0});  // layer always 0",
        ],
        "fix": [
            "int padLayer = (fp.side == 1) ? (model->copperLayers - 1) : 0;",
            "netPads[pad.netId].append({{px, py}, padLayer});",
        ],
    },
    {
        "num": "B3",
        "title": "Expansion Budget 100x Too Low + Via Cost 6x Too Cheap",
        "ref": "CoreBridge.cpp:170-171",
        "sev": RED,
        "desc": [
            "maxExpansions=40,000 vs router.h default of 4,000,000. On a dense 8-layer board,",
            "A* exhausts this immediately and skips the net, leaving ratsnest lines unrouted.",
            "viaCostMm=0.5 vs default 3.0: a via costs less than 2 diagonal moves so the router",
            "changes layers every few steps, creating chaotic multi-layer crossing.",
        ],
        "code": [
            "// CURRENT (broken)",
            "req.viaCostMm   = 0.5;     // should be 3.0",
            "req.maxExpansions = 40000; // should be 500000+",
        ],
        "fix": [
            "req.viaCostMm     = 3.0;",
            "req.maxExpansions = 500000;  // or 2M for power nets",
        ],
    },
    {
        "num": "B4",
        "title": "Grid Too Coarse — 250µm vs Industry 25µm",
        "ref": "CoreBridge.cpp:165",
        "sev": ORANGE,
        "desc": [
            "At 0.25mm grid, QFN/TQFP pads at 0.5mm pitch have only 1 grid point between them.",
            "The router cannot thread between adjacent pads so it routes over them or fails.",
            "KiCad default is 25µm. Even 50µm would give 5 grid points between QFN pads.",
        ],
        "code": [
            "// CURRENT",
            "req.gridStep = (int64_t)(0.25 * NM); // 250 µm",
        ],
        "fix": [
            "req.gridStep = (int64_t)(0.05 * NM);  // 50µm — 5x finer",
            "Optional: two-pass (100µm rough + 25µm refine) for speed.",
        ],
    },
    {
        "num": "B5",
        "title": "No Layer Direction Preference (Burstein Model)",
        "ref": "router.cpp:135",
        "sev": ORANGE,
        "desc": [
            "All 8 directions allowed on every layer. Professional routers assign preferred",
            "directions: even layers = horizontal, odd layers = vertical. Diagonal moves dominate",
            "because octile heuristic favours them, producing 45° spaghetti cross-layer routing.",
        ],
        "code": [
            "// CURRENT — all dirs on all layers",
            "const Vec2 dirs[8] = {{g,0},{-g,0},{0,g},{0,-g},{g,g},{g,-g},{-g,g},{-g,-g}};",
        ],
        "fix": [
            "Pass preferred axis into isBlocked. Score diagonal moves 2x higher on",
            "non-preferred layers. Diagonals become last resort, not first choice.",
        ],
    },
    {
        "num": "B6",
        "title": "No Rip-Up and Reroute / No Net Ordering",
        "ref": "CoreBridge.cpp:138",
        "sev": SUBTEXT,
        "desc": [
            "Nets routed in QMap order (ascending net ID = arbitrary). First nets take direct",
            "paths; later nets detour. Failed nets are silently skipped with no retry.",
            "Professional routers: order by criticality, then rip blocking traces and retry.",
        ],
        "code": [],
        "fix": [
            "Pre-sort nets: power nets first, then by MST length (shortest first).",
            "Two-pass: route all, collect failures, rip 1 blocking trace per failure, retry.",
        ],
    },
]

for bug in bugs:
    result = rule_card(p2, bug["num"], bug["title"], bug["ref"], bug["sev"],
                       bug["desc"], bug["code"], bug["fix"], y2)
    if result is None:
        # overflow — shouldn't happen with current content but handle gracefully
        break
    y2 = result

p2.insert_text((40, H - 30), "Page 2 of 3  —  Design Studio AutoRoute Analysis  —  CONFIDENTIAL",
               fontsize=7, color=SUBTEXT, fontname=FONT)

# ── PAGE 3: Fix Plan + Quick Win ──────────────────────────────────────────────
p3 = new_page(doc)
y3 = 30
p3.insert_text((40, y3), "FIX PRIORITY PLAN", fontsize=14,
               color=ACCENT, fontname=FONT_BOLD)
y3 += 22

table_rows = [
    ("P0", "MST topology (Prim's)",        "~40% shorter wire, kills starburst",  "CoreBridge.cpp:138-153"),
    ("P0", "Fix pad layer assignment",      "Eliminates spurious via-per-pad",      "CoreBridge.cpp:133"),
    ("P0", "Expansion=500K, viaCost=3.0",  "Routes actually complete",             "CoreBridge.cpp:170-171"),
    ("P1", "Grid 250µm → 50µm",            "Threads fine-pitch QFN/BGA pads",      "CoreBridge.cpp:165"),
    ("P1", "Layer direction preference",   "Structured H/V routing per layer",     "router.cpp:135"),
    ("P2", "Net ordering by criticality",  "Power nets take best paths",           "CoreBridge.cpp:138"),
    ("P2", "Rip-up and reroute (2-pass)",  "Fewer unrouted nets",                  "CoreBridge.cpp new fn"),
    ("P3", "Differential pair mode",       "SI compliance on USB/DDR4",            "router.h/cpp new mode"),
]
y3 = priority_table(p3, table_rows, y3)

y3 += 10
p3.insert_text((40, y3), "IMMEDIATE 4-LINE QUICK WIN", fontsize=11,
               color=GREEN, fontname=FONT_BOLD)
y3 += 16
p3.insert_text((40, y3),
    "These four lines in CoreBridge.cpp alone will produce visibly better routing before",
    fontsize=8.5, color=TEXT, fontname=FONT)
y3 += 12
p3.insert_text((40, y3),
    "touching MST or grid changes. Apply first to prove the direction, then tackle B1.",
    fontsize=8.5, color=TEXT, fontname=FONT)
y3 += 16

y3 = code_block(p3, [
    "// CoreBridge.cpp — four immediate fixes",
    "",
    "// Fix B2: correct pad layer",
    "int padLayer = (fp.side == 1) ? (model->copperLayers - 1) : 0;",
    "netPads[pad.netId].append({{px, py}, padLayer});   // was hardcoded 0",
    "",
    "// Fix B3a: restore via penalty",
    "req.viaCostMm     = 3.0;    // was 0.5 — 6x too cheap",
    "",
    "// Fix B3b: enough budget to actually finish routes",
    "req.maxExpansions = 500000; // was 40000 — 100x too low",
    "",
    "// Fix B4: finer grid",
    "req.gridStep      = (int64_t)(0.05 * NM);  // was 0.25mm — 5x too coarse",
], y3)

y3 += 6
p3.insert_text((40, y3), "MST ALGORITHM SKETCH (B1 fix)", fontsize=11,
               color=ACCENT, fontname=FONT_BOLD)
y3 += 14

y3 = code_block(p3, [
    "// Replace the star loop in CoreBridge::autoRoute() with Prim's MST:",
    "",
    "// Build MST edge list from pad positions",
    "auto mstEdges = [](const QVector<QPair<QPointF,int>>& pts) {",
    "    // Prim's: start from pts[0], greedily add closest unvisited pad",
    "    std::vector<bool> inMST(pts.size(), false);",
    "    std::vector<std::pair<int,int>> edges;",
    "    inMST[0] = true;",
    "    for (int step = 1; step < (int)pts.size(); ++step) {",
    "        double best = 1e18; int bi = -1, bj = -1;",
    "        for (int i = 0; i < pts.size(); ++i) {",
    "            if (!inMST[i]) continue;",
    "            for (int j = 0; j < pts.size(); ++j) {",
    "                if (inMST[j]) continue;",
    "                double dx = pts[i].first.x()-pts[j].first.x();",
    "                double dy = pts[i].first.y()-pts[j].first.y();",
    "                double d  = dx*dx + dy*dy;",
    "                if (d < best) { best=d; bi=i; bj=j; }",
    "            }",
    "        }",
    "        inMST[bj] = true;",
    "        edges.push_back({bi, bj});",
    "    }",
    "    return edges;",
    "};",
    "",
    "// Route each MST edge instead of hub-spoke",
    "for (auto [i, j] : mstEdges(pts)) {",
    "    route(pts[i], pts[j], req);   // replaces route(start, pts[i])",
    "}",
], y3)

y3 += 8
p3.insert_text((40, y3), "COMPARISON: WHY ALTIUM / KICAD / FLUX DO BETTER", fontsize=11,
               color=ACCENT, fontname=FONT_BOLD)
y3 += 14

compare = [
    ("Routing topology",    "MST / Steiner tree",   "Star (hub-spoke)"),
    ("Grid resolution",     "25–50 µm",             "250 µm (10x coarser)"),
    ("Via cost",            "3.0–5.0 mm equiv.",    "0.5 mm (6x too cheap)"),
    ("Layer preference",    "H on even, V on odd",  "All 8 dirs on all layers"),
    ("Expansion budget",    "Unlimited / adaptive", "40,000 (100x too low)"),
    ("Rip-up & reroute",    "Yes (multi-pass)",     "No — failures skipped"),
    ("Net ordering",        "Power → diff → signal","Random (net ID order)"),
    ("Pad layer assignment","Per component side",   "Always L0"),
]

col_x = [40, 200, 360]
col_heads = ["Category", "Altium / KiCad / Flux", "Design Studio (current)"]
row_h = 14
p3.draw_rect(fitz.Rect(36, y3, W - 36, y3 + row_h), color=None, fill=ACCENT)
for i, h in enumerate(col_heads):
    p3.insert_text((col_x[i] + 3, y3 + 10), h, fontsize=8,
                   color=(0.05, 0.05, 0.08), fontname=FONT_BOLD)
y3 += row_h

for ri, row in enumerate(compare):
    fill = (0.11, 0.11, 0.17) if ri % 2 == 0 else CARD
    p3.draw_rect(fitz.Rect(36, y3, W - 36, y3 + row_h), color=None, fill=fill)
    p3.insert_text((col_x[0] + 3, y3 + 10), row[0], fontsize=7.5,
                   color=SUBTEXT, fontname=FONT)
    p3.insert_text((col_x[1] + 3, y3 + 10), row[1], fontsize=7.5,
                   color=GREEN, fontname=FONT)
    p3.insert_text((col_x[2] + 3, y3 + 10), row[2], fontsize=7.5,
                   color=RED, fontname=FONT)
    y3 += row_h

p3.insert_text((40, H - 30), "Page 3 of 3  —  Design Studio AutoRoute Analysis  —  CONFIDENTIAL",
               fontsize=7, color=SUBTEXT, fontname=FONT)

# ── Save ──────────────────────────────────────────────────────────────────────
doc.save(OUTPUT)
doc.close()
print(f"Saved: {OUTPUT}")
