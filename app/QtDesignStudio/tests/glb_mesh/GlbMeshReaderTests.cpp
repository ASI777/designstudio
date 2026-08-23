#include "GlbMeshReader.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include <cmath>
#include <cstring>
#include <iostream>

using designstudio::GlbMeshReader;

namespace {

void appendU32(QByteArray& bytes, quint32 value)
{
    bytes.append(char(value & 0xff));
    bytes.append(char((value >> 8) & 0xff));
    bytes.append(char((value >> 16) & 0xff));
    bytes.append(char((value >> 24) & 0xff));
}

void appendFloat(QByteArray& bytes, float value)
{
    quint32 bits = 0;
    static_assert(sizeof(bits) == sizeof(value));
    std::memcpy(&bits, &value, sizeof(bits));
    appendU32(bytes, bits);
}

QByteArray fixture(bool translated = false, bool external = false, quint16 lastIndex = 2)
{
    QByteArray bin;
    for (float coordinate : {0.0f, 0.0f, 0.0f,
                             0.1f, 0.0f, 0.0f,
                             0.0f, 0.2f, 0.0f}) appendFloat(bin, coordinate);
    bin.append(char(0)); bin.append(char(0));
    bin.append(char(1)); bin.append(char(0));
    bin.append(char(lastIndex & 0xff)); bin.append(char((lastIndex >> 8) & 0xff));

    QJsonObject buffer{{"byteLength", bin.size()}};
    if (external) buffer["uri"] = QStringLiteral("data:application/octet-stream;base64,AA==");
    QJsonObject node{{"mesh", 0}};
    if (translated) node["translation"] = QJsonArray{1.0, 2.0, 3.0};
    const QJsonObject root{
        {"asset", QJsonObject{{"version", "2.0"}}},
        {"buffers", QJsonArray{buffer}},
        {"bufferViews", QJsonArray{
            QJsonObject{{"buffer", 0}, {"byteOffset", 0}, {"byteLength", 36}},
            QJsonObject{{"buffer", 0}, {"byteOffset", 36}, {"byteLength", 6}},
        }},
        {"accessors", QJsonArray{
            QJsonObject{{"bufferView", 0}, {"componentType", 5126},
                        {"count", 3}, {"type", "VEC3"}},
            QJsonObject{{"bufferView", 1}, {"componentType", 5123},
                        {"count", 3}, {"type", "SCALAR"}},
        }},
        {"meshes", QJsonArray{QJsonObject{{"primitives", QJsonArray{
            QJsonObject{{"attributes", QJsonObject{{"POSITION", 0}}}, {"indices", 1}}
        }}}}},
        {"nodes", QJsonArray{node}},
        {"scenes", QJsonArray{QJsonObject{{"nodes", QJsonArray{0}}}}},
        {"scene", 0},
    };
    QByteArray json = QJsonDocument(root).toJson(QJsonDocument::Compact);
    while (json.size() % 4) json.append(' ');
    while (bin.size() % 4) bin.append(char(0));

    QByteArray glb;
    appendU32(glb, 0x46546c67); appendU32(glb, 2);
    appendU32(glb, quint32(12 + 8 + json.size() + 8 + bin.size()));
    appendU32(glb, json.size()); appendU32(glb, 0x4e4f534a); glb += json;
    appendU32(glb, bin.size()); appendU32(glb, 0x004e4942); glb += bin;
    return glb;
}

bool check(bool condition, const char* message)
{
    if (condition) return true;
    std::cerr << "FAIL: " << message << '\n';
    return false;
}

bool close(float actual, float expected)
{
    return std::abs(actual - expected) < 0.01f;
}

bool positive()
{
    const QByteArray bytes = fixture();
    const auto loaded = GlbMeshReader::loadBytes(bytes, QStringLiteral("triangle.glb"));
    return check(loaded.ok(), "valid GLB rejected")
        && check(loaded.mesh->verticesMm().size() == 3, "wrong vertex count")
        && check(loaded.mesh->triangleIndices().size() == 3, "wrong index count")
        && check(close(loaded.mesh->sizeMm().x(), 100.0f)
                     && close(loaded.mesh->sizeMm().y(), 200.0f), "metres not converted to mm")
        && check(loaded.mesh->sha256() == QString::fromLatin1(
                     QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex()),
                 "source digest mismatch");
}

bool transforms()
{
    const auto loaded = GlbMeshReader::loadBytes(fixture(true));
    return check(loaded.ok(), "translated GLB rejected")
        && check(close(loaded.mesh->boundsMinMm().x(), 1000.0f)
                     && close(loaded.mesh->boundsMinMm().y(), 2000.0f)
                     && close(loaded.mesh->boundsMinMm().z(), 3000.0f),
                 "node translation was not applied before mm conversion");
}

bool negative()
{
    QByteArray badMagic = fixture(); badMagic[0] = 'x';
    const auto magic = GlbMeshReader::loadBytes(badMagic);
    const auto index = GlbMeshReader::loadBytes(fixture(false, false, 7));
    const auto external = GlbMeshReader::loadBytes(fixture(false, true));
    return check(!magic.ok() && magic.errorSummary().contains("magic"), "bad magic accepted")
        && check(!index.ok() && index.errorSummary().contains("out-of-range"), "bad index accepted")
        && check(!external.ok() && external.errorSummary().contains("URI"), "external URI accepted");
}

} // namespace

int main(int argc, char** argv)
{
    QCoreApplication app(argc, argv);
    const QStringList args = app.arguments();
    if (args.size() >= 3 && args.at(1) == QStringLiteral("write-fixture")) {
        QFile output(args.at(2));
        return output.open(QIODevice::WriteOnly | QIODevice::Truncate)
                && output.write(fixture()) > 0 ? 0 : 1;
    }
    if (args.size() != 2) return 2;
    if (args.at(1) == QStringLiteral("positive")) return positive() ? 0 : 1;
    if (args.at(1) == QStringLiteral("transforms")) return transforms() ? 0 : 1;
    if (args.at(1) == QStringLiteral("negative")) return negative() ? 0 : 1;
    return 2;
}
