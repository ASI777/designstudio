#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>

#include <iostream>

int main() {
    const QString marker = qEnvironmentVariable("DESIGNSTUDIO_AGENTD_TEST_MARKER");
    const bool firstProcess = !marker.isEmpty() && !QFileInfo::exists(marker);
    if (firstProcess) {
        QFile file(marker);
        if (!file.open(QIODevice::WriteOnly)) return 3;
        file.write("first\n");
    }

    std::string line;
    while (std::getline(std::cin, line)) {
        const QJsonObject request = QJsonDocument::fromJson(
            QByteArray::fromStdString(line)).object();
        const QJsonObject params = request.value(QStringLiteral("params")).toObject();
        QFile manifest(params.value(QStringLiteral("manifest_path")).toString());
        if (!manifest.open(QIODevice::ReadOnly)) return 4;
        const QJsonObject workspace = QJsonDocument::fromJson(manifest.readAll()).object();
        const QJsonObject response{
            {QStringLiteral("jsonrpc"), QStringLiteral("2.0")},
            {QStringLiteral("id"), request.value(QStringLiteral("id"))},
            {QStringLiteral("result"), QJsonObject{
                {QStringLiteral("api"), QStringLiteral("design-studio.agentd/1")},
                {QStringLiteral("workspace_id"), workspace.value(QStringLiteral("workspace_id"))},
                {QStringLiteral("workspace_revision"), workspace.value(QStringLiteral("revision"))},
                {QStringLiteral("graph_revision"), 7},
                {QStringLiteral("daemon_revision"), firstProcess ? 11 : 12},
            }},
        };
        std::cout << QJsonDocument(response).toJson(QJsonDocument::Compact).constData()
                  << std::endl;
        if (firstProcess) return 42; // prove supervised crash recovery
    }
    return 0;
}
