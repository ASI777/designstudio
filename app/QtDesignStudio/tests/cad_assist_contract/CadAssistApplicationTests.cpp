#include "CadAssistProjectBinding.h"
#include "ProjectModel.h"

#include <QCoreApplication>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTemporaryDir>

#include <iostream>

namespace {

bool check(bool condition, const QString& message)
{
    if (condition) return true;
    std::cerr << "FAIL: " << message.toStdString() << '\n';
    return false;
}

bool writeJson(const QString& path, const QJsonObject& object)
{
    QFile file(path);
    return file.open(QIODevice::WriteOnly | QIODevice::Truncate)
        && file.write(QJsonDocument(object).toJson()) >= 0;
}

QJsonObject projectFixture()
{
    return QJsonObject{
        {"version", 2},
        {"document_id", "motor-controller-r1"},
        {"revision", 42},
        {"board_width_mm", 100.0},
        {"board_height_mm", 80.0},
        {"grid_mm", 1.0},
        {"copper_layers", 2},
        {"nets", QJsonValue::Null},
        {"net_table", QJsonArray{}},
        {"net_classes", QJsonArray{}},
        {"footprints", QJsonArray{}},
        {"traces", QJsonArray{}},
        {"vias", QJsonArray{}},
    };
}

QJsonObject readObject(const QString& path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) return {};
    return QJsonDocument::fromJson(file.readAll()).object();
}

bool run(const QString& fixtureDir)
{
    QTemporaryDir temporary;
    if (!check(temporary.isValid(), "temporary directory unavailable")) return false;

    const QString projectPath = temporary.path() + "/project.dsproj";
    const QString contractPath = temporary.path() + "/contract.json";
    if (!check(writeJson(projectPath, projectFixture()), "cannot write project fixture")) return false;

    ProjectModel project;
    if (!check(project.loadFromFile(projectPath), project.lastError())) return false;
    if (!check(project.documentId() == "motor-controller-r1" && project.revision() == 42,
               "project identity did not load")) return false;

    QJsonObject contract = readObject(fixtureDir + "/valid-session.json");
    QJsonObject binding = contract.value("project").toObject();
    binding["base_sha256"] = project.fileSha256();
    contract["project"] = binding;
    if (!check(writeJson(contractPath, contract), "cannot write bound contract")) return false;

    auto result = designstudio::loadCadAssistForProject(contractPath, project);
    if (!check(result.ok(), result.errorSummary())) return false;
    if (!check(result.session->baseSha256() == project.fileSha256(),
               "connected session lost project SHA-256")) return false;

    QFile externallyChanged(projectPath);
    if (!check(externallyChanged.open(QIODevice::Append),
               "cannot open project for external rewrite test")) return false;
    externallyChanged.write(" \n");
    externallyChanged.close();
    result = designstudio::loadCadAssistForProject(contractPath, project);
    if (!check(!result.ok() && result.errorSummary().contains("changed on disk"),
               "external project rewrite did not invalidate evidence")) return false;
    if (!check(writeJson(projectPath, projectFixture()) && project.loadFromFile(projectPath),
               "cannot restore project after external rewrite test")) return false;

    const QString originalDocumentId = project.documentId();
    const qint64 originalRevision = project.revision();
    project.setModified(true);
    result = designstudio::loadCadAssistForProject(contractPath, project);
    if (!check(!result.ok() && result.errorSummary().contains("save current project changes"),
               "modified project did not reject evidence")) return false;
    if (!check(project.documentId() == originalDocumentId
                   && project.revision() == originalRevision + 1,
               "rejected evidence changed project identity")) return false;

    if (!check(project.saveToFile(projectPath), project.lastError())) return false;
    result = designstudio::loadCadAssistForProject(contractPath, project);
    if (!check(!result.ok()
                   && (result.errorSummary().contains("base revision")
                       || result.errorSummary().contains("base SHA-256")),
               "stale evidence was accepted after project save")) return false;

    binding = contract.value("project").toObject();
    binding["base_revision"] = static_cast<double>(project.revision());
    binding["base_sha256"] = project.fileSha256();
    contract["project"] = binding;
    if (!check(writeJson(contractPath, contract), "cannot update contract binding")) return false;
    result = designstudio::loadCadAssistForProject(contractPath, project);
    return check(result.ok(), result.errorSummary());
}

} // namespace

int main(int argc, char** argv)
{
    QCoreApplication application(argc, argv);
    if (application.arguments().size() != 2) {
        std::cerr << "usage: cad_assist_application_tests <fixture-dir>\n";
        return 2;
    }
    return run(application.arguments().at(1)) ? 0 : 1;
}
