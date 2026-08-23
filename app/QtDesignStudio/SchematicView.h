#pragma once
#include <QWidget>
#include <QTransform>
#include <QMap>
#include <QVector>
#include <QPointF>
#include <QSizeF>
#include <cmath>
#include <memory>
#include "ProjectModel.h"   // SchSymbol / SchPin
class QPainter;
class QComboBox;
namespace designstudio { class KicadSymbolLibrary; struct KicadSymbolDefinition; }

class SchematicView : public QWidget {
    Q_OBJECT
public:
    explicit SchematicView(ProjectModel* model, QWidget* parent = nullptr);
    ~SchematicView() override;
    void fitToSchematic();
    void zoomIn();
    void zoomOut();
    bool loadKicadSymbolSource(const QString& path, QString* errorMessage = nullptr);
    int loadedKicadSymbolCount() const;

signals:
    void statusMessage(const QString& msg);

protected:
    void paintEvent(QPaintEvent*) override;
    void mousePressEvent(QMouseEvent*) override;
    void mouseMoveEvent(QMouseEvent*) override;
    void mouseReleaseEvent(QMouseEvent*) override;
    void wheelEvent(QWheelEvent*) override;
    void resizeEvent(QResizeEvent*) override;
    void keyPressEvent(QKeyEvent*) override;    // R rotate · Del delete · Esc cancel

private:
    ProjectModel* m_model;
    QTransform    m_xform;
    double        m_scale{8.0};   // px per mm
    double        m_tx{40}, m_ty{40};
    bool          m_panning{false};
    QPoint        m_panStart;

    // ── Editing state ──────────────────────────────────────────────────────────
    int      m_selSym{-1};        // selected symbol index (-1 = none)
    bool     m_movingSym{false};
    QPointF  m_moveGrabMm;        // offset from symbol origin to grab point (mm)
    int      m_wireSym{-1}, m_wirePin{-1};   // pending wire source pin
    QPointF  m_wireCurMm;         // live mouse position while wiring (mm)
    int      m_hoverSym{-1}, m_hoverPin{-1}; // pin under cursor (connection affordance)
    std::unique_ptr<designstudio::KicadSymbolLibrary> m_kicadSymbols;
    QComboBox*    m_scopeSelector{};
    enum class Scope { Overview, HumanInterface, ControlIo, Power, Passives };
    Scope          m_scope{Scope::Overview};

    QPointF screenToSheet(const QPoint& px) const;       // inverse transform
    int     hitSymbol(const QPointF& mm) const;          // body under point
    bool    hitPin(const QPointF& mm, int& symIdx, int& pinIdx) const;  // pin tip near point
    void    connectPins(int symA, int pinA, int symB, int pinB);
    void    rotateSelected();                            // 90° CW, remaps pin sides
    void    deleteSelected();                            // remove symbol + free its pins
    static double snap(double mm) { return std::round(mm / kPitchMm) * kPitchMm; }

    // Ensure symbols exist (generates from footprints on first view) and fit.
    void ensureSymbols();

    // Symbol geometry (all in mm, sheet coordinates).
    QSizeF  symbolSize(const SchSymbol& s) const;     // body w/h
    QPointF pinRoot(const SchSymbol& s, const SchPin& p) const;  // where pin meets body
    QPointF pinTip(const SchSymbol& s, const SchPin& p) const;   // wire-connection tip

    void drawSymbol(QPainter& p, const SchSymbol& s) const;
    void drawLibraryGraphic(QPainter& p, const QRectF& body,
                            const designstudio::KicadSymbolDefinition& definition) const;
    void drawFallbackGraphic(QPainter& p, const QRectF& body,
                             const SchSymbol& symbol) const;
    void drawNets(QPainter& p) const;
    void updateTransform();
    QColor netColor(int netId) const;
    bool    symbolVisible(const SchSymbol& symbol) const;

    static constexpr double kPitchMm = 2.54;   // pin pitch (0.1")
    static constexpr double kStubMm  = 2.54;   // pin stub length
};
