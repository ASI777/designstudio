#include "ApplicationConcept.h"

#include "ProjectModel.h"

#include <QJsonArray>
#include <QJsonObject>
#include <QRegularExpression>
#include <algorithm>
#include <cmath>

namespace designstudio {
namespace {

ProjFootprint provisionalFootprint(
    const QString& ref, const QString& name,
    double x, double y, double width, double height,
    const QVector<QPair<QString, int>>& pins,
    const QString& group, bool throughHole = false,
    const QString& edgeAnchor = {}, double thermalPowerW = 0.0,
    double thermalClearanceMm = 0.0)
{
    ProjFootprint fp;
    fp.ref = ref;
    fp.lib = name;
    fp.mpn = QStringLiteral("PROVISIONAL-%1").arg(name.toUpper());
    fp.manufacturer = QStringLiteral("Evidence pending");
    fp.datasheetEvidence = QJsonObject{
        {QStringLiteral("status"), QStringLiteral("incomplete")},
        {QStringLiteral("reason"), QStringLiteral("local concept placeholder")}};
    fp.x_mm = x;
    fp.y_mm = y;
    fp.bodyW_mm = width;
    fp.bodyH_mm = height;
    fp.h3d_mm = throughHole ? 12.0 : std::max(2.0, thermalPowerW > 1.0 ? 4.0 : 2.0);
    fp.functionalGroup = group;
    fp.placementLocked = throughHole && !edgeAnchor.isEmpty();
    fp.edgeAnchor = edgeAnchor;
    fp.thermalPowerW = thermalPowerW;
    fp.thermalClearanceMm = thermalClearanceMm;
    fp.courtyard = {{-width / 2 - 0.5, -height / 2 - 0.5},
                    { width / 2 + 0.5, -height / 2 - 0.5},
                    { width / 2 + 0.5,  height / 2 + 0.5},
                    {-width / 2 - 0.5,  height / 2 + 0.5}};
    for (int index = 0; index < pins.size(); ++index) {
        ProjPad pad;
        pad.name = pins[index].first;
        pad.netId = pins[index].second;
        pad.x_mm = pins.size() == 1 ? 0.0
                 : -width * 0.36 + (width * 0.72 * index / (pins.size() - 1));
        pad.y_mm = index % 2 == 0 ? -height * 0.35 : height * 0.35;
        pad.w_mm = throughHole ? 1.8 : 0.9;
        pad.h_mm = throughHole ? 1.8 : 0.9;
        pad.throughHole = throughHole;
        pad.drillMm = throughHole ? 0.9 : 0.0;
        pad.shape = throughHole ? QStringLiteral("circle") : QStringLiteral("roundrect");
        fp.pads.append(pad);
    }
    return fp;
}

void resetElectronics(ProjectModel& model)
{
    model.nets.clear();
    model.netClasses.clear();
    ProjNetClass signal;
    signal.id = 0;
    signal.name = QStringLiteral("Default");
    signal.clearanceMm = 0.20;
    signal.traceWidthMm = 0.25;
    signal.viaDiaMm = 0.60;
    signal.viaDrillMm = 0.30;
    model.netClasses.append(signal);
    model.footprints.clear();
    model.symbols.clear();
    model.schWires.clear();
    model.traces.clear();
    model.vias.clear();
    model.copperZones.clear();
    model.unresolvedComponents = {};
    model.placementState = {};
}

int addNet(ProjectModel& model, const QString& name, double voltage = 0.0,
           double current = 0.0, int classId = 0)
{
    const int id = model.createNet(name);
    for (auto& item : model.nets) {
        if (item.id != id) continue;
        item.nominalVoltageV = voltage;
        item.requiredCurrentA = current;
        item.classId = classId;
        break;
    }
    return id;
}

void addEvidenceGates(ProjectModel& model)
{
    for (const auto& fp : model.footprints) {
        model.unresolvedComponents.append(QJsonObject{
            {QStringLiteral("ref"), fp.ref},
            {QStringLiteral("mpn"), fp.mpn},
            {QStringLiteral("status"), QStringLiteral("incomplete")},
            {QStringLiteral("required_evidence"), QJsonArray{
                QStringLiteral("manufacturer datasheet"),
                QStringLiteral("pin map"),
                QStringLiteral("land pattern"),
                QStringLiteral("3D model")}}
        });
    }
}

QString finishConcept(ProjectModel& model)
{
    // Fixed interface connectors are generated from their courtyard extents.
    // This final pre-save check is deliberately allowed to move a newly-created
    // lock; later placement stages preserve the now-legal location.
    const QString placementError = model.legalizeFootprints(false);
    if (!placementError.isEmpty()) return placementError;
    addEvidenceGates(model);
    model.generateSymbolsFromFootprints();
    model.verificationRequirements.requirePowerIntegrity = true;
    model.verificationRequirements.requireThermal = true;
    return {};
}

ApplicationConceptResult buildConditionMonitor(
    ProjectModel& model, const ApplicationConceptRequest& request)
{
    resetElectronics(model);
    const double nominal = std::clamp((request.inputMinV + request.inputMaxV) / 2.0, 5.0, 60.0);
    const int vin = addNet(model, QStringLiteral("VIN_FIELD"), nominal, 0.35);
    const int vinProtected = addNet(model, QStringLiteral("VIN_PROTECTED"), nominal, 0.30);
    const int fieldGround = addNet(model, QStringLiteral("GND_FIELD"));
    const int five = addNet(model, QStringLiteral("5V_ISO"), 5.0, 0.25);
    const int ground = addNet(model, QStringLiteral("GND_ISO"));
    const int three = addNet(model, QStringLiteral("3V3"), 3.3, 0.16);
    const int rsA = addNet(model, QStringLiteral("RS485_A"));
    const int rsB = addNet(model, QStringLiteral("RS485_B"));
    const int tx = addNet(model, QStringLiteral("UART_TX"));
    const int rx = addNet(model, QStringLiteral("UART_RX"));
    const int sck = addNet(model, QStringLiteral("SPI_SCK"));
    const int mosi = addNet(model, QStringLiteral("SPI_MOSI"));
    const int miso = addNet(model, QStringLiteral("SPI_MISO"));
    const int accelCs = addNet(model, QStringLiteral("ACCEL_CS"));
    const int temp = addNet(model, QStringLiteral("TEMP_SENSE"));
    const int led = addNet(model, QStringLiteral("STATUS_LED"));
    const int shield = addNet(model, QStringLiteral("CHASSIS_SHIELD"));

    model.footprints = {
        provisionalFootprint(QStringLiteral("J1"), QStringLiteral("M12_A_5PIN"), 6.5, 30, 12, 12,
            {{QStringLiteral("VIN"), vin}, {QStringLiteral("GND"), fieldGround},
             {QStringLiteral("A"), rsA}, {QStringLiteral("B"), rsB},
             {QStringLiteral("SHIELD"), shield}}, QStringLiteral("external-io"), true,
             QStringLiteral("left")),
        provisionalFootprint(QStringLiteral("F1"), QStringLiteral("INPUT_FUSE"), 19, 12, 5, 3,
            {{QStringLiteral("VIN"), vin}, {QStringLiteral("VOUT"), vinProtected}}, QStringLiteral("power")),
        provisionalFootprint(QStringLiteral("D1"), QStringLiteral("TVS_33V"), 19, 21, 5, 3,
            {{QStringLiteral("K"), vinProtected}, {QStringLiteral("A"), fieldGround}}, QStringLiteral("protection")),
        provisionalFootprint(QStringLiteral("U1"), QStringLiteral("ISOLATED_DC_DC"), 35, 18, 15, 10,
            {{QStringLiteral("VIN"), vinProtected}, {QStringLiteral("GND_IN"), fieldGround},
             {QStringLiteral("5V_OUT"), five}, {QStringLiteral("GND_OUT"), ground}}, QStringLiteral("power"),
             false, {}, 0.8, 2.0),
        provisionalFootprint(QStringLiteral("U2"), QStringLiteral("LDO_3V3"), 51, 12, 6, 5,
            {{QStringLiteral("VIN"), five}, {QStringLiteral("GND"), ground},
             {QStringLiteral("VOUT"), three}}, QStringLiteral("power"), false, {}, 0.3, 1.0),
        provisionalFootprint(QStringLiteral("U3"), QStringLiteral("INDUSTRIAL_MCU"), 58, 31, 12, 12,
            {{QStringLiteral("VDD"), three}, {QStringLiteral("GND"), ground},
             {QStringLiteral("SCK"), sck}, {QStringLiteral("MOSI"), mosi},
             {QStringLiteral("MISO"), miso}, {QStringLiteral("TX"), tx},
             {QStringLiteral("RX"), rx}, {QStringLiteral("LED"), led}}, QStringLiteral("control"),
             false, {}, 0.4, 1.0),
        provisionalFootprint(QStringLiteral("U4"), QStringLiteral("3AXIS_ACCEL"), 77, 18, 5, 5,
            {{QStringLiteral("VDD"), three}, {QStringLiteral("GND"), ground},
             {QStringLiteral("SCK"), sck}, {QStringLiteral("MOSI"), mosi},
             {QStringLiteral("MISO"), miso}, {QStringLiteral("CS"), accelCs}}, QStringLiteral("sensor")),
        provisionalFootprint(QStringLiteral("U5"), QStringLiteral("RTD_INTERFACE"), 78, 38, 7, 7,
            {{QStringLiteral("VDD"), three}, {QStringLiteral("GND"), ground},
             {QStringLiteral("SENSE"), temp}, {QStringLiteral("SCK"), sck},
             {QStringLiteral("MISO"), miso}, {QStringLiteral("MOSI"), mosi}}, QStringLiteral("sensor")),
        provisionalFootprint(QStringLiteral("U6"), QStringLiteral("ISOLATED_RS485"), 35, 44, 13, 8,
            {{QStringLiteral("VCC"), three}, {QStringLiteral("GND"), ground},
             {QStringLiteral("TX"), tx}, {QStringLiteral("RX"), rx},
             {QStringLiteral("A"), rsA}, {QStringLiteral("B"), rsB}}, QStringLiteral("communications")),
        provisionalFootprint(QStringLiteral("LED1"), QStringLiteral("STATUS_LED"), 93, 12, 3, 3,
            {{QStringLiteral("A"), led}, {QStringLiteral("K"), ground}}, QStringLiteral("user-interface")),
        provisionalFootprint(QStringLiteral("J2"), QStringLiteral("TEMP_PROBE"), 96, 47, 7, 7,
            {{QStringLiteral("SENSE"), temp}, {QStringLiteral("GND"), ground}}, QStringLiteral("external-io"), true,
             QStringLiteral("right"))
    };
    if (const QString error = finishConcept(model); !error.isEmpty())
        return {false, request.family, {}, error, 0};
    return {true, request.family,
            QStringLiteral("isolated condition monitor with vibration, temperature, and RS-485"), {}, 0};
}

ApplicationConceptResult buildBuckConverter(
    ProjectModel& model, const ApplicationConceptRequest& request)
{
    resetElectronics(model);
    ProjNetClass power;
    power.id = 1; power.name = QStringLiteral("Converter power");
    power.clearanceMm = 0.35; power.traceWidthMm = 1.5;
    power.viaDiaMm = 1.0; power.viaDrillMm = 0.5;
    power.allowedLayers = {0, std::max(0, model.copperLayers - 1)};
    model.netClasses.append(power);
    const double nominal = (request.inputMinV + request.inputMaxV) / 2.0;
    const int vin = addNet(model, QStringLiteral("VIN"), nominal, 3.0, 1);
    const int ground = addNet(model, QStringLiteral("GND"));
    const int vout = addNet(model, QStringLiteral("VOUT"), 5.0, 3.0, 1);
    const int switchNode = addNet(model, QStringLiteral("SW"), nominal, 3.0, 1);
    const int gateHigh = addNet(model, QStringLiteral("GATE_H"));
    const int gateLow = addNet(model, QStringLiteral("GATE_L"));
    const int boot = addNet(model, QStringLiteral("BOOT"));
    const int feedback = addNet(model, QStringLiteral("FB"));
    model.footprints = {
        provisionalFootprint(QStringLiteral("J1"), QStringLiteral("DC_INPUT"), 4.5, 15, 8, 8,
            {{QStringLiteral("VIN"), vin}, {QStringLiteral("GND"), ground}},
            QStringLiteral("external-io"), true, QStringLiteral("left")),
        provisionalFootprint(QStringLiteral("F1"), QStringLiteral("INPUT_FUSE"), 16, 15, 5, 3,
            {{QStringLiteral("IN"), vin}, {QStringLiteral("OUT"), vin}}, QStringLiteral("protection")),
        provisionalFootprint(QStringLiteral("U1"), QStringLiteral("SYNCHRONOUS_BUCK_CONTROLLER"), 36, 28, 9, 8,
            {{QStringLiteral("VIN"), vin}, {QStringLiteral("GND"), ground},
             {QStringLiteral("GH"), gateHigh}, {QStringLiteral("GL"), gateLow},
             {QStringLiteral("BOOT"), boot}, {QStringLiteral("FB"), feedback}},
            QStringLiteral("control"), false, {}, 0.6, 1.0),
        provisionalFootprint(QStringLiteral("Q1"), QStringLiteral("HIGH_SIDE_MOSFET"), 52, 20, 7, 7,
            {{QStringLiteral("D"), vin}, {QStringLiteral("S"), switchNode},
             {QStringLiteral("G"), gateHigh}}, QStringLiteral("power"), false, {}, 1.2, 1.5),
        provisionalFootprint(QStringLiteral("Q2"), QStringLiteral("LOW_SIDE_MOSFET"), 52, 38, 7, 7,
            {{QStringLiteral("D"), switchNode}, {QStringLiteral("S"), ground},
             {QStringLiteral("G"), gateLow}}, QStringLiteral("power"), false, {}, 1.2, 1.5),
        provisionalFootprint(QStringLiteral("L1"), QStringLiteral("POWER_INDUCTOR"), 68, 28, 10, 10,
            {{QStringLiteral("1"), switchNode}, {QStringLiteral("2"), vout}},
            QStringLiteral("power"), false, {}, 1.0, 1.0),
        provisionalFootprint(QStringLiteral("C1"), QStringLiteral("INPUT_CAPACITOR_BANK"), 27, 12, 8, 6,
            {{QStringLiteral("+"), vin}, {QStringLiteral("-"), ground}}, QStringLiteral("power")),
        provisionalFootprint(QStringLiteral("C2"), QStringLiteral("OUTPUT_CAPACITOR_BANK"), 82, 28, 8, 6,
            {{QStringLiteral("+"), vout}, {QStringLiteral("-"), ground}}, QStringLiteral("power")),
        provisionalFootprint(QStringLiteral("R1"), QStringLiteral("FEEDBACK_DIVIDER"), 70, 43, 5, 3,
            {{QStringLiteral("TOP"), vout}, {QStringLiteral("MID"), feedback},
             {QStringLiteral("BOT"), ground}}, QStringLiteral("feedback")),
        provisionalFootprint(QStringLiteral("J2"), QStringLiteral("DC_OUTPUT"), 95.5, 28, 8, 8,
            {{QStringLiteral("VOUT"), vout}, {QStringLiteral("GND"), ground}},
            QStringLiteral("external-io"), true, QStringLiteral("right"))
    };
    if (const QString error = finishConcept(model); !error.isEmpty())
        return {false, request.family, {}, error, 0};
    return {true, request.family,
        QStringLiteral("synchronous buck power stage with protection, external MOSFETs, feedback, and output filtering"), {}, 0};
}

ApplicationConceptResult buildPrecisionAcquisition(
    ProjectModel& model, const ApplicationConceptRequest& request)
{
    resetElectronics(model);
    const double nominal = (request.inputMinV + request.inputMaxV) / 2.0;
    const int vin = addNet(model, QStringLiteral("VIN"), nominal, 0.25);
    const int ground = addNet(model, QStringLiteral("AGND"));
    const int three = addNet(model, QStringLiteral("3V3_ANALOG"), 3.3, 0.15);
    const int sensorP = addNet(model, QStringLiteral("SENSOR_P"));
    const int sensorN = addNet(model, QStringLiteral("SENSOR_N"));
    const int protectedP = addNet(model, QStringLiteral("AIN_P"));
    const int protectedN = addNet(model, QStringLiteral("AIN_N"));
    const int ref = addNet(model, QStringLiteral("VREF"), 2.5, 0.02);
    const int sck = addNet(model, QStringLiteral("SPI_SCK"));
    const int mosi = addNet(model, QStringLiteral("SPI_MOSI"));
    const int miso = addNet(model, QStringLiteral("SPI_MISO"));
    const int cs = addNet(model, QStringLiteral("ADC_CS"));
    const int dataP = addNet(model, QStringLiteral("DATA_P"));
    const int dataN = addNet(model, QStringLiteral("DATA_N"));
    model.footprints = {
        provisionalFootprint(QStringLiteral("J1"), QStringLiteral("SENSOR_INPUT"), 4.5, 30, 8, 10,
            {{QStringLiteral("P"), sensorP}, {QStringLiteral("N"), sensorN},
             {QStringLiteral("GND"), ground}}, QStringLiteral("external-io"), true, QStringLiteral("left")),
        provisionalFootprint(QStringLiteral("U1"), QStringLiteral("INPUT_PROTECTION_FILTER"), 18, 30, 9, 8,
            {{QStringLiteral("INP"), sensorP}, {QStringLiteral("INN"), sensorN},
             {QStringLiteral("OUTP"), protectedP}, {QStringLiteral("OUTN"), protectedN},
             {QStringLiteral("GND"), ground}}, QStringLiteral("analog-input")),
        provisionalFootprint(QStringLiteral("U2"), QStringLiteral("PRECISION_PGA"), 34, 30, 9, 8,
            {{QStringLiteral("INP"), protectedP}, {QStringLiteral("INN"), protectedN},
             {QStringLiteral("OUTP"), protectedP}, {QStringLiteral("OUTN"), protectedN},
             {QStringLiteral("VDD"), three}, {QStringLiteral("GND"), ground}},
            QStringLiteral("analog-front-end"), false, {}, 0.25, 1.0),
        provisionalFootprint(QStringLiteral("U3"), QStringLiteral("PRECISION_ADC"), 51, 30, 10, 10,
            {{QStringLiteral("AINP"), protectedP}, {QStringLiteral("AINN"), protectedN},
             {QStringLiteral("VREF"), ref}, {QStringLiteral("VDD"), three},
             {QStringLiteral("GND"), ground}, {QStringLiteral("SCK"), sck},
             {QStringLiteral("MOSI"), mosi}, {QStringLiteral("MISO"), miso},
             {QStringLiteral("CS"), cs}}, QStringLiteral("conversion"), false, {}, 0.35, 1.0),
        provisionalFootprint(QStringLiteral("U4"), QStringLiteral("LOW_NOISE_REFERENCE"), 49, 13, 7, 6,
            {{QStringLiteral("VIN"), three}, {QStringLiteral("GND"), ground},
             {QStringLiteral("VREF"), ref}}, QStringLiteral("reference"), false, {}, 0.2, 1.0),
        provisionalFootprint(QStringLiteral("U5"), QStringLiteral("ACQUISITION_MCU"), 69, 30, 11, 11,
            {{QStringLiteral("VDD"), three}, {QStringLiteral("GND"), ground},
             {QStringLiteral("SCK"), sck}, {QStringLiteral("MOSI"), mosi},
             {QStringLiteral("MISO"), miso}, {QStringLiteral("CS"), cs},
             {QStringLiteral("DP"), dataP}, {QStringLiteral("DN"), dataN}}, QStringLiteral("control")),
        provisionalFootprint(QStringLiteral("U6"), QStringLiteral("LOW_NOISE_REGULATOR"), 25, 13, 8, 6,
            {{QStringLiteral("VIN"), vin}, {QStringLiteral("GND"), ground},
             {QStringLiteral("OUT"), three}}, QStringLiteral("power"), false, {}, 0.35, 1.0),
        provisionalFootprint(QStringLiteral("J2"), QStringLiteral("DATA_CONNECTOR"), 95.5, 30, 8, 10,
            {{QStringLiteral("DP"), dataP}, {QStringLiteral("DN"), dataN},
             {QStringLiteral("GND"), ground}}, QStringLiteral("external-io"), true, QStringLiteral("right"))
    };
    if (const QString error = finishConcept(model); !error.isEmpty())
        return {false, request.family, {}, error, 0};
    return {true, request.family,
        QStringLiteral("protected differential input, low-noise gain, precision reference and ADC, and digital readout"), {}, 0};
}

ApplicationConceptResult buildWirelessSensor(
    ProjectModel& model, const ApplicationConceptRequest& request)
{
    resetElectronics(model);
    const double nominal = std::clamp((request.inputMinV + request.inputMaxV) / 2.0, 3.0, 12.0);
    const int source = addNet(model, QStringLiteral("POWER_SOURCE"), nominal, 0.12);
    const int ground = addNet(model, QStringLiteral("GND"));
    const int three = addNet(model, QStringLiteral("3V3"), 3.3, 0.10);
    const int sda = addNet(model, QStringLiteral("I2C_SDA"));
    const int scl = addNet(model, QStringLiteral("I2C_SCL"));
    const int rf = addNet(model, QStringLiteral("RF_2G4"));
    const int swd = addNet(model, QStringLiteral("SWDIO"));
    const int swc = addNet(model, QStringLiteral("SWCLK"));
    model.footprints = {
        provisionalFootprint(QStringLiteral("J1"), QStringLiteral("POWER_INPUT"), 4.5, 14, 8, 8,
            {{QStringLiteral("PWR"), source}, {QStringLiteral("GND"), ground}},
            QStringLiteral("external-io"), true, QStringLiteral("left")),
        provisionalFootprint(QStringLiteral("U1"), QStringLiteral("ULTRA_LOW_POWER_REGULATOR"), 20, 14, 8, 6,
            {{QStringLiteral("VIN"), source}, {QStringLiteral("GND"), ground},
             {QStringLiteral("OUT"), three}}, QStringLiteral("power"), false, {}, 0.2, 0.8),
        provisionalFootprint(QStringLiteral("U2"), QStringLiteral("2G4_RADIO_MCU"), 48, 30, 12, 12,
            {{QStringLiteral("VDD"), three}, {QStringLiteral("GND"), ground},
             {QStringLiteral("SDA"), sda}, {QStringLiteral("SCL"), scl},
             {QStringLiteral("RF"), rf}, {QStringLiteral("SWDIO"), swd},
             {QStringLiteral("SWCLK"), swc}}, QStringLiteral("radio-control"), false, {}, 0.25, 1.0),
        provisionalFootprint(QStringLiteral("U3"), QStringLiteral("SENSOR"), 28, 34, 7, 7,
            {{QStringLiteral("VDD"), three}, {QStringLiteral("GND"), ground},
             {QStringLiteral("SDA"), sda}, {QStringLiteral("SCL"), scl}}, QStringLiteral("sensor")),
        provisionalFootprint(QStringLiteral("FL1"), QStringLiteral("RF_MATCH_FILTER"), 66, 30, 7, 5,
            {{QStringLiteral("IN"), rf}, {QStringLiteral("OUT"), rf},
             {QStringLiteral("GND"), ground}}, QStringLiteral("rf")),
        provisionalFootprint(QStringLiteral("AE1"), QStringLiteral("PCB_ANTENNA"), 88, 30, 16, 10,
            {{QStringLiteral("RF"), rf}, {QStringLiteral("GND"), ground}},
            QStringLiteral("rf-edge"), false),
        provisionalFootprint(QStringLiteral("J2"), QStringLiteral("PROGRAM_HEADER"), 20, 49, 8, 7,
            {{QStringLiteral("VDD"), three}, {QStringLiteral("GND"), ground},
             {QStringLiteral("SWDIO"), swd}, {QStringLiteral("SWCLK"), swc}},
            QStringLiteral("service"), true, QStringLiteral("bottom"))
    };
    if (const QString error = finishConcept(model); !error.isEmpty())
        return {false, request.family, {}, error, 0};
    return {true, request.family,
        QStringLiteral("low-power sensor node with 2.4 GHz radio, matching network, antenna, and programming interface"), {}, 0};
}

ApplicationConceptResult buildHighSpeedInterface(
    ProjectModel& model, const ApplicationConceptRequest& request)
{
    resetElectronics(model);
    const double nominal = (request.inputMinV + request.inputMaxV) / 2.0;
    const int vbus = addNet(model, QStringLiteral("VBUS"), nominal, 0.9);
    const int ground = addNet(model, QStringLiteral("GND"));
    const int three = addNet(model, QStringLiteral("3V3"), 3.3, 0.6);
    const int usbDp = addNet(model, QStringLiteral("USB_DP"));
    const int usbDm = addNet(model, QStringLiteral("USB_DM"));
    const int txP = addNet(model, QStringLiteral("SS_TX_P"));
    const int txN = addNet(model, QStringLiteral("SS_TX_N"));
    const int rxP = addNet(model, QStringLiteral("SS_RX_P"));
    const int rxN = addNet(model, QStringLiteral("SS_RX_N"));
    const int mdi0P = addNet(model, QStringLiteral("MDI0_P"));
    const int mdi0N = addNet(model, QStringLiteral("MDI0_N"));
    const int mdi1P = addNet(model, QStringLiteral("MDI1_P"));
    const int mdi1N = addNet(model, QStringLiteral("MDI1_N"));
    const int xtalP = addNet(model, QStringLiteral("XTAL_P"));
    const int xtalN = addNet(model, QStringLiteral("XTAL_N"));
    model.footprints = {
        provisionalFootprint(QStringLiteral("J1"), QStringLiteral("USB_C_RECEPTACLE"), 5.5, 30, 10, 12,
            {{QStringLiteral("VBUS"), vbus}, {QStringLiteral("GND"), ground},
             {QStringLiteral("DP"), usbDp}, {QStringLiteral("DM"), usbDm},
             {QStringLiteral("TXP"), txP}, {QStringLiteral("TXN"), txN},
             {QStringLiteral("RXP"), rxP}, {QStringLiteral("RXN"), rxN}},
            QStringLiteral("external-io"), true, QStringLiteral("left")),
        provisionalFootprint(QStringLiteral("D1"), QStringLiteral("HIGH_SPEED_ESD_ARRAY"), 20, 30, 7, 7,
            {{QStringLiteral("DP"), usbDp}, {QStringLiteral("DM"), usbDm},
             {QStringLiteral("TXP"), txP}, {QStringLiteral("TXN"), txN},
             {QStringLiteral("RXP"), rxP}, {QStringLiteral("RXN"), rxN},
             {QStringLiteral("GND"), ground}}, QStringLiteral("protection")),
        provisionalFootprint(QStringLiteral("U1"), QStringLiteral("USB_GIGABIT_BRIDGE"), 45, 30, 14, 14,
            {{QStringLiteral("VDD"), three}, {QStringLiteral("GND"), ground},
             {QStringLiteral("DP"), usbDp}, {QStringLiteral("DM"), usbDm},
             {QStringLiteral("TXP"), txP}, {QStringLiteral("TXN"), txN},
             {QStringLiteral("RXP"), rxP}, {QStringLiteral("RXN"), rxN},
             {QStringLiteral("MDI0P"), mdi0P}, {QStringLiteral("MDI0N"), mdi0N},
             {QStringLiteral("MDI1P"), mdi1P}, {QStringLiteral("MDI1N"), mdi1N},
             {QStringLiteral("XTALP"), xtalP}, {QStringLiteral("XTALN"), xtalN}},
            QStringLiteral("high-speed"), false, {}, 0.8, 1.0),
        provisionalFootprint(QStringLiteral("Y1"), QStringLiteral("REFERENCE_CRYSTAL"), 46, 11, 7, 5,
            {{QStringLiteral("P"), xtalP}, {QStringLiteral("N"), xtalN},
             {QStringLiteral("GND"), ground}}, QStringLiteral("clock")),
        provisionalFootprint(QStringLiteral("U2"), QStringLiteral("3V3_REGULATOR"), 25, 11, 8, 6,
            {{QStringLiteral("VIN"), vbus}, {QStringLiteral("GND"), ground},
             {QStringLiteral("OUT"), three}}, QStringLiteral("power"), false, {}, 0.45, 1.0),
        provisionalFootprint(QStringLiteral("T1"), QStringLiteral("ETHERNET_MAGNETICS"), 68, 30, 12, 12,
            {{QStringLiteral("P0"), mdi0P}, {QStringLiteral("N0"), mdi0N},
             {QStringLiteral("P1"), mdi1P}, {QStringLiteral("N1"), mdi1N},
             {QStringLiteral("GND"), ground}}, QStringLiteral("ethernet")),
        provisionalFootprint(QStringLiteral("J2"), QStringLiteral("RJ45_CONNECTOR"), 94, 30, 11, 14,
            {{QStringLiteral("P0"), mdi0P}, {QStringLiteral("N0"), mdi0N},
             {QStringLiteral("P1"), mdi1P}, {QStringLiteral("N1"), mdi1N},
             {QStringLiteral("GND"), ground}}, QStringLiteral("external-io"), true,
             QStringLiteral("right"))
    };
    model.verificationRequirements.requireSignalIntegrity = true;
    if (const QString error = finishConcept(model); !error.isEmpty())
        return {false, request.family, {}, error, 0};
    return {true, request.family,
        QStringLiteral("protected USB and Gigabit-class bridge with reference clock, magnetics, and edge connectors"), {}, 0};
}

ApplicationConceptResult buildRoboticController(
    ProjectModel& model, const ApplicationConceptRequest& request)
{
    const int requestedAxes = std::clamp(request.axisCount, 1, 6);
    const bool distributed = requestedAxes > 3;
    // For a distributed robot this document is the central CAN/E-stop
    // coordinator. Each motor stage lives in its own immutable joint-board
    // configuration and must be independently sized by the control plane.
    const int axes = distributed ? 0 : requestedAxes;
    resetElectronics(model);

    ProjNetClass motorPower;
    motorPower.id = 1;
    motorPower.name = QStringLiteral("Motor power");
    motorPower.clearanceMm = 0.40;
    motorPower.traceWidthMm = 2.50;
    motorPower.viaDiaMm = 1.20;
    motorPower.viaDrillMm = 0.60;
    motorPower.allowedLayers = {0, std::max(0, model.copperLayers - 1)};
    model.netClasses.append(motorPower);

    const double nominal = std::clamp((request.inputMinV + request.inputMaxV) / 2.0, 12.0, 60.0);
    const double axisCurrent = std::clamp(request.axisCurrentA, 0.25, 10.0);
    const int vin = addNet(model, QStringLiteral("VIN_24V"), nominal, axisCurrent * requestedAxes, 1);
    const int protectedVin = addNet(model, QStringLiteral("VIN_PROTECTED"), nominal, axisCurrent * requestedAxes, 1);
    const int powerGround = addNet(model, QStringLiteral("GND_POWER"));
    const int five = addNet(model, QStringLiteral("5V"), 5.0, 0.8);
    const int three = addNet(model, QStringLiteral("3V3"), 3.3, 0.5);
    const int canH = addNet(model, QStringLiteral("CAN_H"));
    const int canL = addNet(model, QStringLiteral("CAN_L"));
    const int canTx = addNet(model, QStringLiteral("CAN_TX"));
    const int canRx = addNet(model, QStringLiteral("CAN_RX"));
    const int estopField = addNet(model, QStringLiteral("ESTOP_FIELD"));
    const int estopSafe = addNet(model, QStringLiteral("ESTOP_SAFE"));
    const int shield = addNet(model, QStringLiteral("CHASSIS_SHIELD"));

    QVector<QPair<QString, int>> mcuPins{
        {QStringLiteral("VDD"), three}, {QStringLiteral("GND"), powerGround},
        {QStringLiteral("CAN_TX"), canTx}, {QStringLiteral("CAN_RX"), canRx},
        {QStringLiteral("ESTOP"), estopSafe}};

    struct AxisNets {
        int pwmU, pwmV, pwmW, gateU, gateV, gateW;
        int phaseU, phaseV, phaseW, current, fault, encA, encB, encZ;
    };
    QVector<AxisNets> axisNets;
    for (int axis = 1; axis <= axes; ++axis) {
        const QString prefix = QStringLiteral("AX%1_").arg(axis);
        AxisNets n{
            addNet(model, prefix + QStringLiteral("PWM_U")),
            addNet(model, prefix + QStringLiteral("PWM_V")),
            addNet(model, prefix + QStringLiteral("PWM_W")),
            addNet(model, prefix + QStringLiteral("GATE_U")),
            addNet(model, prefix + QStringLiteral("GATE_V")),
            addNet(model, prefix + QStringLiteral("GATE_W")),
            addNet(model, prefix + QStringLiteral("PHASE_U"), nominal, axisCurrent, 1),
            addNet(model, prefix + QStringLiteral("PHASE_V"), nominal, axisCurrent, 1),
            addNet(model, prefix + QStringLiteral("PHASE_W"), nominal, axisCurrent, 1),
            addNet(model, prefix + QStringLiteral("CURRENT")),
            addNet(model, prefix + QStringLiteral("FAULT")),
            addNet(model, prefix + QStringLiteral("ENC_A")),
            addNet(model, prefix + QStringLiteral("ENC_B")),
            addNet(model, prefix + QStringLiteral("ENC_Z"))};
        axisNets.append(n);
        mcuPins += {{prefix + QStringLiteral("PWM_U"), n.pwmU},
                    {prefix + QStringLiteral("PWM_V"), n.pwmV},
                    {prefix + QStringLiteral("PWM_W"), n.pwmW},
                    {prefix + QStringLiteral("CURRENT"), n.current},
                    {prefix + QStringLiteral("FAULT"), n.fault},
                    {prefix + QStringLiteral("ENC_A"), n.encA},
                    {prefix + QStringLiteral("ENC_B"), n.encB},
                    {prefix + QStringLiteral("ENC_Z"), n.encZ}};
    }

    model.footprints = {
        provisionalFootprint(QStringLiteral("J1"), QStringLiteral("POWER_ESTOP_INPUT"), 5.5, 10, 10, 8,
            {{QStringLiteral("VIN"), vin}, {QStringLiteral("GND"), powerGround},
             {QStringLiteral("ESTOP"), estopField}, {QStringLiteral("SHIELD"), shield}},
             QStringLiteral("external-io"), true, QStringLiteral("left")),
        provisionalFootprint(QStringLiteral("J2"), QStringLiteral("CAN_CONNECTOR"), 4.5, 48, 8, 8,
            {{QStringLiteral("CAN_H"), canH}, {QStringLiteral("CAN_L"), canL},
             {QStringLiteral("GND"), powerGround}, {QStringLiteral("SHIELD"), shield}},
             QStringLiteral("external-io"), true, QStringLiteral("left")),
        provisionalFootprint(QStringLiteral("F1"), QStringLiteral("INPUT_FUSE"), 16, 10, 5, 3,
            {{QStringLiteral("VIN"), vin}, {QStringLiteral("VOUT"), protectedVin}}, QStringLiteral("power")),
        provisionalFootprint(QStringLiteral("D1"), QStringLiteral("TVS_33V"), 16, 17, 5, 3,
            {{QStringLiteral("K"), protectedVin}, {QStringLiteral("A"), powerGround}}, QStringLiteral("protection")),
        provisionalFootprint(QStringLiteral("U1"), QStringLiteral("BUCK_24V_TO_5V"), 28, 10, 10, 8,
            {{QStringLiteral("VIN"), protectedVin}, {QStringLiteral("GND"), powerGround},
             {QStringLiteral("5V"), five}}, QStringLiteral("power"), false, {}, 0.9, 1.5),
        provisionalFootprint(QStringLiteral("U2"), QStringLiteral("LDO_3V3"), 40, 10, 6, 5,
            {{QStringLiteral("VIN"), five}, {QStringLiteral("GND"), powerGround},
             {QStringLiteral("3V3"), three}}, QStringLiteral("power"), false, {}, 0.3, 1.0),
        provisionalFootprint(QStringLiteral("U3"), QStringLiteral("FOC_MCU"), 50, 30, 12, 12,
            mcuPins, QStringLiteral("control"), false, {}, 0.8, 1.5),
        provisionalFootprint(QStringLiteral("U4"), QStringLiteral("CAN_TRANSCEIVER"), 20, 48, 8, 6,
            {{QStringLiteral("VCC"), five}, {QStringLiteral("GND"), powerGround},
             {QStringLiteral("TX"), canTx}, {QStringLiteral("RX"), canRx},
             {QStringLiteral("CAN_H"), canH}, {QStringLiteral("CAN_L"), canL}},
             QStringLiteral("communications")),
        provisionalFootprint(QStringLiteral("U5"), QStringLiteral("ESTOP_SAFETY_INPUT"), 18, 29, 8, 6,
            {{QStringLiteral("FIELD"), estopField}, {QStringLiteral("SAFE"), estopSafe},
             {QStringLiteral("VCC"), three}, {QStringLiteral("GND"), powerGround}},
             QStringLiteral("safety"))
    };

    if (distributed) {
        for (int axis = 1; axis <= requestedAxes; ++axis) {
            model.footprints.append(provisionalFootprint(
                QStringLiteral("JL%1").arg(axis),
                QStringLiteral("ISOLATED_JOINT_LINK_AX%1").arg(axis),
                95.5, 5.0 + (axis - 1) * 10.0, 8, 7,
                {{QStringLiteral("BUS"), protectedVin},
                 {QStringLiteral("GND"), powerGround},
                 {QStringLiteral("CAN_H"), canH},
                 {QStringLiteral("CAN_L"), canL},
                 {QStringLiteral("ESTOP"), estopSafe}},
                QStringLiteral("joint-link-%1").arg(axis), true,
                QStringLiteral("right")));
        }
    }

    const QVector<double> driverX{64.0, 76.0, 88.0};
    const QVector<double> connectorY{10.0, 30.0, 50.0};
    const QVector<double> encoderX{50.0, 65.0, 80.0};
    for (int i = 0; i < axes; ++i) {
        const int axis = i + 1;
        const AxisNets& n = axisNets[i];
        model.footprints.append(provisionalFootprint(
            QStringLiteral("U%1").arg(6 + i), QStringLiteral("BLDC_GATE_DRIVER_AX%1").arg(axis),
            driverX[i], 18, 8, 8,
            {{QStringLiteral("VCC"), five}, {QStringLiteral("GND"), powerGround},
             {QStringLiteral("PWM_U"), n.pwmU}, {QStringLiteral("PWM_V"), n.pwmV},
             {QStringLiteral("PWM_W"), n.pwmW}, {QStringLiteral("GATE_U"), n.gateU},
             {QStringLiteral("GATE_V"), n.gateV}, {QStringLiteral("GATE_W"), n.gateW},
             {QStringLiteral("CURRENT"), n.current}, {QStringLiteral("FAULT"), n.fault}},
            QStringLiteral("axis-%1").arg(axis), false, {}, 0.5, 1.0));
        model.footprints.append(provisionalFootprint(
            QStringLiteral("Q%1").arg(axis), QStringLiteral("THREE_PHASE_STAGE_AX%1").arg(axis),
            driverX[i], 42, 10, 10,
            {{QStringLiteral("VIN"), protectedVin}, {QStringLiteral("GND"), powerGround},
             {QStringLiteral("GATE_U"), n.gateU}, {QStringLiteral("GATE_V"), n.gateV},
             {QStringLiteral("GATE_W"), n.gateW}, {QStringLiteral("PHASE_U"), n.phaseU},
             {QStringLiteral("PHASE_V"), n.phaseV}, {QStringLiteral("PHASE_W"), n.phaseW},
             {QStringLiteral("CURRENT"), n.current}},
            QStringLiteral("axis-%1").arg(axis), false, {}, 1.5, 1.5));
        model.footprints.append(provisionalFootprint(
            QStringLiteral("JM%1").arg(axis), QStringLiteral("MOTOR_AX%1").arg(axis),
            95.5, connectorY[i], 8, 8,
            {{QStringLiteral("U"), n.phaseU}, {QStringLiteral("V"), n.phaseV},
             {QStringLiteral("W"), n.phaseW}, {QStringLiteral("GND"), powerGround}},
            QStringLiteral("external-io"), true, QStringLiteral("right")));
        model.footprints.append(provisionalFootprint(
            QStringLiteral("JE%1").arg(axis), QStringLiteral("ENCODER_AX%1").arg(axis),
            encoderX[i], 3.5, 6, 6,
            {{QStringLiteral("5V"), five}, {QStringLiteral("GND"), powerGround},
             {QStringLiteral("A"), n.encA}, {QStringLiteral("B"), n.encB},
             {QStringLiteral("Z"), n.encZ}},
            QStringLiteral("external-io"), true, QStringLiteral("top")));
    }

    if (const QString error = finishConcept(model); !error.isEmpty())
        return {false, request.family, {}, error, requestedAxes};
    if (distributed) {
        return {true, request.family,
            QStringLiteral("central CAN/E-stop coordinator for six independently configured isolated joint-controller boards"),
            {}, requestedAxes};
    }
    return {true, request.family,
        QStringLiteral("single PCB for %1 BLDC axes with FOC control, encoder feedback, CAN, current monitoring, and E-stop")
            .arg(axes), {}, requestedAxes};
}

} // namespace

QString classifyApplicationFamily(const QString& ordinaryLanguage)
{
    const QString text = ordinaryLanguage.toLower();
    if (text.contains(QStringLiteral("robotic")) || text.contains(QStringLiteral("robot arm"))
        || text.contains(QStringLiteral("robot joint")) || text.contains(QStringLiteral("joint controller")))
        return QStringLiteral("robotic_joint_capstone");
    if (text.contains(QStringLiteral("bldc")) || text.contains(QStringLiteral("servo controller"))
        || text.contains(QStringLiteral("motor controller")))
        return QStringLiteral("bldc_servo_controller");
    if (text.contains(QStringLiteral("condition monitor")) || text.contains(QStringLiteral("vibration monitor")))
        return QStringLiteral("industrial_condition_monitor");
    if (text.contains(QStringLiteral("buck converter"))) return QStringLiteral("synchronous_buck_converter");
    if (text.contains(QStringLiteral("wireless sensor")) || text.contains(QStringLiteral("2.4 ghz")))
        return QStringLiteral("wireless_sensor_2_4ghz");
    if (text.contains(QStringLiteral("precision acquisition")) || text.contains(QStringLiteral("data acquisition")))
        return QStringLiteral("precision_acquisition_board");
    if (text.contains(QStringLiteral("gigabit")) || text.contains(QStringLiteral("high-speed board")))
        return QStringLiteral("usb_gigabit_high_speed_board");
    return QStringLiteral("custom_concept");
}

QString requirementProfileForFamily(const QString& family)
{
    return family + QStringLiteral("-concept");
}

int axisCountFromPrompt(const QString& ordinaryLanguage, int fallback)
{
    static const QRegularExpression expression(
        QStringLiteral("(?:^|\\b)([1-9])\\s*(?:-| )?axis(?:\\b|$)"),
        QRegularExpression::CaseInsensitiveOption);
    const QRegularExpressionMatch match = expression.match(ordinaryLanguage);
    return match.hasMatch() ? std::clamp(match.captured(1).toInt(), 1, 6)
                            : std::clamp(fallback, 1, 6);
}

ApplicationConceptResult buildApplicationConcept(
    ProjectModel& model, const ApplicationConceptRequest& request)
{
    if (request.inputMinV <= 0 || request.inputMaxV <= request.inputMinV)
        return {false, request.family, {},
                QStringLiteral("input voltage range must be positive and increasing"), 0};
    if (request.family == QStringLiteral("industrial_condition_monitor"))
        return buildConditionMonitor(model, request);
    if (request.family == QStringLiteral("synchronous_buck_converter"))
        return buildBuckConverter(model, request);
    if (request.family == QStringLiteral("precision_acquisition_board"))
        return buildPrecisionAcquisition(model, request);
    if (request.family == QStringLiteral("wireless_sensor_2_4ghz"))
        return buildWirelessSensor(model, request);
    if (request.family == QStringLiteral("usb_gigabit_high_speed_board"))
        return buildHighSpeedInterface(model, request);
    if (request.family == QStringLiteral("robotic_joint_capstone")
        || request.family == QStringLiteral("bldc_servo_controller"))
        return buildRoboticController(model, request);
    return {false, request.family, {},
        QStringLiteral("no deterministic electrical template exists for family '%1'; refusing to substitute another product")
            .arg(request.family), 0};
}

} // namespace designstudio
