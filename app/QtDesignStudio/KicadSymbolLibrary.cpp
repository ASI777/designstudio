#include "KicadSymbolLibrary.h"

#include <QFile>
#include <QFileInfo>

#include <algorithm>
#include <cmath>
#include <optional>

namespace designstudio {
namespace {

struct Node {
    QString atom;
    QVector<Node> children;
    bool isList() const noexcept { return !children.isEmpty(); }
    QString tag() const { return children.isEmpty() ? QString() : children.front().atom; }
};

class Parser final {
public:
    explicit Parser(const QByteArray& bytes) : bytes_(bytes) {}

    std::optional<Node> parse(QString& error)
    {
        skipWhitespace();
        if (position_ >= bytes_.size() || bytes_.at(position_) != '(') {
            error = QStringLiteral("KiCad S-expression must start with '('");
            return std::nullopt;
        }
        auto result = parseNode(0, error);
        if (!result) return std::nullopt;
        skipWhitespace();
        if (position_ != bytes_.size()) {
            error = QStringLiteral("Unexpected data after the top-level S-expression");
            return std::nullopt;
        }
        return result;
    }

private:
    std::optional<Node> parseNode(int depth, QString& error)
    {
        if (depth > 256) {
            error = QStringLiteral("KiCad S-expression nesting exceeds 256 levels");
            return std::nullopt;
        }
        skipWhitespace();
        if (position_ >= bytes_.size()) {
            error = QStringLiteral("Unexpected end of KiCad S-expression");
            return std::nullopt;
        }
        if (bytes_.at(position_) != '(') return parseAtom(error);
        ++position_;
        Node list;
        while (true) {
            skipWhitespace();
            if (position_ >= bytes_.size()) {
                error = QStringLiteral("Unterminated KiCad S-expression list");
                return std::nullopt;
            }
            if (bytes_.at(position_) == ')') {
                ++position_;
                if (list.children.isEmpty()) {
                    error = QStringLiteral("Empty KiCad S-expression list is not valid here");
                    return std::nullopt;
                }
                return list;
            }
            if (++nodeCount_ > 2'000'000) {
                error = QStringLiteral("KiCad file exceeds the two-million-node parsing limit");
                return std::nullopt;
            }
            auto child = parseNode(depth + 1, error);
            if (!child) return std::nullopt;
            list.children.push_back(std::move(*child));
        }
    }

    std::optional<Node> parseAtom(QString& error)
    {
        Node node;
        if (bytes_.at(position_) == '"') {
            ++position_;
            QByteArray value;
            bool escaped = false;
            while (position_ < bytes_.size()) {
                const char c = bytes_.at(position_++);
                if (escaped) {
                    if (c == 'n') value.append('\n');
                    else if (c == 'r') value.append('\r');
                    else if (c == 't') value.append('\t');
                    else value.append(c);
                    escaped = false;
                } else if (c == '\\') {
                    escaped = true;
                } else if (c == '"') {
                    node.atom = QString::fromUtf8(value);
                    return node;
                } else {
                    value.append(c);
                }
            }
            error = QStringLiteral("Unterminated quoted string in KiCad file");
            return std::nullopt;
        }
        const qsizetype start = position_;
        while (position_ < bytes_.size()) {
            const char c = bytes_.at(position_);
            if (c == '(' || c == ')' || c == ' ' || c == '\t' || c == '\r' || c == '\n')
                break;
            ++position_;
        }
        if (position_ == start) {
            error = QStringLiteral("Invalid token in KiCad S-expression");
            return std::nullopt;
        }
        node.atom = QString::fromUtf8(bytes_.mid(start, position_ - start));
        return node;
    }

    void skipWhitespace()
    {
        while (position_ < bytes_.size()) {
            const char c = bytes_.at(position_);
            if (c == ' ' || c == '\t' || c == '\r' || c == '\n') ++position_;
            else break;
        }
    }

    const QByteArray& bytes_;
    qsizetype position_{};
    qsizetype nodeCount_{};
};

const Node* child(const Node& node, const QString& tag)
{
    for (const Node& item : node.children)
        if (item.isList() && item.tag() == tag) return &item;
    return nullptr;
}

std::optional<double> number(const Node& node, int index)
{
    if (index < 0 || index >= node.children.size()) return std::nullopt;
    bool ok = false;
    const double value = node.children.at(index).atom.toDouble(&ok);
    if (!ok || !std::isfinite(value)) return std::nullopt;
    return value;
}

std::optional<QPointF> point(const Node* node)
{
    if (!node) return std::nullopt;
    const auto x = number(*node, 1);
    const auto y = number(*node, 2);
    if (!x || !y) return std::nullopt;
    return QPointF(*x, *y);
}

double strokeWidth(const Node& node)
{
    const Node* stroke = child(node, QStringLiteral("stroke"));
    const Node* width = stroke ? child(*stroke, QStringLiteral("width")) : nullptr;
    const auto value = width ? number(*width, 1) : std::nullopt;
    return value && *value >= 0.0 ? *value : 0.254;
}

bool isFilled(const Node& node)
{
    const Node* fill = child(node, QStringLiteral("fill"));
    const Node* type = fill ? child(*fill, QStringLiteral("type")) : nullptr;
    return type && type->children.size() > 1
        && type->children.at(1).atom != QStringLiteral("none");
}

void extendBounds(QRectF& bounds, const QPointF& value)
{
    const QRectF tiny(value, QSizeF(0.000001, 0.000001));
    bounds = bounds.isNull() ? tiny : bounds.united(tiny);
}

void parseGraphic(const Node& node, KicadSymbolDefinition& definition)
{
    KicadSymbolGraphic graphic;
    const QString tag = node.tag();
    if (tag == QStringLiteral("rectangle")) {
        graphic.kind = KicadSymbolGraphic::Kind::Rectangle;
        const auto start = point(child(node, QStringLiteral("start")));
        const auto end = point(child(node, QStringLiteral("end")));
        if (!start || !end) return;
        graphic.points = {*start, *end};
    } else if (tag == QStringLiteral("circle")) {
        graphic.kind = KicadSymbolGraphic::Kind::Circle;
        const auto center = point(child(node, QStringLiteral("center")));
        const Node* radius = child(node, QStringLiteral("radius"));
        const auto radiusValue = radius ? number(*radius, 1) : std::nullopt;
        if (!center || !radiusValue || *radiusValue <= 0.0) return;
        graphic.points = {*center};
        graphic.radiusMm = *radiusValue;
    } else if (tag == QStringLiteral("polyline") || tag == QStringLiteral("bezier")) {
        graphic.kind = tag == QStringLiteral("polyline")
            ? KicadSymbolGraphic::Kind::Polyline : KicadSymbolGraphic::Kind::Bezier;
        const Node* points = child(node, QStringLiteral("pts"));
        if (!points) return;
        for (const Node& item : points->children) {
            if (item.isList() && item.tag() == QStringLiteral("xy")) {
                const auto value = point(&item);
                if (value) graphic.points.push_back(*value);
            }
        }
        if (graphic.points.size() < 2) return;
    } else if (tag == QStringLiteral("arc")) {
        graphic.kind = KicadSymbolGraphic::Kind::Arc;
        const auto start = point(child(node, QStringLiteral("start")));
        const auto mid = point(child(node, QStringLiteral("mid")));
        const auto end = point(child(node, QStringLiteral("end")));
        if (!start || !mid || !end) return;
        graphic.points = {*start, *mid, *end};
    } else {
        return;
    }
    graphic.strokeWidthMm = strokeWidth(node);
    graphic.filled = isFilled(node);
    if (graphic.kind == KicadSymbolGraphic::Kind::Circle) {
        const QPointF c = graphic.points.front();
        extendBounds(definition.boundsMm,
                     c - QPointF(graphic.radiusMm, graphic.radiusMm));
        extendBounds(definition.boundsMm,
                     c + QPointF(graphic.radiusMm, graphic.radiusMm));
    } else {
        for (const QPointF& value : graphic.points) extendBounds(definition.boundsMm, value);
    }
    definition.graphics.push_back(std::move(graphic));
}

void collectGraphics(const Node& node, KicadSymbolDefinition& definition)
{
    for (const Node& item : node.children) {
        if (!item.isList()) continue;
        const QString tag = item.tag();
        if (tag == QStringLiteral("rectangle") || tag == QStringLiteral("circle")
            || tag == QStringLiteral("polyline") || tag == QStringLiteral("arc")
            || tag == QStringLiteral("bezier"))
            parseGraphic(item, definition);
        else if (tag == QStringLiteral("symbol"))
            collectGraphics(item, definition);
    }
}

QString leafName(QString id)
{
    const int colon = id.lastIndexOf(QLatin1Char(':'));
    if (colon >= 0) id = id.mid(colon + 1);
    const int slash = id.lastIndexOf(QLatin1Char('/'));
    if (slash >= 0) id = id.mid(slash + 1);
    return id;
}

} // namespace

bool KicadSymbolLibrary::loadFile(const QString& path, QString* errorMessage)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        if (errorMessage) *errorMessage = file.errorString();
        return false;
    }
    constexpr qint64 MaxBytes = 256LL * 1024LL * 1024LL;
    if (file.size() <= 0 || file.size() > MaxBytes) {
        if (errorMessage) *errorMessage = QStringLiteral(
            "KiCad symbol source is empty or exceeds the 256 MiB limit");
        return false;
    }
    const QByteArray bytes = file.readAll();
    QString parseError;
    auto root = Parser(bytes).parse(parseError);
    if (!root) {
        if (errorMessage) *errorMessage = parseError;
        return false;
    }

    const Node* container = nullptr;
    if (root->tag() == QStringLiteral("kicad_symbol_lib")) container = &*root;
    else if (root->tag() == QStringLiteral("kicad_sch"))
        container = child(*root, QStringLiteral("lib_symbols"));
    if (!container) {
        if (errorMessage) *errorMessage = QStringLiteral(
            "Expected kicad_symbol_lib or a kicad_sch file with lib_symbols");
        return false;
    }

    int loaded = 0;
    for (const Node& item : container->children) {
        if (!item.isList() || item.tag() != QStringLiteral("symbol")
            || item.children.size() < 2 || item.children.at(1).isList()) continue;
        KicadSymbolDefinition definition;
        definition.name = item.children.at(1).atom;
        collectGraphics(item, definition);
        if (definition.graphics.isEmpty() || definition.boundsMm.isNull()) continue;
        symbols_.insert(definition.name, definition);
        const QString leaf = leafName(definition.name);
        if (!leaf.isEmpty() && !symbols_.contains(leaf)) symbols_.insert(leaf, definition);
        ++loaded;
    }
    if (loaded == 0) {
        if (errorMessage) *errorMessage = QStringLiteral(
            "No drawable symbol definitions were found in this KiCad source");
        return false;
    }
    const QString absolute = QFileInfo(path).absoluteFilePath();
    if (!sourcePaths_.contains(absolute)) sourcePaths_.push_back(absolute);
    if (errorMessage) errorMessage->clear();
    return true;
}

const KicadSymbolDefinition* KicadSymbolLibrary::definition(const QString& libraryId) const
{
    auto found = symbols_.constFind(libraryId);
    if (found != symbols_.cend()) return &found.value();
    found = symbols_.constFind(leafName(libraryId));
    return found == symbols_.cend() ? nullptr : &found.value();
}

} // namespace designstudio
