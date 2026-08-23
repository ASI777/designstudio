#pragma once
#include <QString>
#include <QVector>
#include <QJsonObject>
#include <QJsonArray>
#include <QMap>
#include <QPointF>
#include <QSizeF>
#include <QStringList>
#include <cmath>

// ── Mirror of ProjectIO.cs data structures ────────────────────────────────────
// Matches the .dsproj JSON format exactly so files saved on Windows open here.

struct ProjPad {
    QString name;
    double  x_mm{}, y_mm{};
    double  w_mm{0.6}, h_mm{0.25};
    int     netId{-1};
    bool    throughHole{false};
    double  drillMm{0};
    QString shape{"rect"};       // rect | roundrect | oval | circle
    double  cornerR_mm{0};       // corner radius for roundrect (0 = sharp)
    double  clearanceMm{0};      // pad-specific override; 0 = inherit
};

// A custom copper polygon (e.g. a connector's curved corner solder area) that no
// rectangular pad can represent. Footprint-scoped: points are relative to the
// footprint origin, so it moves/rotates with the part.
struct ProjRegion {
    QVector<QPointF> points;     // closed polygon outline, in mm
    QVector<QPointF> hole;       // optional inner cutout (hollow centre of a ring)
    int     netId{-1};
};

struct ProjFootprint {
    QString             ref;
    QString             lib;
    QString             mpn;
    QString             manufacturer;
    QString             value;
    QJsonObject         datasheetEvidence;
    QJsonObject         boundComponent;            // immutable bound-component-ref/1
    QJsonObject         asset3d;                   // datasheet-derived component-3d-asset/1
    double              x_mm{}, y_mm{};
    double              rotDeg{};
    int                 side{0};    // 0=top, 1=bottom
    double              h3d_mm{};
    double              bodyW_mm{}, bodyH_mm{};   // exact silkscreen body outline
    double              bodyCx_mm{}, bodyCy_mm{}; // body centre vs origin
    QVector<QPointF>     courtyard;                // exact placement courtyard, optional
    bool                placementLocked{false};
    QString             functionalGroup;
    QString             edgeAnchor;                // left | right | top | bottom | none
    double              thermalPowerW{0};
    double              thermalClearanceMm{0};
    bool                testAccessRequired{false};
    double              testAccessHaloMm{0};
    QVector<ProjPad>    pads;
    QVector<ProjRegion> regions;                  // custom copper shapes
};

// Canonical ECAD instance transform used by the 2D canvas and placement checks.
// Bottom-side footprints mirror local Y before the same positive, counter-
// clockwise board rotation used by DesignCore and the FreeCAD STEP assembly.
inline QPointF footprintLocalToBoard(const ProjFootprint& fp, const QPointF& local,
                                     double xMm, double yMm, double rotationDeg)
{
    const double radians = rotationDeg * 3.14159265358979323846 / 180.0;
    const double c = std::cos(radians), s = std::sin(radians);
    const double localY = fp.side == 1 ? -local.y() : local.y();
    return {xMm + local.x() * c - localY * s,
            yMm + local.x() * s + localY * c};
}

inline QPointF footprintLocalToBoard(const ProjFootprint& fp, const QPointF& local)
{
    return footprintLocalToBoard(fp, local, fp.x_mm, fp.y_mm, fp.rotDeg);
}

struct ProjTrace {
    double ax_mm{}, ay_mm{};
    double bx_mm{}, by_mm{};
    double width_mm{0.25};
    int    netId{-1};
    int    layer{0};
    bool   isPour{false};
    double minWidthOverrideMm{0};
    double requiredCurrentA{0};       // optional branch allocation; 0 = net budget
};

struct ProjVia {
    double x_mm{}, y_mm{};
    double diaMm{0.6};
    double drillMm{0.3};
    int    netId{-1};
    int    fromLayer{0};
    int    toLayer{1};
    QString type{"through"};       // through | blind | buried | microvia
};

struct ProjNet {
    int     id{};
    QString name;
    int     classId{0};
    double  requiredCurrentA{0};
    double  nominalVoltageV{0};
};

struct ProjNetClass {
    int     id{};
    QString name;
    double  clearanceMm{0.2};
    double  traceWidthMm{0.25};
    double  viaDiaMm{0.6};
    double  viaDrillMm{0.3};
    double  diffPairGapMm{0};
    double  maxSkewMm{0};
    bool    allowMicrovia{false};
    double  z0Ohm{0};
    double  zdiffOhm{0};
    QVector<int> allowedLayers;         // empty = every copper layer
    QStringList allowedViaTypes{"through", "blind", "buried", "microvia"};
    int maxViaCount{0};                 // 0 = unrestricted
    double signalFrequencyHz{0};
    double impedanceTolerancePct{10};
};

struct ProjVerificationRequirements {
    bool requireSignalIntegrity{false};
    bool requirePowerIntegrity{false};
    bool requireThermal{false};
    // New projects fail closed at production export unless every footprint
    // resolves to a complete, digest-checked component binding.
    bool requireComponentSemantics{true};
    bool requireEnclosureEvidence{false};
    QString enclosureEvidencePath;
    QString enclosureEvidenceSha256;
};

struct ProjRuleArea {
    int id{0};
    QString name;
    QVector<QPointF> points;
    int layer{-1};                    // -1 = all copper layers
    double clearanceMm{0};            // 0 = inherit
    double minTraceWidthMm{0};        // 0 = inherit
    bool forbidRouting{false};
    bool forbidVias{false};
    bool forbidPlacement{false};
    double maxHeightMm{0};             // 0 = unrestricted
    QString source{"project"};
    QString sourceRevision{"1"};
};

struct ProjClassPairRule {
    int classA{0}, classB{0};
    double clearanceMm{0};
    QString source{"project"};
    QString sourceRevision{"1"};
};

struct ProjLayerPolicy {
    int layer{0};
    QString name;
    QString role{"signal"};             // signal | plane | mixed
    QString preferredDirection{"any"};  // any | horizontal | vertical
    bool allowRouting{true};
    double copperThicknessMm{0.035};
    QString source{"project"};
    QString sourceRevision{"1"};
};

struct ProjCopperZone {
    int id{0};
    QString name;
    QVector<QPointF> points;
    int netId{-1};
    int layer{0};
    double clearanceMm{0.2};
    double minIslandAreaMm2{0};
    bool requireConnection{true};
    QString source{"project"};
    QString sourceRevision{"1"};
};

// Versioned manufacturing/verification constraint profile. Values are project
// data, never UI constants: a fabricator import may replace the built-in
// industrial baseline while retaining source and revision provenance.
struct ProjPcbRuleProfile {
    int     schemaVersion{1};
    QString id{"industrial-class2-b-baseline"};
    QString name{"Industrial IPC Class 2 / Producibility B baseline"};
    int     ipcPerformanceClass{2};
    QString producibilityLevel{"B"};
    QString source{"builtin:designstudio"};
    QString sourceRevision{"1.0.0"};
    QString fabricator;
    QString assembler;

    double defaultClearanceMm{0.20};
    double minTraceWidthMm{0.10};
    double minMechanicalDrillMm{0.20};
    double minAnnularRingMm{0.05};
    double minDrillToDrillMm{0.50};
    double minMicroviaDrillMm{0.075};
    double minMicroviaWallMm{0.10};
    double minCopperToEdgeMm{0.25};
    double minCopperToHoleMm{0.25};
    double minCourtyardClearanceMm{0.25};
    double minMaskSliverMm{0.10};
    double minSilkWidthMm{0.10};

    bool checkConnectivity{true};
    bool checkSkew{true};
    bool releaseRequiresNativeDrc{true};

    // Rule name -> error | warning | info | disabled. Unknown rules default to
    // error so a newer engine cannot silently introduce non-blocking checks.
    QMap<QString, QString> severityByRule;
};

// ── Schematic-first model ─────────────────────────────────────────────────────
// The schematic is the source of truth for connectivity. Symbols are placed and
// wired here, then forward-annotated to PCB footprints (linked by ref designator).

enum class PinSide { Left = 0, Right = 1, Top = 2, Bottom = 3 };

struct SchPin {
    QString number;          // pad/pin number ("1", "A4", "TAB")
    QString name;            // functional name ("VCC", "GP4", "SDA")
    QString etype;           // electrical_type: power_in/power_out/input/output/bidir/passive/nc
    PinSide side{PinSide::Left};
    int     order{0};        // slot index along that side (top→bottom or left→right)
    int     netId{-1};       // net this pin is tied to (mirror of the footprint pad's net)
};

struct SchSymbol {
    QString          ref;          // "U1" — same designator as its PCB footprint
    QString          lib;          // component library name / MPN
    QString          value;        // electrical value/function shown on the schematic
    double           x_mm{}, y_mm{};   // symbol origin on the schematic sheet
    double           rotDeg{};
    int              unit{1};      // for multi-unit parts (op-amps, gate arrays)
    QVector<SchPin>  pins;
};

struct SchWire {
    int              netId{-1};
    QVector<QPointF> points;       // orthogonal polyline (sheet coordinates, mm)
};

// ── Main project model ─────────────────────────────────────────────────────────
class ProjectModel : public QObject {
    Q_OBJECT
public:
    explicit ProjectModel(QObject* parent = nullptr);

    bool loadFromFile(const QString& path);
    // Verification-only adapter for canonical project/3 wrappers. It reads the
    // embedded board while binding evidence to the exact outer file bytes and
    // never permits the legacy editor to save the wrapper.
    bool loadForVerification(const QString& path);
    bool saveToFile(const QString& path);
    QString lastError() const { return m_lastError; }

    void clear();
    QString filePath() const { return m_filePath; }
    QString documentId() const { return m_documentId; }
    qint64  revision() const { return m_revision; }
    QString fileSha256() const { return m_fileSha256; }
    bool    hasPersistedIdentity() const { return m_identityPersisted; }
    bool    isModified() const { return m_modified; }
    void    setModified(bool v);

    // Board geometry
    double boardWidthMm{100};
    double boardHeightMm{80};
    QVector<QPointF> boardOutline;              // empty = legacy rectangle
    QVector<QVector<QPointF>> boardCutouts;     // routed slots/void polygons
    double gridMm{1.27};
    int    copperLayers{2};
    double erDielectric{4.4};
    double dielectricHMm{0.2};
    double lossTangent{0.02};
    double copperTMm{0.035};
    ProjPcbRuleProfile pcbRules;
    ProjVerificationRequirements verificationRequirements;

    QVector<ProjNet>       nets;
    QVector<ProjNetClass>  netClasses;
    QVector<ProjRuleArea>  ruleAreas;
    QVector<ProjClassPairRule> classPairRules;
    QVector<ProjLayerPolicy> layerPolicies;
    QVector<ProjCopperZone> copperZones;
    QVector<ProjFootprint> footprints;
    QVector<ProjTrace>     traces;
    QVector<ProjVia>       vias;
    QJsonArray unresolvedComponents;
    // Explicit lifecycle marker for unconstrained draft versus optimized PCB
    // placement. It is informational; native DRC remains authoritative.
    QJsonObject placementState;
    // Immutable enclosure/assembly boundary produced by the FreeCAD workbench.
    // The electronics editor preserves this object byte-for-structure and may
    // consume its derived board fields, but never authors or unlocks it.
    QJsonObject mechanicalContract;

    // Schematic-first artifacts (source of truth for connectivity)
    QVector<SchSymbol>     symbols;
    QVector<SchWire>       schWires;

    // helpers
    QString netName(int id) const;
    int     netIdByName(const QString& name) const;
    QString validatePcbRuleProfile() const;
    QString validatePcbRuleProfile(const ProjPcbRuleProfile& profile) const;
    bool loadPcbRuleProfileFile(const QString& path, ProjPcbRuleProfile& out,
                                QString& error) const;
    QString severityForRule(const QString& ruleName) const;

    // Placement legality uses the real substrate outline and the footprint's
    // courtyard/body/pad geometry, not just the footprint origin.
    bool isFootprintInsideBoard(const ProjFootprint& fp, double xMm, double yMm,
                                double rotationDeg, double marginMm = 0.0) const;
    QString legalizeFootprints(bool preserveLocked = true);

    // Generate schematic symbols from the current footprints when none exist yet:
    // infers each pin's electrical role from its name and lays pins out by
    // convention (power top, ground bottom, signals split left/right). No-op if
    // symbols are already present. Returns the number of symbols generated.
    int generateSymbolsFromFootprints();

    // Create a new net with a unique id and the given name. Returns the new id.
    int createNet(const QString& name);

    // Forward-annotation: set the net of one symbol pin AND the matching PCB
    // footprint pad (same ref designator, pad name == pin number) so a schematic
    // edit flows straight to the board. Updates the SchPin's cached netId too.
    void setPinNet(const QString& ref, const QString& pinNumber, int netId);

signals:
    void modified();
    void loaded();

private:
    QString m_filePath;
    QString m_documentId;
    QString m_fileSha256;
    qint64  m_revision{0};
    bool    m_identityPersisted{false};
    bool    m_readOnlyProject3{false};
    QString m_lastError;
    bool    m_modified{false};
    // Lossless extension storage for board/schematic fields owned by release,
    // mechanical, manufacturing, or future schema subsystems.
    QJsonObject m_rootExtensions;
    QJsonObject m_schematicExtensions;

    bool loadFromFileInternal(const QString& path, bool allowProject3ForVerification);

    ProjPad        parsePad(const QJsonObject& o) const;
    ProjFootprint  parseFp(const QJsonObject& o)  const;
    ProjTrace      parseTrace(const QJsonObject& o) const;
    ProjVia        parseVia(const QJsonObject& o)  const;
    ProjNet        parseNet(const QJsonObject& o)  const;
    ProjNetClass   parseNetClass(const QJsonObject& o) const;
    ProjPcbRuleProfile parsePcbRuleProfile(const QJsonObject& o) const;
    ProjRuleArea   parseRuleArea(const QJsonObject& o) const;
    ProjClassPairRule parseClassPairRule(const QJsonObject& o) const;
    ProjLayerPolicy parseLayerPolicy(const QJsonObject& o) const;
    ProjCopperZone parseCopperZone(const QJsonObject& o) const;
    SchSymbol      parseSymbol(const QJsonObject& o) const;
    SchWire        parseWire(const QJsonObject& o) const;

    QJsonObject toJson(const ProjPad& p)       const;
    QJsonObject toJson(const ProjFootprint& fp) const;
    QJsonObject toJson(const ProjTrace& t)     const;
    QJsonObject toJson(const ProjVia& v)       const;
    QJsonObject toJson(const ProjNet& n)       const;
    QJsonObject toJson(const ProjNetClass& nc) const;
    QJsonObject toJson(const ProjPcbRuleProfile& p) const;
    QJsonObject toJson(const ProjRuleArea& a) const;
    QJsonObject toJson(const ProjClassPairRule& r) const;
    QJsonObject toJson(const ProjLayerPolicy& p) const;
    QJsonObject toJson(const ProjCopperZone& z) const;
    QJsonObject toJson(const SchSymbol& s)     const;
    QJsonObject toJson(const SchWire& w)       const;
};
