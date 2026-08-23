#pragma once

#include <QHash>
#include <QPointF>
#include <QRectF>
#include <QString>
#include <QStringList>
#include <QVector>

namespace designstudio {

struct KicadSymbolGraphic {
    enum class Kind { Rectangle, Circle, Polyline, Arc, Bezier };
    Kind kind{Kind::Polyline};
    QVector<QPointF> points;
    double radiusMm{};
    double strokeWidthMm{0.254};
    bool filled{false};
};

struct KicadSymbolDefinition {
    QString name;
    QVector<KicadSymbolGraphic> graphics;
    QRectF boundsMm;
};

// Read-only interchange for KiCad .kicad_sym libraries and the lib_symbols
// section embedded in .kicad_sch files. Assets stay external; DesignStudio only
// keeps independently parsed vector geometry in memory.
class KicadSymbolLibrary final {
public:
    bool loadFile(const QString& path, QString* errorMessage = nullptr);
    const KicadSymbolDefinition* definition(const QString& libraryId) const;
    int symbolCount() const noexcept { return symbols_.size(); }
    QStringList sourcePaths() const { return sourcePaths_; }

private:
    QHash<QString, KicadSymbolDefinition> symbols_;
    QStringList sourcePaths_;
};

} // namespace designstudio
