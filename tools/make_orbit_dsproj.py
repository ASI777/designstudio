#!/usr/bin/env python3
"""Generate ORBIT.dsproj — the ORBIT smart-ring netlist as a DesignStudio project
so the schematic tab (SchematicView) renders it (symbols auto-generate from these
footprints; pins are labelled/coloured by the net each pad connects to)."""
import json, os

# ── nets ───────────────────────────────────────────────────────────────────────
NETS = ["GND","+3V0","VBAT","VLED","SDA","SCL","SDA_T","SCL_T","SCK","MOSI","MISO",
        "CSB","RF","RFMATCH","RESET","PPG_INT","ACC_INT1","PD","COIL1","COIL2",
        "RECT","SW","FB","XC1","XC2","XL1","XL2","DCC","DEC4","ISET","COMM","TS","LED_DRV"]
NID = {n:i for i,n in enumerate(NETS)}

# ── components: (ref, lib, h3d_mm, [(pad_name, net_name), ...]) ─────────────────
COMPS = [
 ("U1","nRF52832-QFAA",0.9,[("VDD","+3V0"),("VSS","GND"),("DEC4","DEC4"),("DCC","DCC"),
   ("XC1","XC1"),("XC2","XC2"),("XL1","XL1"),("XL2","XL2"),("ANT","RF"),("SDA","SDA"),
   ("SCL","SCL"),("SCK","SCK"),("MOSI","MOSI"),("MISO","MISO"),("CSB","CSB"),("RESET","RESET")]),
 ("U2","MAX86141",0.6,[("VDD_A","+3V0"),("VDD_D","+3V0"),("VLED","VLED"),("GND","GND"),
   ("SCLK","SCK"),("MOSI","MOSI"),("MISO","MISO"),("CSB","CSB"),("INT","PPG_INT"),
   ("LED1","LED_DRV"),("PD","PD")]),
 ("U3","LIS2DW12",0.7,[("VDD","+3V0"),("GND","GND"),("SDA","SDA"),("SCL","SCL"),("INT1","ACC_INT1")]),
 ("U4","TMP117",0.6,[("V+","+3V0"),("GND","GND"),("SDA","SDA_T"),("SCL","SCL_T")]),
 ("U5","BQ51050B",0.7,[("AC1","COIL1"),("AC2","COIL2"),("RECT","RECT"),("OUT","VBAT"),
   ("ISET","ISET"),("COMM","COMM"),("TS","TS"),("GND","GND")]),
 ("U6","TPS62840",0.5,[("VIN","VBAT"),("SW","SW"),("FB","FB"),("EN","VBAT"),("GND","GND")]),
 ("DS1","LED-G/R/IR",0.4,[("A","VLED"),("K","LED_DRV")]),
 ("PD1","VEMD8080",0.4,[("A","PD"),("K","GND")]),
 ("AE1","2450AT18",0.5,[("RF","RFMATCH")]),
 ("L1","RxCoil-18mm",0.6,[("1","COIL1"),("2","COIL2")]),
 ("BT1","LiPo-22mAh",1.1,[("+","VBAT"),("-","GND")]),
 # ── decoupling / support passives ──
 ("C1","C-100n",0.5,[("1","+3V0"),("2","GND")]),("C2","C-100n",0.5,[("1","+3V0"),("2","GND")]),
 ("C3","C-100n",0.5,[("1","+3V0"),("2","GND")]),("C4","C-100n",0.5,[("1","+3V0"),("2","GND")]),
 ("C5","C-1u-DEC4",0.5,[("1","DEC4"),("2","GND")]),
 ("Y1","XTAL-32MHz",0.6,[("1","XC1"),("2","XC2")]),
 ("CL1","C-12p",0.4,[("1","XC1"),("2","GND")]),("CL2","C-12p",0.4,[("1","XC2"),("2","GND")]),
 ("Y2","XTAL-32.768k",0.6,[("1","XL1"),("2","XL2")]),
 ("L4","L-10u-DCDC",0.8,[("1","DCC"),("2","+3V0")]),
 ("L2","L-2u2",0.8,[("1","SW"),("2","+3V0")]),
 ("Cin","C-10u",0.5,[("1","VBAT"),("2","GND")]),("Cout","C-10u",0.5,[("1","+3V0"),("2","GND")]),
 ("R1","R-fbtop",0.4,[("1","+3V0"),("2","FB")]),("R2","R-fbbot",0.4,[("1","FB"),("2","GND")]),
 ("L3","L-RFmatch",0.5,[("1","RF"),("2","RFMATCH")]),
 ("Cm1","C-RF1",0.4,[("1","RF"),("2","GND")]),("Cm2","C-RF2",0.4,[("1","RFMATCH"),("2","GND")]),
 ("R3","R-10k-reset",0.4,[("1","RESET"),("2","+3V0")]),
 ("Rp1","R-4k7",0.4,[("1","SDA"),("2","+3V0")]),("Rp2","R-4k7",0.4,[("1","SCL"),("2","+3V0")]),
 ("C10","C-1u-VANA",0.5,[("1","+3V0"),("2","GND")]),("C11","C-100n-VDIG",0.5,[("1","+3V0"),("2","GND")]),
 ("C12","C-10u-VLED",0.5,[("1","VLED"),("2","GND")]),
 ("C13","C-100n",0.5,[("1","+3V0"),("2","GND")]),("C14","C-100n",0.5,[("1","+3V0"),("2","GND")]),
 ("Riset","R-ISET",0.4,[("1","ISET"),("2","GND")]),("Crect","C-10u-RECT",0.5,[("1","RECT"),("2","GND")]),
 ("Ccomm","C-COMM",0.4,[("1","COMM"),("2","GND")]),("Rts","R-TS",0.4,[("1","TS"),("2","GND")]),
 # ── 0-ohm option links isolating U4 (temp) from the I2C bus ──
 ("R20","R-0R",0.4,[("1","SDA"),("2","SDA_T")]),("R21","R-0R",0.4,[("1","SCL"),("2","SCL_T")]),
]

def footprint(ref, lib, h3d, pins):
    pads=[]
    for i,(name,net) in enumerate(pins):
        pads.append({"name":name,"x_mm":round(i*1.0-len(pins)*0.5,3),"y_mm":0.0,
                     "w_mm":0.5,"h_mm":0.3,"net":NID[net],"th":False,"shape":"rect"})
    return {"ref":ref,"lib":lib,"x_mm":0.0,"y_mm":0.0,"rot_deg":0,"side":0,"h3d_mm":h3d,
            "body_w_mm":max(2.0,len(pins)*1.0),"body_h_mm":2.0,"pads":pads,"regions":[]}

doc={
 "version":2,"board_width_mm":24.0,"board_height_mm":8.0,"grid_mm":1.27,"copper_layers":6,
 "dielectric_er":4.4,"dielectric_h_mm":0.1,"loss_tangent":0.02,"copper_t_mm":0.035,
 "nets":None,
 "net_table":[{"id":NID[n],"name":n,"class":0} for n in NETS],
 "net_classes":[{"id":0,"name":"Default","clearance_mm":0.1,"trace_width_mm":0.15,
   "via_diameter_mm":0.3,"via_drill_mm":0.15,"z0_ohm":0,"zdiff_ohm":0}],
 "footprints":[footprint(*c) for c in COMPS],
 "traces":[],"vias":[],
}

out=os.path.join(os.path.dirname(__file__),"..","ORBIT.dsproj")
json.dump(doc, open(out,"w"), indent=2)
print("wrote %s"%os.path.abspath(out))
print("components: %d  nets: %d  pads: %d"%(len(COMPS),len(NETS),sum(len(c[3]) for c in COMPS)))
