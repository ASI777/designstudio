# ~6000-TOPS AI Inference Carrier — Reference BOM (2026 parts)

**Architecture: a 3× NVIDIA Jetson AGX Thor carrier/baseboard.** No single
board-mountable chip delivers 6000 TOPS — at that level the compute silicon ships
on a module (Blackwell GPU + 128 GB + 2.5D packaging). The buildable path is a
**carrier that aggregates three production Thor modules** (~2070 FP4 TFLOPS each →
**~6000 FP4 TOPS**) and provides the power, PCIe Gen5 fabric, networking, storage
and cooling.

> **TOPS basis:** Thor's ~2070 figure is **FP4**. For **INT8**, budget ~1000/module
> → ~6 modules for 6000 INT8 TOPS. This BOM assumes the FP4 interpretation (3 modules).

> **This is a hard board.** ~400 W of distributed power, PCIe Gen5 (32 GT/s) signal
> integrity, a 699-pin connector breakout ×3, and serious thermal. It is exactly the
> kind of board your SI/PI suite exists for: Gen5 channel eyes + via back-drill, the
> spatial PDN + decap ranking on the high-current module rails, and a ~20–24-layer
> stackup (the layer planner will confirm). Recommend it as a *team/serious* project,
> not a first board.

Legend: ✅ datasheet/source verified this session · 🔒 NVIDIA secure doc (developer
account login may be required) · ⚠ representative — confirm current MPN/stock/footprint.

---

## 1. Compute modules (the ~6000 TOPS)

| Qty | Part | Mfr | Function | Key specs | Datasheet |
|---|---|---|---|---|---|
| **3** | Jetson **AGX Thor** module | NVIDIA | AI compute module | ~2070 FP4 / 1035 FP8 TFLOPS, 14-core Neoverse CPU, 128 GB, 4×25 GbE, 7–20 V in, 699-pin B2B, 40–130 W | [Datasheet DS-11945-001 v1.5 🔒](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/thor/docs/jetson-thor-series-modules-datasheet_ds-11945-001.pdf) · [Product page ✅](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-thor/) |
| 3 | **699-pin B2B connector** (carrier side) | per NVIDIA SCL | Module-to-carrier interface | 65×11, 699-pin; exact MPN in NVIDIA Supported Components List | [Thor Design Guide DG-12084-001 🔒](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/thor/docs/jetson_thor_series_modules_designguide.pdf) |

Design references: [Thor Carrier Board Spec SP-12533-001 (Nov 2025) 🔒](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/thor/docs/jetson-thor-developer-kit-carrier-board-specification.pdf) · [GA announcement ✅](https://nvidianews.nvidia.com/news/nvidia-blackwell-powered-jetson-thor-now-available-accelerating-the-age-of-general-robotics)

---

## 2. PCIe Gen5 fabric (inter-module + NVMe + NIC fan-out)

| Qty | Part | Mfr | Function | Key specs | Datasheet |
|---|---|---|---|---|---|
| 1 | Switchtec **PM50052** | Microchip | PCIe Gen5 switch | 52-lane, 32 GT/s, NTB for multi-host (module↔module) | [Microchip Switchtec page ✅](https://www.microchip.com/en-us/products/interface-and-connectivity/pcie/pcie-switches) · [PM50052 (c-payne) ✅](https://c-payne.com/products/pcie-gen5-mcio-switch-52-lane-microchip-switchtec-pm50052) |
| *alt* | **PEX89072** | Broadcom | PCIe Gen5 switch | 72-lane, 36-port ExpressFabric | [Broadcom PEX89072 ✅](https://www.broadcom.com/products/pcie-switches-retimers/expressfabric/gen5/pex89072) |
| 4–8 | PCIe Gen5 **retimer** | Microchip/Broadcom/Astera | recover Gen5 across the board | needed on longer Gen5 runs (your eye sim flags where) | [Microchip PCIe page ✅](https://www.microchip.com/en-us/products/interface-and-connectivity/pcie/pcie-switches) |
| 1 | Gen5 clock buffer/jitter atten. | Renesas/Skyworks ⚠ | 100 MHz HCSL fan-out | low phase-noise, per-port | ⚠ confirm MPN |

> The NTB-capable switch lets the three Thor hosts share NVMe / NICs and message
> each other over PCIe. These Gen5 lanes are the channels your link simulator
> extracts; expect **back-drill** recommendations on the through-vias.

---

## 3. Networking (aggregate the modules' 25 GbE)

| Qty | Part | Mfr | Function | Notes | Datasheet |
|---|---|---|---|---|---|
| 1 | Ethernet switch (Marvell Prestera / Broadcom) ⚠ | Marvell/Broadcom | aggregate 12×25 GbE → 100 GbE uplinks | each Thor exposes 4×25 GbE | ⚠ NDA/registered docs |
| 2–4 | QSFP28/QSFP56 cage + connector | Amphenol/TE ⚠ | 100 GbE uplinks | with retimers if needed | ⚠ confirm |

*(Optional — only if you need external network fabric; otherwise route the 25 GbE to RJ45/SFP per module.)*

---

## 4. Power (≈400 W distributed)

| Qty | Part | Mfr | Function | Key specs | Datasheet |
|---|---|---|---|---|---|
| 1 | 12 V (or 48 V) DC input + ORing | — | board input within Thor's 7–20 V | size for ≥3×130 W + overhead (~500 W) | — |
| 3 | **eFuse / hot-swap** (e.g., TI TPS25985 / LM5066I) ⚠ | TI | per-module inrush + protection | high-current, telemetry | [TI TPS25985 ⚠](https://www.ti.com/product/TPS25985) |
| n | Multiphase buck VRM (e.g., MPS MP29816 / Renesas) ⚠ | MPS/Renesas | carrier rails (3.3 V, 1.8 V, 1.0 V) | switch/retimer/NIC supplies | ⚠ confirm |
| n | Bulk + ceramic decoupling | — | PDN | per the spatial-PDN + decap-ranking output | — |
| 1 | Power-sequencer / BMC MCU | TI/Microchip ⚠ | sequencing + telemetry | I²C/PMBus | ⚠ confirm |

---

## 5. Storage, clocks, support

| Qty | Part | Mfr | Function | Notes | Datasheet |
|---|---|---|---|---|---|
| 1–4 | NVMe SSD (M.2 2280 / U.2) behind the switch | Micron/Samsung ⚠ | model + dataset storage | PCIe Gen4/5 x4 | ⚠ confirm |
| 1 | Reference crystal/oscillator | Abracon/SiTime ⚠ | board + PCIe ref | low-jitter | ⚠ confirm |
| n | ESD / TVS arrays | TI/onsemi/Nexperia ⚠ | I/O protection | on USB/Eth/exposed I/O | [TI ESD122 ✅](https://www.ti.com/product/ESD122/part-details/ESD122DMXR) |
| 1 | Management connectors | — | JTAG, UART, fan, RGMII | — | — |

---

## 6. Thermal (do not skip)

Three Thor modules at full tilt ≈ **400 W**. Plan **active or liquid cooling**:
per-module heatsink/cold plate to NVIDIA's mechanical interface, high-static-pressure
fans or a cold-plate loop, and temperature/fan control on the BMC. The mechanical
keep-outs and module thermal interface are in the Thor Design Guide.

---

## Honest feasibility notes

- **You are not laying out the AI silicon.** The 6000 TOPS lives inside the Thor
  modules (GPU + HBM-class memory + 2.5D packaging). Your PCB is the carrier.
- **Difficulty:** Gen5 SI + ~400 W power distribution + 699-pin breakout ×3 +
  thermal is a genuine high-end carrier — a multi-month effort. Start by getting
  **one** Thor module's carrier working (power, PCIe, networking, boot), then
  replicate to three behind the switch.
- **If you want a single finished accelerator instead of designing this**, a
  Blackwell-class data-center GPU card (or the Thor dev kit) gives you the compute
  off-the-shelf — but that's buying a card, not designing a PCB.
- **Smaller realistic edge target:** if ~6000 TOPS isn't a hard requirement, edge
  M.2 accelerators (Hailo-10 ~40 TOPS, MemryX, DEGIRUM Orca) make a clean, truly
  hand-designable multi-accelerator board in the tens-to-low-hundreds of TOPS.

---

## Sources

- [NVIDIA Jetson Thor product page](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-thor/) · [GA announcement](https://nvidianews.nvidia.com/news/nvidia-blackwell-powered-jetson-thor-now-available-accelerating-the-age-of-general-robotics)
- [Jetson Thor Series Modules Datasheet DS-11945-001 v1.5 (June 2026)](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/thor/docs/jetson-thor-series-modules-datasheet_ds-11945-001.pdf) · [Design Guide DG-12084-001 (July 2025)](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/thor/docs/jetson_thor_series_modules_designguide.pdf) · [Carrier Board Spec SP-12533-001 (Nov 2025)](https://developer.nvidia.com/downloads/assets/embedded/secure/jetson/thor/docs/jetson-thor-developer-kit-carrier-board-specification.pdf)
- [Microchip Switchtec PCIe Gen5 switches](https://www.microchip.com/en-us/products/interface-and-connectivity/pcie/pcie-switches) · [PFX Gen5 family announcement](https://embeddedcomputing.com/technology/processing/compute-modules/microchip-releases-switchtec-pfx-pcie-50-family-worlds-first-pcle-50-solutions) · [Switchtec PM50052 (c-payne)](https://c-payne.com/products/pcie-gen5-mcio-switch-52-lane-microchip-switchtec-pm50052)
- [Broadcom PEX89072 PCIe Gen5 switch](https://www.broadcom.com/products/pcie-switches-retimers/expressfabric/gen5/pex89072)
- [Hailo-8 / Hailo edge accelerators](https://hailo.ai/products/ai-accelerators/hailo-8-m2-ai-acceleration-module/) (smaller-target alternative)
