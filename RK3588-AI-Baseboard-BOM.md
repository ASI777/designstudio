# RK3588 Arm AI Baseboard — Reference BOM

A buildable, high-end reference bill of materials for an **Arm AI baseboard** built
around the **Rockchip RK3588** (8-core, triple-core 6 TOPS NPU, LPDDR5, PCIe 3.0,
HDMI 2.1, dual 2.5GbE). This is the canonical "small SoC/AI baseboard" target —
self-contained, public docs, and a genuine SI/PI workout for the simulation suite.

> **Recommended stackup: 14 layers** (the layer planner lands here for a ~1100-pin
> SoC + LPDDR5 + several rails). Plane cadence: a reference plane adjacent to every
> signal layer; LPDDR5 byte lanes on inner striplines; PCIe/USB3/HDMI diff pairs on
> low-loss layers (Megtron-6 or equivalent). The SI/PI-critical nets your engines
> will sign off: **LPDDR5** (DDR timing + crosstalk), **PCIe Gen3** and **USB3 / HDMI 2.1**
> (channel eyes, via stubs → back-drill), and the **VDD_CPU / VDD_GPU / VDD_NPU / VDDQ**
> rails (spatial PDN + decap placement).

Legend: ✅ = datasheet link verified this session · ⚠ = representative part, confirm
current MPN/availability/footprint before committing.

---

## 1. Core silicon

| Block | MPN | Mfr | Function | Key specs | Datasheet |
|---|---|---|---|---|---|
| **SoC** | RK3588 | Rockchip | Application processor | 4×A76 + 4×A55, Mali-G610 MP4, 3-core NPU 6 TOPS (INT4/8/16, FP16), 8K VPU, FCBGA | [RK3588 Datasheet V1.8 ✅](https://www.boardcon.com/download/Rockchip_RK3588_Datasheet_V1.8-20240821.pdf) · [Brief ✅](https://www.rock-chips.com/uploads/pdf/2022.8.26/192/RK3588%20Brief%20Datasheet.pdf) |
| **Main PMIC** | RK806-1 | Rockchip | Primary power-management IC | 10 bucks + 6 LDO, 2.7–5.5 V in, I²C/SPI, sequencing, QFN-68 | [RK806 Datasheet V1.3 ✅](https://www.cool-pi.com/wp-content/uploads/2025/04/Rockchip-RK806-Datasheet-V1.3-20231019.pdf) |
| **Companion PMIC** | RK806-2 | Rockchip | Second PMIC (dual-PMIC coop) | Powers the second set of SoC rails; master/slave coop with RK806-1 | [same as RK806 ✅](https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/2401261533_Rockchip-RK806-1_C5156483.pdf) |

The RK3588 reference power design (NanoPC-T6, Radxa ROCK 5B+, RK3588 EVB) uses **two
RK806 PMICs** to cover all SoC domains. High-current compute rails (VDD_CPU_BIG,
VDD_GPU, VDD_NPU, VDD_LOGIC) are the ones your PDN engine should target first.

---

## 2. Memory

| Block | MPN | Mfr | Function | Key specs | Datasheet |
|---|---|---|---|---|---|
| **LPDDR5 (16 GB)** | MT62F4G32D8DV-026 | Micron | DRAM, x32, dual-die | LPDDR5-5500/6400, 200-ball FBGA; pair two for the RK3588's 4×16-bit (64-bit) bus | [Micron part page ✅](https://www.micron.com/products/memory/dram-components/lpddr5/part-catalog/part-detail/mt62f4g32d8dv-026-wt-b) · [LPDDR5 family DS ✅](https://www.mouser.com/datasheet/2/671/Micron_05092023_315b_441b_y42m_sdp_ddp_qdp_8dp_lpd-3175540.pdf) |
| **LPDDR5 (8 GB) alt** | MT62F2G32D4DS-026 | Micron | DRAM, x32 | smaller-capacity option, same family/footprint class | [Micron part page ✅](https://www.micron.com/products/memory/lpddr-components/lpddr5/part-catalog/part-detail/mt62f2g32d4ds-026-wt-b) |
| **eMMC 5.1 (32 GB)** | EMMC32G-TX29-8AD01 | Kingston | Boot/OS flash | HS400, FBGA-153, VCC 3.3 V / VCCQ 1.8 V | [Kingston DS ✅](https://www1.futureelectronics.com/doc/Kingston/EMMC32G-TX29-8AD01.pdf) |
| **eMMC alt** | KLMBG4GEUF-B04Q | Samsung | Boot/OS flash | eMMC 5.1, 32 GB, FBGA-153 | [Samsung part page ✅](https://semiconductor.samsung.com/estorage/emmc/emmc-5-1/klmbg4geuf-b04q/) |

> RK3588 supports LPDDR4/4X/**5**. For a 64-bit bus at 16 GB, use **two** x32 LPDDR5
> packages (point-to-point per channel pair). These nets feed the **DDR timing
> engine** (per-byte-lane setup/hold) and **crosstalk-aware eye**.

---

## 3. High-speed connectivity

| Block | MPN | Mfr | Function | Key specs | Datasheet |
|---|---|---|---|---|---|
| **2.5 GbE ×1–2** | RTL8125BG | Realtek | PCIe→2.5GbE MAC+PHY | PCIe 2.1 x1, 10/100/1000/2500, single chip | [Realtek product page ✅](https://www.realtek.com/en/products/connected-media-ics/item/rtl8125bg-s-cg) · [DS v1.5 ✅](https://file.elecfans.com/web2/M00/44/D8/poYBAGKHVriAHnfWADAT6T6hjVk715.pdf) |
| **USB-C redriver** | TUSB1044-RNQ | Texas Instruments | USB3.2 / DP Alt-Mode 10 Gbps linear redriver | 4-lane reversible, RX EQ for ISI, 3.3 V | [TI DS ✅](https://www.ti.com/lit/ds/symlink/tusb1044.pdf) |
| **USB-C PD** | FUSB302B | onsemi | USB Type-C / PD controller | CC logic, PD 2.0/3.0, I²C | [onsemi FUSB302B DS ⚠](https://www.onsemi.com/pdf/datasheet/fusb302b-d.pdf) |
| **M.2 M-key conn.** | Amphenol M.2 (PCIe) series | Amphenol CS | NVMe SSD socket (PCIe x4) | 67-pos, 0.50 mm pitch, 2280 | [Amphenol M.2 PCIe DS ✅](https://cdn.amphenol-cs.com/media/wysiwyg/files/documentation/datasheet/ssio/ssio_pcie_m2.pdf) |
| **Wi-Fi 6 + BT 5** | AP6275P | Ampak / AzureWave | Wi-Fi 6 2T2R + BT 5 module | SDIO/PCIe + UART; used on RK3588 EVB | [RK3588S EVB guide ⚠](https://www.scribd.com/document/798961936/Rockchip-RK3588S-EVB-User-Guide-V1-1-EN) |

> HDMI 2.1 TX is **integrated in the RK3588** — no external transmitter needed, only
> ESD protection (section 5). PCIe Gen3 lanes route to the M.2 socket and/or the
> RTL8125; these are the channels your link simulator extracts and the advisor will
> recommend **back-drilling** if a through-via stub closes the eye.

---

## 4. Power tree (board rails feeding the PMICs / peripherals)

| Block | MPN | Mfr | Function | Key specs | Datasheet |
|---|---|---|---|---|---|
| **5 V / 3.3 V buck** | SY8205 | Silergy | Synchronous buck (rail gen) | 5 A, 4.5–18 V in, adjustable | [Silergy SY8205 ⚠](https://www.silergy.com) |
| **High-current point-of-load alt** | MP8759 / MP2143 | MPS | Synchronous buck | up to 8 A POL options for compute rails | [MPS product pages ⚠](https://www.monolithicpower.com) |
| **Load switches** | TPS22918 | Texas Instruments | Rail sequencing / enable | 2 A, low Rds(on) | [TI TPS22918 ⚠](https://www.ti.com/product/TPS22918) |
| **DC input** | 12 V barrel jack **or** USB-C PD | — | Board power in | 12 V/≥3 A recommended for full NPU+GPU load | — |

> The RK806 dual-PMIC covers the SoC core/logic/DDR rails; discrete bucks generate
> the board-level 5 V / 3.3 V / 1.8 V and any high-current peripheral POLs. All of
> these are nets the **electro-thermal IR-drop** check (PowerIntegrity) will sweep.

---

## 5. Clocks & protection

| Block | MPN | Mfr | Function | Key specs | Datasheet |
|---|---|---|---|---|---|
| **Main crystal** | ABM8-24.000MHZ-B2 | Abracon | 24 MHz SoC reference | ±20 ppm, 18 pF, 3.2×2.5 mm | [Abracon ABM8 ⚠](https://abracon.com/Resonators/ABM8.pdf) |
| **RTC crystal** | ABS07-32.768KHZ | Abracon | 32.768 kHz RTC | tuning-fork, 12.5 pF | [Abracon ABS07 ⚠](https://abracon.com/Resonators/abs07.pdf) |
| **HDMI ESD** | TPD4E02B04 | Texas Instruments | HDMI/USB-C ESD array | 4-ch, low cap, ±8 kV contact | [TI DS ✅](https://www.ti.com/lit/ds/symlink/tpd4e02b04.pdf) |
| **HDMI ESD alt** | HDMI2C4-5F2 | STMicroelectronics | HDMI ESD + level | 4-line, clamp | [ST DS ✅](https://www.st.com/resource/en/datasheet/hdmi2c4-5f2.pdf) |
| **USB3 / GbE ESD** | ESD122 | Texas Instruments | 2-ch ESD for 10 Gbps lines | bidirectional, ~0.25 pF | [ESD122 DS ✅](https://www.ti.com/product/ESD122/part-details/ESD122DMXR) |

---

## 6. Connectors & I/O (representative — confirm footprints)

| Block | Part | Mfr | Notes |
|---|---|---|---|
| USB-C receptacle | GCT USB4500 series ⚠ | GCT | data + PD; pair with FUSB302B + TUSB1044 |
| USB-A 3.0 ×2 | dual stacked Type-A ⚠ | Amphenol/Molex | from native USB3 / hub |
| RJ45 + magnetics (2.5G) | Pulse / Halo integrated-magnetics jack ⚠ | Pulse/Halo | one per RTL8125BG |
| HDMI 2.1 | Type-A HDMI receptacle ⚠ | Amphenol | from RK3588 HDMI TX + ESD |
| 40-pin GPIO header | 2×20 2.54 mm ⚠ | generic | expansion |
| M.2 E-key (Wi-Fi) | Amphenol M.2 E-key ⚠ | Amphenol CS | if using a card instead of the AP6275P module |

---

## 7. Passives (the SI/PI-relevant set)

| Class | Typical | Notes |
|---|---|---|
| **LPDDR5 decoupling** | 100 nF + 1 µF 0201/0402, X5R/X7R | tight to each DRAM/ SoC DDR rail; the PDN antinode target |
| **Core-rail bulk + HF** | 22–47 µF bulk + 100 nF/1 µF arrays | per VDD_CPU/GPU/NPU; decap ranking comes from the PDN engine |
| **Series / AC-coupling** | PCIe/USB3 AC-coupling caps (e.g., 100–220 nF) | on TX pairs per spec |
| **Crystal load caps** | per crystal C_L (≈18 pF / 12.5 pF) | match to the chosen crystal |
| **Ferrite beads** | analog/PLL rail isolation | e.g., 600 Ω @100 MHz |

> Decap *values and counts* are exactly what the **spatial PDN solver + decap-ranking
> advisor** will refine once you place them and run the analysis — start with the
> reference quantities above and let the tool flag the ones to move/add.

---

## Sources

- [Rockchip RK3588 Datasheet V1.8](https://www.boardcon.com/download/Rockchip_RK3588_Datasheet_V1.8-20240821.pdf) · [RK3588 Brief Datasheet](https://www.rock-chips.com/uploads/pdf/2022.8.26/192/RK3588%20Brief%20Datasheet.pdf)
- [Rockchip RK806 PMIC Datasheet V1.3](https://www.cool-pi.com/wp-content/uploads/2025/04/Rockchip-RK806-Datasheet-V1.3-20231019.pdf) · [RK806 (LCSC mirror)](https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/2401261533_Rockchip-RK806-1_C5156483.pdf)
- [Micron MT62F4G32D8DV LPDDR5 part page](https://www.micron.com/products/memory/dram-components/lpddr5/part-catalog/part-detail/mt62f4g32d8dv-026-wt-b) · [Micron LPDDR5 family datasheet](https://www.mouser.com/datasheet/2/671/Micron_05092023_315b_441b_y42m_sdp_ddp_qdp_8dp_lpd-3175540.pdf)
- [Kingston eMMC 32 GB datasheet](https://www1.futureelectronics.com/doc/Kingston/EMMC32G-TX29-8AD01.pdf) · [Samsung KLMBG4GEUF-B04Q](https://semiconductor.samsung.com/estorage/emmc/emmc-5-1/klmbg4geuf-b04q/)
- [Realtek RTL8125BG product page](https://www.realtek.com/en/products/connected-media-ics/item/rtl8125bg-s-cg) · [RTL8125BG datasheet v1.5](https://file.elecfans.com/web2/M00/44/D8/poYBAGKHVriAHnfWADAT6T6hjVk715.pdf)
- [TI TUSB1044 datasheet](https://www.ti.com/lit/ds/symlink/tusb1044.pdf)
- [onsemi FUSB302B datasheet](https://www.onsemi.com/pdf/datasheet/fusb302b-d.pdf)
- [Amphenol M.2 PCIe connector datasheet](https://cdn.amphenol-cs.com/media/wysiwyg/files/documentation/datasheet/ssio/ssio_pcie_m2.pdf)
- [TI TPD4E02B04 ESD datasheet](https://www.ti.com/lit/ds/symlink/tpd4e02b04.pdf) · [ST HDMI2C4-5F2](https://www.st.com/resource/en/datasheet/hdmi2c4-5f2.pdf) · [TI ESD122](https://www.ti.com/product/ESD122/part-details/ESD122DMXR)
- [Rockchip RK3588S EVB User Guide V1.1](https://www.scribd.com/document/798961936/Rockchip-RK3588S-EVB-User-Guide-V1-1-EN) (AP6275P Wi-Fi, FUSB302 Type-C reference)
- Reference power/SI designs: [NanoPC-T6 schematic](https://wiki.friendlyelec.com/wiki/images/9/97/NanoPC-T6_2301_SCH.PDF) · [Radxa ROCK 5B+ schematic](https://dl.radxa.com/rock5/5b+/docs/hw/radxa_rock5bp_v1.2_schematic.pdf)
