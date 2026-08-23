#include "GlbMeshReader.h"

#include <QCryptographicHash>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QMatrix4x4>
#include <QQuaternion>
#include <QSet>
#include <QtEndian>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <optional>

namespace designstudio {
namespace {

constexpr quint32 GlbMagic = 0x46546c67;
constexpr quint32 JsonChunk = 0x4e4f534a;
constexpr quint32 BinChunk = 0x004e4942;
constexpr int MaxNodeDepth = 256;

quint32 readU32(const QByteArray& bytes, qsizetype offset)
{
    const auto* p = reinterpret_cast<const unsigned char*>(bytes.constData() + offset);
    return quint32(p[0]) | (quint32(p[1]) << 8) | (quint32(p[2]) << 16)
        | (quint32(p[3]) << 24);
}

std::optional<qint64> jsonInteger(const QJsonValue& value)
{
    if (!value.isDouble()) return std::nullopt;
    const double raw = value.toDouble();
    if (!std::isfinite(raw) || std::floor(raw) != raw || raw < 0
        || raw > 9007199254740991.0) return std::nullopt;
    return static_cast<qint64>(raw);
}

QString memberPath(const QString& parent, const QString& member)
{
    return parent + QLatin1Char('.') + member;
}

QString itemPath(const QString& parent, qsizetype index)
{
    return parent + QLatin1Char('[') + QString::number(index) + QLatin1Char(']');
}

struct AccessorView {
    const char* data = nullptr;
    qsizetype count = 0;
    qsizetype stride = 0;
    qsizetype elementBytes = 0;
    int componentType = 0;
    QString type;
};

class Parser final {
public:
    Parser(const QByteArray& bytes, QString sourceName)
        : bytes_(bytes), sourceName_(std::move(sourceName))
    {
    }

    GlbLoadResult parse()
    {
        parseContainer();
        if (!issues_.isEmpty()) return result();
        parseDocument();
        if (!issues_.isEmpty()) return result();
        parseScene();
        if (!issues_.isEmpty()) return result();
        if (vertices_.isEmpty() || indices_.isEmpty()) {
            add(QStringLiteral("$.meshes"), QStringLiteral("default scene contains no triangle geometry"));
            return result();
        }
        if (indices_.size() % 3 != 0) {
            add(QStringLiteral("$.meshes"), QStringLiteral("triangle index stream is not divisible by three"));
            return result();
        }

        auto mesh = std::make_shared<GlbMesh>(
            std::move(vertices_), std::move(indices_), boundsMin_, boundsMax_,
            QString::fromLatin1(
                QCryptographicHash::hash(bytes_, QCryptographicHash::Sha256).toHex()),
            sourceName_, bytes_.size());
        GlbLoadResult loaded;
        loaded.mesh = std::move(mesh);
        return loaded;
    }

private:
    void add(const QString& path, const QString& message)
    {
        issues_.push_back({path, message});
    }

    GlbLoadResult result()
    {
        GlbLoadResult value;
        value.issues = std::move(issues_);
        return value;
    }

    void parseContainer()
    {
        if (bytes_.size() < 20) {
            add(QStringLiteral("$"), QStringLiteral("GLB is shorter than its header and first chunk"));
            return;
        }
        if (readU32(bytes_, 0) != GlbMagic) {
            add(QStringLiteral("$"), QStringLiteral("invalid GLB magic"));
            return;
        }
        if (readU32(bytes_, 4) != 2) {
            add(QStringLiteral("$"), QStringLiteral("only GLB version 2 is supported"));
            return;
        }
        if (readU32(bytes_, 8) != static_cast<quint32>(bytes_.size())) {
            add(QStringLiteral("$"), QStringLiteral("GLB header length does not match file size"));
            return;
        }

        qsizetype offset = 12;
        int chunkIndex = 0;
        while (offset < bytes_.size()) {
            if (offset + 8 > bytes_.size()) {
                add(QStringLiteral("$"), QStringLiteral("truncated GLB chunk header"));
                return;
            }
            const quint32 length = readU32(bytes_, offset);
            const quint32 type = readU32(bytes_, offset + 4);
            offset += 8;
            if (length % 4 != 0 || qint64(offset) + qint64(length) > bytes_.size()) {
                add(QStringLiteral("$"), QStringLiteral("invalid or truncated GLB chunk length"));
                return;
            }
            const QByteArray chunk = bytes_.mid(offset, length);
            if (chunkIndex == 0 && type != JsonChunk) {
                add(QStringLiteral("$"), QStringLiteral("first GLB chunk must be JSON"));
                return;
            }
            if (type == JsonChunk) {
                if (!jsonBytes_.isEmpty()) {
                    add(QStringLiteral("$"), QStringLiteral("GLB contains multiple JSON chunks"));
                    return;
                }
                jsonBytes_ = chunk;
            } else if (type == BinChunk) {
                ++binChunkCount_;
                if (binChunkCount_ > 1) {
                    add(QStringLiteral("$"), QStringLiteral("GLB contains multiple BIN chunks"));
                    return;
                }
                binBytes_ = chunk;
            }
            offset += length;
            ++chunkIndex;
        }
        if (jsonBytes_.isEmpty() || binBytes_.isEmpty()) {
            add(QStringLiteral("$"), QStringLiteral("self-contained GLB requires JSON and BIN chunks"));
        }
    }

    void parseDocument()
    {
        while (!jsonBytes_.isEmpty()
               && (jsonBytes_.endsWith(' ') || jsonBytes_.endsWith('\0'))) {
            jsonBytes_.chop(1);
        }
        QJsonParseError error;
        const QJsonDocument document = QJsonDocument::fromJson(jsonBytes_, &error);
        if (error.error != QJsonParseError::NoError || !document.isObject()) {
            add(QStringLiteral("$"), QStringLiteral("invalid GLB JSON: %1").arg(error.errorString()));
            return;
        }
        root_ = document.object();
        const QJsonObject asset = root_.value(QStringLiteral("asset")).toObject();
        if (asset.value(QStringLiteral("version")).toString() != QStringLiteral("2.0")) {
            add(QStringLiteral("$.asset.version"), QStringLiteral("must equal '2.0'"));
        }

        const QJsonArray buffers = root_.value(QStringLiteral("buffers")).toArray();
        if (buffers.size() != 1 || !buffers.at(0).isObject()) {
            add(QStringLiteral("$.buffers"), QStringLiteral("self-contained GLB must declare exactly one buffer"));
            return;
        }
        const QJsonObject buffer = buffers.at(0).toObject();
        if (buffer.contains(QStringLiteral("uri"))) {
            add(QStringLiteral("$.buffers[0].uri"), QStringLiteral("external/data URI buffers are not accepted in GLB mode"));
        }
        const auto declared = jsonInteger(buffer.value(QStringLiteral("byteLength")));
        if (!declared || *declared > binBytes_.size()) {
            add(QStringLiteral("$.buffers[0].byteLength"), QStringLiteral("exceeds available BIN chunk bytes"));
        }
    }

    std::optional<AccessorView> accessor(qsizetype accessorIndex,
                                         const QString& expectedType,
                                         const QSet<int>& componentTypes,
                                         const QString& path)
    {
        const QJsonArray accessors = root_.value(QStringLiteral("accessors")).toArray();
        const QJsonArray views = root_.value(QStringLiteral("bufferViews")).toArray();
        if (accessorIndex < 0 || accessorIndex >= accessors.size()
            || !accessors.at(accessorIndex).isObject()) {
            add(path, QStringLiteral("accessor index is out of range"));
            return std::nullopt;
        }
        const QJsonObject a = accessors.at(accessorIndex).toObject();
        if (a.contains(QStringLiteral("sparse"))) {
            add(path, QStringLiteral("sparse accessors are not supported by the enclosure tracker"));
            return std::nullopt;
        }
        if (a.contains(QStringLiteral("normalized"))
            && !a.value(QStringLiteral("normalized")).isBool()) {
            add(path, QStringLiteral("normalized must be a boolean"));
            return std::nullopt;
        }
        if (a.value(QStringLiteral("normalized")).toBool(false)) {
            add(path, QStringLiteral("normalized accessors are not supported"));
            return std::nullopt;
        }
        const auto viewIndex = jsonInteger(a.value(QStringLiteral("bufferView")));
        const auto count = jsonInteger(a.value(QStringLiteral("count")));
        const auto component = jsonInteger(a.value(QStringLiteral("componentType")));
        const QString type = a.value(QStringLiteral("type")).toString();
        const qint64 maxCount = expectedType == QStringLiteral("VEC3")
            ? GlbMeshReader::MaxVertices : qint64(GlbMeshReader::MaxTriangles) * 3;
        if (!viewIndex || *viewIndex >= views.size() || !views.at(*viewIndex).isObject()
            || !count || *count <= 0 || *count > maxCount
            || !component || !componentTypes.contains(static_cast<int>(*component))
            || type != expectedType) {
            add(path, QStringLiteral("accessor type, component type, count, or bufferView is unsupported"));
            return std::nullopt;
        }
        const QJsonObject view = views.at(*viewIndex).toObject();
        const auto bufferIndex = jsonInteger(view.value(QStringLiteral("buffer")));
        const auto viewLength = jsonInteger(view.value(QStringLiteral("byteLength")));
        const auto viewOffsetValue = jsonInteger(view.value(QStringLiteral("byteOffset")));
        const auto accessorOffsetValue = jsonInteger(a.value(QStringLiteral("byteOffset")));
        if ((view.contains(QStringLiteral("byteOffset")) && !viewOffsetValue)
            || (a.contains(QStringLiteral("byteOffset")) && !accessorOffsetValue)) {
            add(path, QStringLiteral("byteOffset must be a non-negative integer"));
            return std::nullopt;
        }
        const qint64 viewOffset = viewOffsetValue.value_or(0);
        const qint64 accessorOffset = accessorOffsetValue.value_or(0);
        if (!bufferIndex || *bufferIndex != 0 || !viewLength || viewOffset < 0
            || accessorOffset < 0 || viewOffset + *viewLength > binBytes_.size()) {
            add(path, QStringLiteral("bufferView range is invalid"));
            return std::nullopt;
        }

        const int componentBytes = *component == 5126 || *component == 5125 ? 4
            : (*component == 5123 ? 2 : 1);
        const int components = expectedType == QStringLiteral("VEC3") ? 3 : 1;
        const qint64 elementBytes = qint64(componentBytes) * components;
        const auto strideValue = jsonInteger(view.value(QStringLiteral("byteStride")));
        if (view.contains(QStringLiteral("byteStride")) && !strideValue) {
            add(path, QStringLiteral("byteStride must be a non-negative integer"));
            return std::nullopt;
        }
        const qint64 stride = strideValue.value_or(elementBytes);
        const qint64 start = viewOffset + accessorOffset;
        const qint64 finish = start + (qint64(*count) - 1) * stride + elementBytes;
        if (stride < elementBytes || stride > 252 || start < viewOffset
            || start % componentBytes != 0 || stride % componentBytes != 0
            || finish > viewOffset + *viewLength || finish > binBytes_.size()) {
            add(path, QStringLiteral("accessor byte range or stride is invalid"));
            return std::nullopt;
        }
        return AccessorView{binBytes_.constData() + start,
                            static_cast<qsizetype>(*count),
                            static_cast<qsizetype>(stride),
                            static_cast<qsizetype>(elementBytes),
                            static_cast<int>(*component), type};
    }

    static float readFloat(const char* p)
    {
        const quint32 bits = qFromLittleEndian<quint32>(
            reinterpret_cast<const uchar*>(p));
        float value = 0;
        std::memcpy(&value, &bits, sizeof(value));
        return value;
    }

    static quint32 readIndex(const char* p, int componentType)
    {
        if (componentType == 5121) return static_cast<unsigned char>(*p);
        if (componentType == 5123)
            return qFromLittleEndian<quint16>(reinterpret_cast<const uchar*>(p));
        return qFromLittleEndian<quint32>(reinterpret_cast<const uchar*>(p));
    }

    std::optional<QMatrix4x4> nodeTransform(const QJsonObject& node, const QString& path)
    {
        if (node.contains(QStringLiteral("matrix"))) {
            if (node.contains(QStringLiteral("translation"))
                || node.contains(QStringLiteral("rotation"))
                || node.contains(QStringLiteral("scale"))) {
                add(path, QStringLiteral("node matrix cannot be combined with TRS properties"));
                return std::nullopt;
            }
            const QJsonArray values = node.value(QStringLiteral("matrix")).toArray();
            if (values.size() != 16) {
                add(memberPath(path, QStringLiteral("matrix")), QStringLiteral("must contain 16 finite numbers"));
                return std::nullopt;
            }
            QMatrix4x4 matrix;
            for (int column = 0; column < 4; ++column) {
                for (int row = 0; row < 4; ++row) {
                    const double value = values.at(column * 4 + row).toDouble(
                        std::numeric_limits<double>::quiet_NaN());
                    if (!std::isfinite(value)) {
                        add(memberPath(path, QStringLiteral("matrix")), QStringLiteral("contains a non-finite value"));
                        return std::nullopt;
                    }
                    matrix(row, column) = float(value);
                }
            }
            return matrix;
        }

        auto vector = [this, &node, &path](const char* name, int size,
                                           const QVector<double>& defaults)
            -> std::optional<QVector<double>> {
            if (!node.contains(QLatin1String(name))) return defaults;
            const QJsonArray values = node.value(QLatin1String(name)).toArray();
            if (values.size() != size) {
                add(memberPath(path, QLatin1String(name)), QStringLiteral("has an invalid length"));
                return std::nullopt;
            }
            QVector<double> parsed;
            parsed.reserve(size);
            for (const QJsonValue& raw : values) {
                const double value = raw.toDouble(std::numeric_limits<double>::quiet_NaN());
                if (!std::isfinite(value)) {
                    add(memberPath(path, QLatin1String(name)), QStringLiteral("contains a non-finite value"));
                    return std::nullopt;
                }
                parsed.push_back(value);
            }
            return parsed;
        };

        const auto t = vector("translation", 3, {0, 0, 0});
        const auto r = vector("rotation", 4, {0, 0, 0, 1});
        const auto s = vector("scale", 3, {1, 1, 1});
        if (!t || !r || !s) return std::nullopt;
        QQuaternion rotation(float(r->at(3)), float(r->at(0)),
                             float(r->at(1)), float(r->at(2)));
        if (rotation.lengthSquared() < 1e-12f) {
            add(memberPath(path, QStringLiteral("rotation")), QStringLiteral("quaternion must be non-zero"));
            return std::nullopt;
        }
        rotation.normalize();
        QMatrix4x4 translationMatrix;
        translationMatrix.translate(float(t->at(0)), float(t->at(1)), float(t->at(2)));
        QMatrix4x4 rotationMatrix;
        rotationMatrix.rotate(rotation);
        QMatrix4x4 scaleMatrix;
        scaleMatrix.scale(float(s->at(0)), float(s->at(1)), float(s->at(2)));
        return translationMatrix * rotationMatrix * scaleMatrix;
    }

    void parsePrimitive(const QJsonObject& primitive,
                        const QMatrix4x4& transform,
                        const QString& path)
    {
        const auto mode = jsonInteger(primitive.value(QStringLiteral("mode")));
        if ((primitive.contains(QStringLiteral("mode")) && !mode)
            || mode.value_or(4) != 4) {
            add(memberPath(path, QStringLiteral("mode")), QStringLiteral("only TRIANGLES primitives are supported"));
            return;
        }
        if (primitive.value(QStringLiteral("extensions")).toObject()
                .contains(QStringLiteral("KHR_draco_mesh_compression"))) {
            add(memberPath(path, QStringLiteral("extensions")), QStringLiteral("Draco-compressed primitives are not supported"));
            return;
        }
        const QJsonObject attributes = primitive.value(QStringLiteral("attributes")).toObject();
        const auto positionIndex = jsonInteger(attributes.value(QStringLiteral("POSITION")));
        if (!positionIndex) {
            add(memberPath(path, QStringLiteral("attributes.POSITION")), QStringLiteral("is required"));
            return;
        }
        const auto positions = accessor(*positionIndex, QStringLiteral("VEC3"), {5126},
                                        memberPath(path, QStringLiteral("attributes.POSITION")));
        if (!positions) return;
        if (vertices_.size() + positions->count > GlbMeshReader::MaxVertices) {
            add(path, QStringLiteral("combined scene vertex limit exceeded"));
            return;
        }

        const quint32 base = static_cast<quint32>(vertices_.size());
        for (qsizetype i = 0; i < positions->count; ++i) {
            const char* p = positions->data + i * positions->stride;
            QVector3D point(readFloat(p), readFloat(p + 4), readFloat(p + 8));
            if (!std::isfinite(point.x()) || !std::isfinite(point.y()) || !std::isfinite(point.z())) {
                add(memberPath(path, QStringLiteral("attributes.POSITION")), QStringLiteral("contains a non-finite vertex"));
                return;
            }
            point = transform.map(point) * 1000.0f; // glTF linear units are metres; DesignStudio stores mm.
            if (!std::isfinite(point.x()) || !std::isfinite(point.y()) || !std::isfinite(point.z())) {
                add(path, QStringLiteral("node transform produced a non-finite vertex"));
                return;
            }
            vertices_.push_back(point);
            if (!haveBounds_) {
                boundsMin_ = boundsMax_ = point;
                haveBounds_ = true;
            } else {
                boundsMin_.setX(std::min(boundsMin_.x(), point.x()));
                boundsMin_.setY(std::min(boundsMin_.y(), point.y()));
                boundsMin_.setZ(std::min(boundsMin_.z(), point.z()));
                boundsMax_.setX(std::max(boundsMax_.x(), point.x()));
                boundsMax_.setY(std::max(boundsMax_.y(), point.y()));
                boundsMax_.setZ(std::max(boundsMax_.z(), point.z()));
            }
        }

        QVector<quint32> primitiveIndices;
        if (primitive.contains(QStringLiteral("indices"))) {
            const auto indexAccessor = jsonInteger(primitive.value(QStringLiteral("indices")));
            if (!indexAccessor) {
                add(memberPath(path, QStringLiteral("indices")), QStringLiteral("must be an accessor index"));
                return;
            }
            const auto indices = accessor(*indexAccessor, QStringLiteral("SCALAR"),
                                          {5121, 5123, 5125},
                                          memberPath(path, QStringLiteral("indices")));
            if (!indices) return;
            primitiveIndices.reserve(indices->count);
            for (qsizetype i = 0; i < indices->count; ++i) {
                const quint32 value = readIndex(indices->data + i * indices->stride,
                                                indices->componentType);
                if (value >= static_cast<quint32>(positions->count)) {
                    add(memberPath(path, QStringLiteral("indices")), QStringLiteral("contains an out-of-range vertex index"));
                    return;
                }
                primitiveIndices.push_back(base + value);
            }
        } else {
            primitiveIndices.reserve(positions->count);
            for (qsizetype i = 0; i < positions->count; ++i)
                primitiveIndices.push_back(base + static_cast<quint32>(i));
        }
        if (primitiveIndices.size() % 3 != 0
            || indices_.size() / 3 + primitiveIndices.size() / 3 > GlbMeshReader::MaxTriangles) {
            add(path, QStringLiteral("triangle count is invalid or exceeds the limit"));
            return;
        }
        indices_ += primitiveIndices;
    }

    void visitNode(qsizetype nodeIndex,
                   const QMatrix4x4& parent,
                   int depth,
                   QSet<qsizetype>& active)
    {
        const QJsonArray nodes = root_.value(QStringLiteral("nodes")).toArray();
        const QString path = itemPath(QStringLiteral("$.nodes"), nodeIndex);
        if (nodeIndex < 0 || nodeIndex >= nodes.size() || !nodes.at(nodeIndex).isObject()) {
            add(path, QStringLiteral("node index is out of range"));
            return;
        }
        if (depth > MaxNodeDepth || active.contains(nodeIndex)) {
            add(path, QStringLiteral("node hierarchy is cyclic or too deep"));
            return;
        }
        active.insert(nodeIndex);
        const QJsonObject node = nodes.at(nodeIndex).toObject();
        const auto local = nodeTransform(node, path);
        if (!local) {
            active.remove(nodeIndex);
            return;
        }
        const QMatrix4x4 world = parent * *local;
        if (node.contains(QStringLiteral("mesh"))) {
            const auto meshIndex = jsonInteger(node.value(QStringLiteral("mesh")));
            const QJsonArray meshes = root_.value(QStringLiteral("meshes")).toArray();
            if (!meshIndex || *meshIndex >= meshes.size() || !meshes.at(*meshIndex).isObject()) {
                add(memberPath(path, QStringLiteral("mesh")), QStringLiteral("mesh index is out of range"));
            } else {
                const QJsonArray primitives = meshes.at(*meshIndex).toObject()
                                                  .value(QStringLiteral("primitives")).toArray();
                if (primitives.isEmpty()) {
                    add(itemPath(QStringLiteral("$.meshes"), *meshIndex), QStringLiteral("contains no primitives"));
                }
                for (qsizetype i = 0; i < primitives.size(); ++i) {
                    if (!primitives.at(i).isObject()) {
                        add(itemPath(memberPath(itemPath(QStringLiteral("$.meshes"), *meshIndex),
                                                QStringLiteral("primitives")), i),
                            QStringLiteral("must be an object"));
                        continue;
                    }
                    parsePrimitive(primitives.at(i).toObject(), world,
                                   itemPath(memberPath(itemPath(QStringLiteral("$.meshes"), *meshIndex),
                                                       QStringLiteral("primitives")), i));
                }
            }
        }
        const QJsonArray children = node.value(QStringLiteral("children")).toArray();
        for (qsizetype i = 0; i < children.size(); ++i) {
            const auto child = jsonInteger(children.at(i));
            if (!child) add(itemPath(memberPath(path, QStringLiteral("children")), i),
                            QStringLiteral("must be a node index"));
            else visitNode(*child, world, depth + 1, active);
        }
        active.remove(nodeIndex);
    }

    void parseScene()
    {
        const QJsonArray scenes = root_.value(QStringLiteral("scenes")).toArray();
        const qint64 sceneIndex = jsonInteger(root_.value(QStringLiteral("scene"))).value_or(0);
        if (scenes.isEmpty() || sceneIndex >= scenes.size() || !scenes.at(sceneIndex).isObject()) {
            add(QStringLiteral("$.scene"), QStringLiteral("default scene is missing or out of range"));
            return;
        }
        const QJsonArray roots = scenes.at(sceneIndex).toObject().value(QStringLiteral("nodes")).toArray();
        if (roots.isEmpty()) {
            add(itemPath(QStringLiteral("$.scenes"), sceneIndex), QStringLiteral("contains no root nodes"));
            return;
        }
        QSet<qsizetype> active;
        QMatrix4x4 identity;
        for (qsizetype i = 0; i < roots.size(); ++i) {
            const auto nodeIndex = jsonInteger(roots.at(i));
            if (!nodeIndex) add(itemPath(QStringLiteral("$.scenes[0].nodes"), i),
                                QStringLiteral("must be a node index"));
            else visitNode(*nodeIndex, identity, 0, active);
        }
    }

    const QByteArray& bytes_;
    QString sourceName_;
    QByteArray jsonBytes_;
    QByteArray binBytes_;
    int binChunkCount_ = 0;
    QJsonObject root_;
    QVector<GlbValidationIssue> issues_;
    QVector<QVector3D> vertices_;
    QVector<quint32> indices_;
    QVector3D boundsMin_;
    QVector3D boundsMax_;
    bool haveBounds_ = false;
};

GlbLoadResult oneIssue(const QString& path, const QString& message)
{
    GlbLoadResult result;
    result.issues.push_back({path, message});
    return result;
}

} // namespace

GlbMesh::GlbMesh(QVector<QVector3D> verticesMm,
                 QVector<quint32> triangleIndices,
                 const QVector3D& boundsMinMm,
                 const QVector3D& boundsMaxMm,
                 QString sha256,
                 QString sourceName,
                 qint64 sourceBytes)
    : verticesMm_(std::move(verticesMm)),
      triangleIndices_(std::move(triangleIndices)),
      boundsMinMm_(boundsMinMm),
      boundsMaxMm_(boundsMaxMm),
      sha256_(std::move(sha256)),
      sourceName_(std::move(sourceName)),
      sourceBytes_(sourceBytes)
{
}

GlbMesh::GlbMesh(QVector<QVector3D> verticesMm,
                 QVector<quint32> triangleIndices,
                 const QVector3D& boundsMinMm,
                 const QVector3D& boundsMaxMm,
                 QString sha256,
                 QString sourceName,
                 qint64 sourceBytes,
                 std::shared_ptr<const SemanticAssembly> assembly)
    : GlbMesh(std::move(verticesMm), std::move(triangleIndices), boundsMinMm,
              boundsMaxMm, std::move(sha256), std::move(sourceName), sourceBytes,
              std::move(assembly), {})
{
}

GlbMesh::GlbMesh(QVector<QVector3D> verticesMm,
                 QVector<quint32> triangleIndices,
                 const QVector3D& boundsMinMm,
                 const QVector3D& boundsMaxMm,
                 QString sha256,
                 QString sourceName,
                 qint64 sourceBytes,
                 std::shared_ptr<const SemanticAssembly> assembly,
                 QByteArray semanticAssemblyJson)
    : GlbMesh(std::move(verticesMm), std::move(triangleIndices), boundsMinMm,
              boundsMaxMm, std::move(sha256), std::move(sourceName), sourceBytes)
{
    assembly_ = std::move(assembly);
    semanticAssemblyJson_ = std::move(semanticAssemblyJson);
}

QString GlbLoadResult::errorSummary() const
{
    QStringList lines;
    for (const GlbValidationIssue& issue : issues)
        lines.push_back(issue.path + QStringLiteral(": ") + issue.message);
    return lines.join(QLatin1Char('\n'));
}

GlbLoadResult GlbMeshReader::loadFile(const QString& path)
{
    const QFileInfo info(path);
    if (!info.exists() || !info.isFile())
        return oneIssue(QStringLiteral("$"), QStringLiteral("GLB file does not exist or is not regular"));
    if (info.size() > MaxInputBytes)
        return oneIssue(QStringLiteral("$"), QStringLiteral("GLB exceeds the 256 MiB input limit"));
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly))
        return oneIssue(QStringLiteral("$"), QStringLiteral("cannot open GLB: %1").arg(file.errorString()));
    const QByteArray bytes = file.read(MaxInputBytes + 1);
    if (bytes.size() > MaxInputBytes)
        return oneIssue(QStringLiteral("$"), QStringLiteral("GLB exceeds the 256 MiB input limit"));
    if (file.error() != QFileDevice::NoError)
        return oneIssue(QStringLiteral("$"), QStringLiteral("cannot read GLB: %1").arg(file.errorString()));
    return loadBytes(bytes, path);
}

GlbLoadResult GlbMeshReader::loadBytes(const QByteArray& bytes, const QString& sourceName)
{
    if (bytes.size() > MaxInputBytes)
        return oneIssue(QStringLiteral("$"), QStringLiteral("GLB exceeds the 256 MiB input limit"));
    Parser parser(bytes, sourceName);
    return parser.parse();
}

} // namespace designstudio
