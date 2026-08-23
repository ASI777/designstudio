#include "CadAssistProjectBinding.h"

#include "ProjectModel.h"

#include <QCryptographicHash>
#include <QFile>

namespace designstudio {
namespace {

CadAssistLoadResult projectIssue(const QString& message)
{
    CadAssistLoadResult result;
    result.issues.push_back({QStringLiteral("$.project"), message});
    return result;
}

} // namespace

CadAssistLoadResult loadCadAssistForProject(const QString& contractPath,
                                            const ProjectModel& project)
{
    if (project.filePath().isEmpty()) {
        return projectIssue(QStringLiteral(
            "save the DesignStudio project before loading CAD-assist evidence"));
    }
    if (project.isModified()) {
        return projectIssue(QStringLiteral(
            "save current project changes before loading CAD-assist evidence"));
    }
    if (!project.hasPersistedIdentity()) {
        return projectIssue(QStringLiteral(
            "save the legacy project once to persist its CAD-assist identity"));
    }
    if (project.documentId().isEmpty() || project.fileSha256().isEmpty()) {
        return projectIssue(QStringLiteral(
            "saved project identity or SHA-256 is unavailable"));
    }

    QFile savedProject(project.filePath());
    if (!savedProject.open(QIODevice::ReadOnly)) {
        return projectIssue(QStringLiteral(
            "cannot re-read the saved project to verify its SHA-256"));
    }
    QCryptographicHash digest(QCryptographicHash::Sha256);
    if (!digest.addData(&savedProject)
        || QString::fromLatin1(digest.result().toHex()) != project.fileSha256()) {
        return projectIssue(QStringLiteral(
            "the project changed on disk; explicitly reload or save before loading evidence"));
    }

    CadAssistProjectExpectation expected;
    expected.documentId = project.documentId();
    expected.baseRevision = project.revision();
    expected.baseSha256 = project.fileSha256();
    return CadAssistContractReader::loadFile(contractPath, expected);
}

} // namespace designstudio
