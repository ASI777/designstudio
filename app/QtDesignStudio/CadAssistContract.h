#pragma once

#include <QByteArray>
#include <QJsonDocument>
#include <QString>
#include <QStringList>
#include <QVector>

#include <memory>
#include <optional>

namespace designstudio {

struct CadAssistProjectExpectation {
    std::optional<QString> documentId;
    std::optional<qint64> baseRevision;
    std::optional<QString> baseSha256;
};

struct CadAssistValidationIssue {
    QString path;
    QString message;
};

// A successfully constructed session is an immutable, independently validated
// snapshot. Callers can copy the JSON document for downstream translation, but
// cannot change the snapshot held by this object.
class CadAssistSession final {
public:
    const QString& schema() const noexcept { return schema_; }
    const QString& sessionId() const noexcept { return sessionId_; }
    qint64 revision() const noexcept { return revision_; }
    const QString& documentId() const noexcept { return documentId_; }
    qint64 baseRevision() const noexcept { return baseRevision_; }
    const QString& baseSha256() const noexcept { return baseSha256_; }
    const QString& sourceSha256() const noexcept { return sourceSha256_; }
    int pageCount() const noexcept { return pageCount_; }

    const QStringList& calibrationIds() const noexcept { return calibrationIds_; }
    const QStringList& viewboxIds() const noexcept { return viewboxIds_; }
    const QStringList& targetIds() const noexcept { return targetIds_; }
    const QStringList& elementIds() const noexcept { return elementIds_; }
    const QStringList& deviationIds() const noexcept { return deviationIds_; }

    // QJsonDocument is implicitly shared; returning by value preserves the
    // immutable session while allowing an adapter to consume its own copy.
    QJsonDocument documentSnapshot() const { return document_; }

private:
    friend class CadAssistContractReader;

    CadAssistSession(QJsonDocument document,
                     QString schema,
                     QString sessionId,
                     qint64 revision,
                     QString documentId,
                     qint64 baseRevision,
                     QString baseSha256,
                     QString sourceSha256,
                     int pageCount,
                     QStringList calibrationIds,
                     QStringList viewboxIds,
                     QStringList targetIds,
                     QStringList elementIds,
                     QStringList deviationIds);

    QJsonDocument document_;
    QString schema_;
    QString sessionId_;
    qint64 revision_ = 0;
    QString documentId_;
    qint64 baseRevision_ = 0;
    QString baseSha256_;
    QString sourceSha256_;
    int pageCount_ = 0;
    QStringList calibrationIds_;
    QStringList viewboxIds_;
    QStringList targetIds_;
    QStringList elementIds_;
    QStringList deviationIds_;
};

struct CadAssistLoadResult {
    std::shared_ptr<const CadAssistSession> session;
    QVector<CadAssistValidationIssue> issues;

    bool ok() const noexcept { return session != nullptr && issues.isEmpty(); }
    QString errorSummary() const;
};

class CadAssistContractReader final {
public:
    static constexpr qint64 MaxInputBytes = 50LL * 1024LL * 1024LL;
    static constexpr const char* ExpectedSchema =
        "urn:design-studio:schema:cad-assist-session:1";

    static CadAssistLoadResult loadFile(
        const QString& path,
        const CadAssistProjectExpectation& expectedProject = {});

    static CadAssistLoadResult loadBytes(
        const QByteArray& bytes,
        const CadAssistProjectExpectation& expectedProject = {},
        const QString& sourceName = QStringLiteral("<memory>"));

private:
    CadAssistContractReader() = delete;
};

} // namespace designstudio
