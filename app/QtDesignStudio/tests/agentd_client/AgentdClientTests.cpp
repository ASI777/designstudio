#include "AgentdClient.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTemporaryDir>
#include <QTimer>

#include <cstdio>

namespace {
bool write(const QString& path, const QByteArray& bytes) {
    QFile file(path);
    return file.open(QIODevice::WriteOnly) && file.write(bytes) == bytes.size();
}
}

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    if (argc != 2) return 2;
    QTemporaryDir temporary;
    if (!temporary.isValid()) return 3;
    QDir root(temporary.path());
    if (!root.mkpath(QStringLiteral("mechanical"))
        || !root.mkpath(QStringLiteral("electronics"))) return 4;
    if (!write(root.filePath(QStringLiteral("mechanical/product.FCStd")), "fixture\n")
        || !write(root.filePath(QStringLiteral("electronics/product.dsproj")), "{}\n")) return 5;

    const QJsonObject manifest{
        {QStringLiteral("schema"), QStringLiteral("design-studio.workspace/1")},
        {QStringLiteral("workspace_id"), QStringLiteral("64f7bfe7-d053-4c97-b8a7-c63d8995e31d")},
        {QStringLiteral("revision"), 3},
        {QStringLiteral("product"), QJsonObject{{QStringLiteral("name"), QStringLiteral("Recovery fixture")}}},
        {QStringLiteral("documents"), QJsonObject{
            {QStringLiteral("mechanical"), QStringLiteral("mechanical/product.FCStd")},
            {QStringLiteral("electronics"), QStringLiteral("electronics/product.dsproj")},
        }},
        {QStringLiteral("product_graph"), QStringLiteral("product-graph.json")},
        {QStringLiteral("baseline_configuration"), QStringLiteral("configurations/baseline.json")},
    };
    const QString manifestPath = root.filePath(QStringLiteral("manifest.json"));
    if (!write(manifestPath, QJsonDocument(manifest).toJson())) return 6;

    const QString marker = root.filePath(QStringLiteral("first-process.marker"));
    qputenv("DESIGNSTUDIO_AGENTD", argv[1]);
    qputenv("DESIGNSTUDIO_AGENTD_TEST_MARKER", marker.toUtf8());

    AgentdClient client;
    int opened = 0;
    bool recovered = false;
    bool failed = false;
    QObject::connect(&client, &AgentdClient::workspaceOpened, &app,
        [&](const QString&, qint64 workspaceRevision, qint64 graphRevision,
            qint64 daemonRevision, bool wasRecovered) {
        ++opened;
        recovered = recovered || wasRecovered;
        if (workspaceRevision != 3 || graphRevision != 7 || daemonRevision < 11)
            failed = true;
        if (opened == 2) app.quit();
    });
    QObject::connect(&client, &AgentdClient::requestFailed, &app,
                     [&](const QString&, const QString&) { failed = true; app.quit(); });
    QTimer::singleShot(8000, &app, [&] { failed = true; app.quit(); });

    QString error;
    if (!client.openWorkspace(manifestPath, &error)) {
        std::fprintf(stderr, "%s\n", error.toUtf8().constData());
        return 7;
    }
    app.exec();
    if (failed || opened != 2 || !recovered
        || client.state() != AgentdClient::State::Ready) return 8;
    std::puts("AGENTD_STARTUP_RECONNECT_RECOVERY_OK");
    return 0;
}
