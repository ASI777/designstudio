#include "CadAssistContract.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QJsonParseError>
#include <QRegularExpression>
#include <QSet>

#include <cmath>
#include <initializer_list>
#include <limits>
#include <utility>

namespace designstudio {
namespace {

constexpr double kMaxExactJsonInteger = 9007199254740991.0; // 2^53 - 1

QString memberPath(const QString& parent, const QString& member)
{
    return parent + QLatin1Char('.') + member;
}

QString itemPath(const QString& parent, qsizetype index)
{
    return parent + QLatin1Char('[') + QString::number(index) + QLatin1Char(']');
}

class Validator final {
public:
    Validator(const QJsonObject& root, const CadAssistProjectExpectation& expected)
        : root_(root), expected_(expected)
    {
    }

    void run()
    {
        validateRoot();
    }

    QVector<CadAssistValidationIssue> issues;
    QString schema;
    QString sessionId;
    qint64 revision = 0;
    QString documentId;
    qint64 baseRevision = 0;
    QString baseSha256;
    QString sourceSha256;
    int pageCount = 0;
    QStringList calibrationIds;
    QStringList viewboxIds;
    QStringList targetIds;
    QStringList elementIds;
    QStringList deviationIds;

private:
    struct PageRef {
        bool valid = false;
        int page = -1;
    };

    void add(const QString& path, const QString& message)
    {
        issues.push_back({path, message});
    }

    void shape(const QJsonObject& object,
               const QString& path,
               std::initializer_list<const char*> required,
               std::initializer_list<const char*> allowed)
    {
        QSet<QString> allowedSet;
        for (const char* key : allowed) {
            allowedSet.insert(QString::fromLatin1(key));
        }
        for (const char* key : required) {
            const QString name = QString::fromLatin1(key);
            if (!object.contains(name)) {
                add(memberPath(path, name), QStringLiteral("missing required member"));
            }
        }
        for (auto it = object.constBegin(); it != object.constEnd(); ++it) {
            if (!allowedSet.contains(it.key())) {
                add(memberPath(path, it.key()), QStringLiteral("unknown member"));
            }
        }
    }

    std::optional<QJsonObject> object(const QJsonValue& value, const QString& path)
    {
        if (value.isUndefined()) {
            return std::nullopt;
        }
        if (!value.isObject()) {
            add(path, QStringLiteral("must be an object"));
            return std::nullopt;
        }
        return value.toObject();
    }

    std::optional<QJsonArray> array(const QJsonValue& value, const QString& path)
    {
        if (value.isUndefined()) {
            return std::nullopt;
        }
        if (!value.isArray()) {
            add(path, QStringLiteral("must be an array"));
            return std::nullopt;
        }
        return value.toArray();
    }

    std::optional<QString> string(const QJsonValue& value,
                                  const QString& path,
                                  qsizetype minimum = 0,
                                  qsizetype maximum = std::numeric_limits<int>::max())
    {
        if (value.isUndefined()) {
            return std::nullopt;
        }
        if (!value.isString()) {
            add(path, QStringLiteral("must be a string"));
            return std::nullopt;
        }
        const QString result = value.toString();
        if (result.size() < minimum || result.size() > maximum) {
            add(path, QStringLiteral("string length is outside the permitted range"));
            return std::nullopt;
        }
        return result;
    }

    std::optional<bool> boolean(const QJsonValue& value, const QString& path)
    {
        if (value.isUndefined()) {
            return std::nullopt;
        }
        if (!value.isBool()) {
            add(path, QStringLiteral("must be a boolean"));
            return std::nullopt;
        }
        return value.toBool();
    }

    std::optional<double> number(const QJsonValue& value, const QString& path)
    {
        if (value.isUndefined()) {
            return std::nullopt;
        }
        if (!value.isDouble()) {
            add(path, QStringLiteral("must be a number"));
            return std::nullopt;
        }
        const double result = value.toDouble();
        if (!std::isfinite(result)) {
            add(path, QStringLiteral("must be finite"));
            return std::nullopt;
        }
        return result;
    }

    std::optional<qint64> integer(const QJsonValue& value, const QString& path)
    {
        const auto result = number(value, path);
        if (!result) {
            return std::nullopt;
        }
        if (std::floor(*result) != *result || std::abs(*result) > kMaxExactJsonInteger) {
            add(path, QStringLiteral("must be an exactly represented integer"));
            return std::nullopt;
        }
        return static_cast<qint64>(*result);
    }

    bool oneOf(const QString& value,
               const QString& path,
               std::initializer_list<const char*> allowed)
    {
        for (const char* candidate : allowed) {
            if (value == QLatin1String(candidate)) {
                return true;
            }
        }
        add(path, QStringLiteral("has an unsupported value"));
        return false;
    }

    bool exactString(const QJsonValue& value, const QString& path, const char* expected)
    {
        const auto result = string(value, path);
        if (!result) {
            return false;
        }
        if (*result != QLatin1String(expected)) {
            add(path, QStringLiteral("must equal '%1'").arg(QLatin1String(expected)));
            return false;
        }
        return true;
    }

    bool uuid(const QString& value, const QString& path)
    {
        static const QRegularExpression expression(
            QStringLiteral("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                           "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"));
        if (!expression.match(value).hasMatch()) {
            add(path, QStringLiteral("must be a canonical UUID"));
            return false;
        }
        return true;
    }

    bool stableId(const QString& value, const QString& path)
    {
        static const QRegularExpression expression(
            QStringLiteral("^[A-Za-z][A-Za-z0-9._:-]{0,127}$"));
        if (!expression.match(value).hasMatch()) {
            add(path, QStringLiteral("must be a stable identifier"));
            return false;
        }
        return true;
    }

    bool sha256(const QString& value, const QString& path)
    {
        static const QRegularExpression expression(QStringLiteral("^[a-f0-9]{64}$"));
        if (!expression.match(value).hasMatch()) {
            add(path, QStringLiteral("must be a lowercase SHA-256 digest"));
            return false;
        }
        return true;
    }

    std::optional<QString> recordId(const QJsonValue& value,
                                    const QString& path,
                                    const QString& kind)
    {
        const auto id = string(value, path);
        if (!id || !uuid(*id, path)) {
            return std::nullopt;
        }
        if (allRecordIds_.contains(*id)) {
            add(path,
                QStringLiteral("duplicate record ID '%1' (already used by %2)")
                    .arg(*id, allRecordIds_.value(*id)));
            return std::nullopt;
        }
        allRecordIds_.insert(*id, kind);
        return id;
    }

    PageRef recordPage(const QJsonValue& value, const QString& path)
    {
        const auto parsed = integer(value, path);
        if (!parsed) {
            return {};
        }
        if (*parsed < 0 || pageCount <= 0 || *parsed >= pageCount) {
            add(path,
                QStringLiteral("page index %1 is outside source page range [0, %2)")
                    .arg(*parsed)
                    .arg(pageCount));
            return {};
        }
        return {true, static_cast<int>(*parsed)};
    }

    std::optional<QVector<double>> fixedNumbers(const QJsonValue& value,
                                                const QString& path,
                                                qsizetype count)
    {
        const auto values = array(value, path);
        if (!values) {
            return std::nullopt;
        }
        if (values->size() != count) {
            add(path, QStringLiteral("must contain exactly %1 numbers").arg(count));
            return std::nullopt;
        }
        QVector<double> result;
        result.reserve(count);
        bool valid = true;
        for (qsizetype i = 0; i < values->size(); ++i) {
            const auto item = number(values->at(i), itemPath(path, i));
            if (item) {
                result.push_back(*item);
            } else {
                valid = false;
            }
        }
        return valid ? std::optional<QVector<double>>(std::move(result)) : std::nullopt;
    }

    std::optional<QVector<double>> point2(const QJsonValue& value, const QString& path)
    {
        return fixedNumbers(value, path, 2);
    }

    std::optional<QVector<double>> point3(const QJsonValue& value, const QString& path)
    {
        return fixedNumbers(value, path, 3);
    }

    void rect4(const QJsonValue& value, const QString& path)
    {
        const auto rect = fixedNumbers(value, path, 4);
        if (rect && ((*rect)[2] <= (*rect)[0] || (*rect)[3] <= (*rect)[1])) {
            add(path, QStringLiteral("rectangle must have positive width and height"));
        }
    }

    void nullableNumber(const QJsonValue& value,
                        const QString& path,
                        bool nonnegative = false)
    {
        if (value.isNull()) {
            return;
        }
        const auto parsed = number(value, path);
        if (parsed && nonnegative && *parsed < 0.0) {
            add(path, QStringLiteral("must be nonnegative or null"));
        }
    }

    void stringArray(const QJsonValue& value,
                     const QString& path,
                     qsizetype itemMaximum)
    {
        const auto values = array(value, path);
        if (!values) {
            return;
        }
        for (qsizetype i = 0; i < values->size(); ++i) {
            string(values->at(i), itemPath(path, i), 0, itemMaximum);
        }
    }

    void validateRoot()
    {
        shape(root_,
              QStringLiteral("$"),
              {"schema", "producer", "created_at", "session_id", "revision",
               "idempotency_key", "status", "geometry_units",
               "canonical_scale_nm_per_mm", "project", "source", "calibrations",
               "viewboxes", "targets", "elements", "deviations", "dataset_policy",
               "producer_validation"},
              {"schema", "producer", "created_at", "session_id", "revision",
               "idempotency_key", "status", "geometry_units",
               "canonical_scale_nm_per_mm", "project", "source", "calibrations",
               "viewboxes", "targets", "elements", "deviations", "dataset_policy",
               "producer_validation"});

        if (const auto parsed = string(root_.value(QStringLiteral("schema")),
                                       QStringLiteral("$.schema"))) {
            schema = *parsed;
            if (schema != QLatin1String(CadAssistContractReader::ExpectedSchema)) {
                add(QStringLiteral("$.schema"),
                    QStringLiteral("unsupported schema; only exact major '%1' is accepted")
                        .arg(QLatin1String(CadAssistContractReader::ExpectedSchema)));
            }
        }

        validateProducer(root_.value(QStringLiteral("producer")),
                         QStringLiteral("$.producer"));
        validateCreatedAt(root_.value(QStringLiteral("created_at")),
                          QStringLiteral("$.created_at"));

        if (const auto parsed = string(root_.value(QStringLiteral("session_id")),
                                       QStringLiteral("$.session_id"))) {
            sessionId = *parsed;
            uuid(*parsed, QStringLiteral("$.session_id"));
        }
        if (const auto parsed = integer(root_.value(QStringLiteral("revision")),
                                        QStringLiteral("$.revision"))) {
            revision = *parsed;
            if (*parsed < 1) {
                add(QStringLiteral("$.revision"), QStringLiteral("must be at least 1"));
            }
        }
        if (const auto parsed = string(root_.value(QStringLiteral("idempotency_key")),
                                       QStringLiteral("$.idempotency_key"))) {
            uuid(*parsed, QStringLiteral("$.idempotency_key"));
        }
        if (const auto parsed = string(root_.value(QStringLiteral("status")),
                                       QStringLiteral("$.status"))) {
            oneOf(*parsed, QStringLiteral("$.status"), {"draft", "ready", "invalid"});
        }
        exactString(root_.value(QStringLiteral("geometry_units")),
                    QStringLiteral("$.geometry_units"),
                    "mm");
        if (const auto scale = integer(root_.value(QStringLiteral("canonical_scale_nm_per_mm")),
                                       QStringLiteral("$.canonical_scale_nm_per_mm"))) {
            if (*scale != 1000000) {
                add(QStringLiteral("$.canonical_scale_nm_per_mm"),
                    QStringLiteral("must equal 1000000"));
            }
        }

        validateProject(root_.value(QStringLiteral("project")), QStringLiteral("$.project"));
        validateSource(root_.value(QStringLiteral("source")), QStringLiteral("$.source"));
        validateCalibrations(root_.value(QStringLiteral("calibrations")),
                             QStringLiteral("$.calibrations"));
        validateViewboxes(root_.value(QStringLiteral("viewboxes")),
                          QStringLiteral("$.viewboxes"));
        validateTargets(root_.value(QStringLiteral("targets")), QStringLiteral("$.targets"));
        validateElements(root_.value(QStringLiteral("elements")), QStringLiteral("$.elements"));
        validateDeviations(root_.value(QStringLiteral("deviations")),
                           QStringLiteral("$.deviations"));
        validateDatasetPolicy(root_.value(QStringLiteral("dataset_policy")),
                              QStringLiteral("$.dataset_policy"));
        validateProducerValidation(root_.value(QStringLiteral("producer_validation")),
                                   QStringLiteral("$.producer_validation"));

        if (expected_.documentId && documentId != *expected_.documentId) {
            add(QStringLiteral("$.project.document_id"),
                QStringLiteral("document ID does not match the open project"));
        }
        if (expected_.baseRevision && baseRevision != *expected_.baseRevision) {
            add(QStringLiteral("$.project.base_revision"),
                QStringLiteral("base revision does not match the open project"));
        }
        if (expected_.baseSha256 && baseSha256 != *expected_.baseSha256) {
            add(QStringLiteral("$.project.base_sha256"),
                QStringLiteral("base SHA-256 does not match the saved open project"));
        }
    }

    void validateProducer(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"name", "version", "runtime"},
              {"name", "version", "runtime"});
        exactString(parsed->value(QStringLiteral("name")), memberPath(path, QStringLiteral("name")),
                    "cad-visual-helper");
        string(parsed->value(QStringLiteral("version")),
               memberPath(path, QStringLiteral("version")), 1, 64);
        string(parsed->value(QStringLiteral("runtime")),
               memberPath(path, QStringLiteral("runtime")), 1, 128);
    }

    void validateCreatedAt(const QJsonValue& value, const QString& path)
    {
        const auto parsed = string(value, path);
        if (!parsed) {
            return;
        }
        static const QRegularExpression rfc3339(
            QStringLiteral("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}"
                           "(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})$"));
        if (!rfc3339.match(*parsed).hasMatch()
            || !QDateTime::fromString(*parsed, Qt::ISODateWithMs).isValid()) {
            add(path, QStringLiteral("must be an RFC 3339 date-time with an offset"));
        }
    }

    void validateProject(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"document_id", "base_revision"},
              {"document_id", "base_revision", "base_sha256", "object_id"});
        if (const auto id = string(parsed->value(QStringLiteral("document_id")),
                                   memberPath(path, QStringLiteral("document_id")))) {
            documentId = *id;
            stableId(*id, memberPath(path, QStringLiteral("document_id")));
        }
        if (const auto base = integer(parsed->value(QStringLiteral("base_revision")),
                                      memberPath(path, QStringLiteral("base_revision")))) {
            baseRevision = *base;
            if (*base < 0) {
                add(memberPath(path, QStringLiteral("base_revision")),
                    QStringLiteral("must be nonnegative"));
            }
        }
        if (parsed->contains(QStringLiteral("base_sha256"))) {
            if (const auto digest = string(parsed->value(QStringLiteral("base_sha256")),
                                           memberPath(path, QStringLiteral("base_sha256")))) {
                sha256(*digest, memberPath(path, QStringLiteral("base_sha256")));
                baseSha256 = *digest;
            }
        }
        if (parsed->contains(QStringLiteral("object_id"))) {
            if (const auto id = string(parsed->value(QStringLiteral("object_id")),
                                       memberPath(path, QStringLiteral("object_id")))) {
                if (stableId(*id, memberPath(path, QStringLiteral("object_id")))) {
                    projectObjectId_ = *id;
                }
            }
        }
    }

    void validateSource(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"sha256", "original_name", "page_count", "coordinate_system", "pages"},
              {"sha256", "original_name", "page_count", "coordinate_system", "pages"});
        if (const auto digest = string(parsed->value(QStringLiteral("sha256")),
                                       memberPath(path, QStringLiteral("sha256")))) {
            sourceSha256 = *digest;
            sha256(*digest, memberPath(path, QStringLiteral("sha256")));
        }
        string(parsed->value(QStringLiteral("original_name")),
               memberPath(path, QStringLiteral("original_name")), 1, 255);
        if (const auto count = integer(parsed->value(QStringLiteral("page_count")),
                                       memberPath(path, QStringLiteral("page_count")))) {
            if (*count < 1 || *count > std::numeric_limits<int>::max()) {
                add(memberPath(path, QStringLiteral("page_count")),
                    QStringLiteral("must be in the supported positive page range"));
            } else {
                pageCount = static_cast<int>(*count);
            }
        }
        validateCoordinateSystem(parsed->value(QStringLiteral("coordinate_system")),
                                 memberPath(path, QStringLiteral("coordinate_system")));
        validateSourcePages(parsed->value(QStringLiteral("pages")),
                            memberPath(path, QStringLiteral("pages")));
    }

    void validateCoordinateSystem(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"frame", "units", "origin", "x_axis", "y_axis", "rotation_applied"},
              {"frame", "units", "origin", "x_axis", "y_axis", "rotation_applied"});
        exactString(parsed->value(QStringLiteral("frame")), memberPath(path, QStringLiteral("frame")),
                    "pdf_viewed_page");
        exactString(parsed->value(QStringLiteral("units")), memberPath(path, QStringLiteral("units")),
                    "pt");
        exactString(parsed->value(QStringLiteral("origin")), memberPath(path, QStringLiteral("origin")),
                    "top_left");
        exactString(parsed->value(QStringLiteral("x_axis")), memberPath(path, QStringLiteral("x_axis")),
                    "right");
        exactString(parsed->value(QStringLiteral("y_axis")), memberPath(path, QStringLiteral("y_axis")),
                    "down");
        if (const auto applied = boolean(parsed->value(QStringLiteral("rotation_applied")),
                                         memberPath(path, QStringLiteral("rotation_applied")))) {
            if (!*applied) {
                add(memberPath(path, QStringLiteral("rotation_applied")),
                    QStringLiteral("must be true"));
            }
        }
    }

    void validateSourcePages(const QJsonValue& value, const QString& path)
    {
        const auto pages = array(value, path);
        if (!pages) {
            return;
        }
        if (pages->isEmpty()) {
            add(path, QStringLiteral("must contain at least one page record"));
        }
        if (pageCount > 0 && pages->size() != pageCount) {
            add(path,
                QStringLiteral("page record count %1 does not match page_count %2")
                    .arg(pages->size())
                    .arg(pageCount));
        }
        QSet<qint64> indexes;
        for (qsizetype i = 0; i < pages->size(); ++i) {
            const QString pagePath = itemPath(path, i);
            const auto page = object(pages->at(i), pagePath);
            if (!page) {
                continue;
            }
            shape(*page,
                  pagePath,
                  {"index", "record_status", "rotation_deg", "media_box_pdf_pt",
                   "crop_box_pdf_pt"},
                  {"index", "record_status", "rotation_deg", "media_box_pdf_pt",
                   "crop_box_pdf_pt"});
            if (const auto index = integer(page->value(QStringLiteral("index")),
                                           memberPath(pagePath, QStringLiteral("index")))) {
                if (*index < 0 || pageCount <= 0 || *index >= pageCount) {
                    add(memberPath(pagePath, QStringLiteral("index")),
                        QStringLiteral("page record index is outside page_count"));
                }
                if (indexes.contains(*index)) {
                    add(memberPath(pagePath, QStringLiteral("index")),
                        QStringLiteral("duplicate source page index"));
                }
                indexes.insert(*index);
                if (*index != i) {
                    add(memberPath(pagePath, QStringLiteral("index")),
                        QStringLiteral("page records must be ordered and indexed contiguously"));
                }
            }
            if (const auto status = string(page->value(QStringLiteral("record_status")),
                                           memberPath(pagePath, QStringLiteral("record_status")))) {
                oneOf(*status, memberPath(pagePath, QStringLiteral("record_status")),
                      {"recorded", "unknown"});
            }
            const QJsonValue rotation = page->value(QStringLiteral("rotation_deg"));
            if (!rotation.isNull()) {
                if (const auto degrees = integer(rotation,
                                                 memberPath(pagePath, QStringLiteral("rotation_deg")))) {
                    if (*degrees != 0 && *degrees != 90 && *degrees != 180 && *degrees != 270) {
                        add(memberPath(pagePath, QStringLiteral("rotation_deg")),
                            QStringLiteral("must be 0, 90, 180, 270, or null"));
                    }
                }
            }
            const QJsonValue mediaBox = page->value(QStringLiteral("media_box_pdf_pt"));
            if (!mediaBox.isNull()) {
                rect4(mediaBox, memberPath(pagePath, QStringLiteral("media_box_pdf_pt")));
            }
            const QJsonValue cropBox = page->value(QStringLiteral("crop_box_pdf_pt"));
            if (!cropBox.isNull()) {
                rect4(cropBox, memberPath(pagePath, QStringLiteral("crop_box_pdf_pt")));
            }
        }
    }

    void validateCalibrations(const QJsonValue& value, const QString& path)
    {
        const auto records = array(value, path);
        if (!records) {
            return;
        }
        if (records->isEmpty()) {
            add(path, QStringLiteral("must contain at least one calibration"));
        }
        for (qsizetype i = 0; i < records->size(); ++i) {
            const QString recordPath = itemPath(path, i);
            const auto record = object(records->at(i), recordPath);
            if (!record) {
                continue;
            }
            shape(*record,
                  recordPath,
                  {"id", "page", "scale_mm_per_pt", "rotation_deg", "tx_mm", "ty_mm",
                   "flip_y", "measured_distance_mm", "anchors", "residual_mm",
                   "uncertainty_mm", "review_status"},
                  {"id", "page", "scale_mm_per_pt", "rotation_deg", "tx_mm", "ty_mm",
                   "flip_y", "measured_distance_mm", "anchors", "residual_mm",
                   "uncertainty_mm", "review_status"});
            const auto id = recordId(record->value(QStringLiteral("id")),
                                     memberPath(recordPath, QStringLiteral("id")),
                                     QStringLiteral("calibration"));
            const PageRef page = recordPage(record->value(QStringLiteral("page")),
                                            memberPath(recordPath, QStringLiteral("page")));
            if (id) {
                calibrationIds.push_back(*id);
                calibrationPages_.insert(*id, page.valid ? page.page : -1);
            }
            if (const auto scale = number(record->value(QStringLiteral("scale_mm_per_pt")),
                                          memberPath(recordPath, QStringLiteral("scale_mm_per_pt")))) {
                if (*scale <= 0.0) {
                    add(memberPath(recordPath, QStringLiteral("scale_mm_per_pt")),
                        QStringLiteral("must be greater than zero"));
                }
            }
            number(record->value(QStringLiteral("rotation_deg")),
                   memberPath(recordPath, QStringLiteral("rotation_deg")));
            number(record->value(QStringLiteral("tx_mm")),
                   memberPath(recordPath, QStringLiteral("tx_mm")));
            number(record->value(QStringLiteral("ty_mm")),
                   memberPath(recordPath, QStringLiteral("ty_mm")));
            boolean(record->value(QStringLiteral("flip_y")),
                    memberPath(recordPath, QStringLiteral("flip_y")));
            if (const auto measured = number(record->value(QStringLiteral("measured_distance_mm")),
                                             memberPath(recordPath, QStringLiteral("measured_distance_mm")))) {
                if (*measured <= 0.0) {
                    add(memberPath(recordPath, QStringLiteral("measured_distance_mm")),
                        QStringLiteral("must be greater than zero"));
                }
            }
            validateAnchors(record->value(QStringLiteral("anchors")),
                            memberPath(recordPath, QStringLiteral("anchors")));
            nullableNumber(record->value(QStringLiteral("residual_mm")),
                           memberPath(recordPath, QStringLiteral("residual_mm")), true);
            nullableNumber(record->value(QStringLiteral("uncertainty_mm")),
                           memberPath(recordPath, QStringLiteral("uncertainty_mm")), true);
            if (const auto status = string(record->value(QStringLiteral("review_status")),
                                           memberPath(recordPath, QStringLiteral("review_status")))) {
                oneOf(*status, memberPath(recordPath, QStringLiteral("review_status")),
                      {"needs_review", "confirmed"});
            }
        }
    }

    void validateAnchors(const QJsonValue& value, const QString& path)
    {
        const auto anchors = array(value, path);
        if (!anchors) {
            return;
        }
        if (anchors->size() < 2) {
            add(path, QStringLiteral("must contain at least two calibration anchors"));
        }
        for (qsizetype i = 0; i < anchors->size(); ++i) {
            const QString anchorPath = itemPath(path, i);
            const auto anchor = object(anchors->at(i), anchorPath);
            if (!anchor) {
                continue;
            }
            shape(*anchor,
                  anchorPath,
                  {"pdf_point_pt", "view_point_mm", "role"},
                  {"pdf_point_pt", "view_point_mm", "role"});
            point2(anchor->value(QStringLiteral("pdf_point_pt")),
                   memberPath(anchorPath, QStringLiteral("pdf_point_pt")));
            point2(anchor->value(QStringLiteral("view_point_mm")),
                   memberPath(anchorPath, QStringLiteral("view_point_mm")));
            if (const auto role = string(anchor->value(QStringLiteral("role")),
                                         memberPath(anchorPath, QStringLiteral("role")))) {
                oneOf(*role, memberPath(anchorPath, QStringLiteral("role")),
                      {"origin", "distance_endpoint", "correspondence"});
            }
        }
    }

    void validateViewboxes(const QJsonValue& value, const QString& path)
    {
        const auto records = array(value, path);
        if (!records) {
            return;
        }
        for (qsizetype i = 0; i < records->size(); ++i) {
            const QString recordPath = itemPath(path, i);
            const auto record = object(records->at(i), recordPath);
            if (!record) {
                continue;
            }
            shape(*record,
                  recordPath,
                  {"id", "page", "rect_pdf_pt", "label", "classification",
                   "calibration_id", "frame"},
                  {"id", "legacy_id", "page", "rect_pdf_pt", "label", "classification",
                   "calibration_id", "frame", "crop_asset"});
            const auto id = recordId(record->value(QStringLiteral("id")),
                                     memberPath(recordPath, QStringLiteral("id")),
                                     QStringLiteral("viewbox"));
            const PageRef page = recordPage(record->value(QStringLiteral("page")),
                                            memberPath(recordPath, QStringLiteral("page")));
            if (id) {
                viewboxIds.push_back(*id);
                viewboxPages_.insert(*id, page.valid ? page.page : -1);
            }
            validateLegacyId(*record, recordPath);
            rect4(record->value(QStringLiteral("rect_pdf_pt")),
                  memberPath(recordPath, QStringLiteral("rect_pdf_pt")));
            string(record->value(QStringLiteral("label")),
                   memberPath(recordPath, QStringLiteral("label")), 1, 64);
            validateClassification(record->value(QStringLiteral("classification")),
                                   memberPath(recordPath, QStringLiteral("classification")));
            if (const auto calibrationId = string(record->value(QStringLiteral("calibration_id")),
                                                  memberPath(recordPath, QStringLiteral("calibration_id")))) {
                if (uuid(*calibrationId, memberPath(recordPath, QStringLiteral("calibration_id")))) {
                    if (!calibrationPages_.contains(*calibrationId)) {
                        add(memberPath(recordPath, QStringLiteral("calibration_id")),
                            QStringLiteral("references an unknown calibration ID"));
                    } else if (page.valid && calibrationPages_.value(*calibrationId) != page.page) {
                        add(memberPath(recordPath, QStringLiteral("calibration_id")),
                            QStringLiteral("calibration belongs to a different source page"));
                    }
                }
            }
            validateFrame(record->value(QStringLiteral("frame")),
                          memberPath(recordPath, QStringLiteral("frame")));
            if (record->contains(QStringLiteral("crop_asset"))) {
                validateManagedAsset(record->value(QStringLiteral("crop_asset")),
                                     memberPath(recordPath, QStringLiteral("crop_asset")));
            }
        }
    }

    void validateLegacyId(const QJsonObject& object, const QString& path)
    {
        if (!object.contains(QStringLiteral("legacy_id"))) {
            return;
        }
        if (const auto legacy = integer(object.value(QStringLiteral("legacy_id")),
                                        memberPath(path, QStringLiteral("legacy_id")))) {
            if (*legacy < 1) {
                add(memberPath(path, QStringLiteral("legacy_id")),
                    QStringLiteral("must be at least 1"));
            }
        }
    }

    void validateClassification(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"source", "confidence", "caption"},
              {"source", "confidence", "caption"});
        if (const auto source = string(parsed->value(QStringLiteral("source")),
                                       memberPath(path, QStringLiteral("source")))) {
            oneOf(*source, memberPath(path, QStringLiteral("source")), {"caption", "ai", "user"});
        }
        if (const auto confidence = number(parsed->value(QStringLiteral("confidence")),
                                           memberPath(path, QStringLiteral("confidence")))) {
            if (*confidence < 0.0 || *confidence > 1.0) {
                add(memberPath(path, QStringLiteral("confidence")),
                    QStringLiteral("must be between zero and one"));
            }
        }
        string(parsed->value(QStringLiteral("caption")),
               memberPath(path, QStringLiteral("caption")), 0, 1024);
    }

    void validateFrame(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"origin_mm", "u_axis", "v_axis", "normal", "local_to_world_mm",
               "handedness", "binding_status", "plane_hint"},
              {"origin_mm", "u_axis", "v_axis", "normal", "local_to_world_mm",
               "handedness", "binding_status", "plane_hint", "datum_ids"});
        const auto origin = point3(parsed->value(QStringLiteral("origin_mm")),
                                   memberPath(path, QStringLiteral("origin_mm")));
        const auto uAxis = validateAxis(parsed->value(QStringLiteral("u_axis")),
                                        memberPath(path, QStringLiteral("u_axis")));
        const auto vAxis = validateAxis(parsed->value(QStringLiteral("v_axis")),
                                        memberPath(path, QStringLiteral("v_axis")));
        const auto normal = validateAxis(parsed->value(QStringLiteral("normal")),
                                         memberPath(path, QStringLiteral("normal")));
        const auto matrix = fixedNumbers(parsed->value(QStringLiteral("local_to_world_mm")),
                                         memberPath(path, QStringLiteral("local_to_world_mm")), 16);
        const auto handedness = string(parsed->value(QStringLiteral("handedness")),
                                       memberPath(path, QStringLiteral("handedness")));
        if (handedness) {
            oneOf(*handedness, memberPath(path, QStringLiteral("handedness")), {"right", "left"});
        }
        if (uAxis && vAxis && normal) {
            const auto dot = [](const QVector<double>& a, const QVector<double>& b) {
                return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
            };
            if (std::abs(dot(*uAxis, *vAxis)) > 1.0e-6
                || std::abs(dot(*uAxis, *normal)) > 1.0e-6
                || std::abs(dot(*vAxis, *normal)) > 1.0e-6) {
                add(path, QStringLiteral("frame axes must be mutually orthogonal"));
            }
            const QVector<double> cross{
                (*uAxis)[1] * (*vAxis)[2] - (*uAxis)[2] * (*vAxis)[1],
                (*uAxis)[2] * (*vAxis)[0] - (*uAxis)[0] * (*vAxis)[2],
                (*uAxis)[0] * (*vAxis)[1] - (*uAxis)[1] * (*vAxis)[0],
            };
            if (handedness) {
                const double expected = *handedness == QLatin1String("right") ? 1.0 : -1.0;
                if (std::abs(dot(cross, *normal) - expected) > 1.0e-6) {
                    add(memberPath(path, QStringLiteral("handedness")),
                        QStringLiteral("does not match the frame axes"));
                }
            }
        }
        if (origin && uAxis && vAxis && normal && matrix) {
            const QVector<double> expected{
                (*uAxis)[0], (*vAxis)[0], (*normal)[0], (*origin)[0],
                (*uAxis)[1], (*vAxis)[1], (*normal)[1], (*origin)[1],
                (*uAxis)[2], (*vAxis)[2], (*normal)[2], (*origin)[2],
                0.0, 0.0, 0.0, 1.0,
            };
            for (qsizetype index = 0; index < expected.size(); ++index) {
                if (std::abs((*matrix)[index] - expected[index]) > 1.0e-6) {
                    add(memberPath(path, QStringLiteral("local_to_world_mm")),
                        QStringLiteral("matrix does not match frame axes and origin"));
                    break;
                }
            }
        }
        if (const auto status = string(parsed->value(QStringLiteral("binding_status")),
                                       memberPath(path, QStringLiteral("binding_status")))) {
            oneOf(*status, memberPath(path, QStringLiteral("binding_status")),
                  {"suggested", "confirmed"});
        }
        if (const auto hint = string(parsed->value(QStringLiteral("plane_hint")),
                                     memberPath(path, QStringLiteral("plane_hint")))) {
            oneOf(*hint, memberPath(path, QStringLiteral("plane_hint")),
                  {"XY", "XZ", "YZ", "arbitrary", "unknown"});
        }
        if (parsed->contains(QStringLiteral("datum_ids"))) {
            const QString datumPath = memberPath(path, QStringLiteral("datum_ids"));
            const auto datums = array(parsed->value(QStringLiteral("datum_ids")), datumPath);
            if (datums) {
                QSet<QString> seen;
                for (qsizetype i = 0; i < datums->size(); ++i) {
                    const QString idPath = itemPath(datumPath, i);
                    if (const auto id = string(datums->at(i), idPath)) {
                        stableId(*id, idPath);
                        if (seen.contains(*id)) {
                            add(idPath, QStringLiteral("duplicate datum ID"));
                        }
                        seen.insert(*id);
                    }
                }
            }
        }
    }

    std::optional<QVector<double>> validateAxis(const QJsonValue& value, const QString& path)
    {
        const auto axis = point3(value, path);
        if (!axis) {
            return std::nullopt;
        }
        const double normSquared = (*axis)[0] * (*axis)[0] + (*axis)[1] * (*axis)[1]
            + (*axis)[2] * (*axis)[2];
        if (std::abs(normSquared - 1.0) > 1.0e-6) {
            add(path, QStringLiteral("axis must be a unit vector"));
        }
        return axis;
    }

    void validateManagedAsset(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"uri", "sha256", "media_type", "byte_size"},
              {"uri", "sha256", "media_type", "byte_size"});
        if (const auto uri = string(parsed->value(QStringLiteral("uri")),
                                    memberPath(path, QStringLiteral("uri")), 1, 1024)) {
            validateAssetUri(*uri, memberPath(path, QStringLiteral("uri")));
        }
        if (const auto digest = string(parsed->value(QStringLiteral("sha256")),
                                       memberPath(path, QStringLiteral("sha256")))) {
            sha256(*digest, memberPath(path, QStringLiteral("sha256")));
        }
        string(parsed->value(QStringLiteral("media_type")),
               memberPath(path, QStringLiteral("media_type")), 1, 127);
        if (const auto bytes = integer(parsed->value(QStringLiteral("byte_size")),
                                       memberPath(path, QStringLiteral("byte_size")))) {
            if (*bytes < 0 || *bytes > CadAssistContractReader::MaxInputBytes) {
                add(memberPath(path, QStringLiteral("byte_size")),
                    QStringLiteral("asset size is outside the 50 MiB managed limit"));
            }
        }
    }

    void validateAssetUri(const QString& uri, const QString& path)
    {
        static const QRegularExpression allowed(QStringLiteral("^assets/[A-Za-z0-9._/-]+$"));
        static const QRegularExpression driveAbsolute(QStringLiteral("^[A-Za-z]:[/\\\\]"));
        const QString normalizedSeparators = QString(uri).replace(QLatin1Char('\\'), QLatin1Char('/'));
        const QStringList components = normalizedSeparators.split(QLatin1Char('/'), Qt::KeepEmptyParts);
        const bool traversal = components.contains(QStringLiteral(".."))
            || components.contains(QStringLiteral(".")) || components.contains(QString());
        if (QDir::isAbsolutePath(uri) || driveAbsolute.match(uri).hasMatch()
            || uri.startsWith(QLatin1Char('/')) || uri.contains(QLatin1Char('\\'))
            || !uri.startsWith(QStringLiteral("assets/")) || !allowed.match(uri).hasMatch()
            || traversal) {
            add(path,
                QStringLiteral("must be a normalized relative URI below assets/ without traversal"));
        }
    }

    void validateTargets(const QJsonValue& value, const QString& path)
    {
        const auto records = array(value, path);
        if (!records) {
            return;
        }
        for (qsizetype i = 0; i < records->size(); ++i) {
            const QString recordPath = itemPath(path, i);
            const auto record = object(records->at(i), recordPath);
            if (!record) {
                continue;
            }
            shape(*record,
                  recordPath,
                  {"id", "label", "page", "viewbox_id", "snap_point_pdf_pt",
                   "snap_point_view_mm", "snap_kind", "entity_kind", "closed",
                   "points_view_mm", "evidence"},
                  {"id", "legacy_id", "label", "page", "viewbox_id", "snap_point_pdf_pt",
                   "snap_point_view_mm", "snap_kind", "entity_kind", "closed",
                   "points_view_mm", "evidence"});
            const auto id = recordId(record->value(QStringLiteral("id")),
                                     memberPath(recordPath, QStringLiteral("id")),
                                     QStringLiteral("target"));
            const PageRef page = recordPage(record->value(QStringLiteral("page")),
                                            memberPath(recordPath, QStringLiteral("page")));
            if (id) {
                targetIds.push_back(*id);
                targetPages_.insert(*id, page.valid ? page.page : -1);
            }
            validateLegacyId(*record, recordPath);
            string(record->value(QStringLiteral("label")),
                   memberPath(recordPath, QStringLiteral("label")), 0, 256);
            validateOptionalViewboxRef(record->value(QStringLiteral("viewbox_id")),
                                       memberPath(recordPath, QStringLiteral("viewbox_id")), page);
            point2(record->value(QStringLiteral("snap_point_pdf_pt")),
                   memberPath(recordPath, QStringLiteral("snap_point_pdf_pt")));
            point2(record->value(QStringLiteral("snap_point_view_mm")),
                   memberPath(recordPath, QStringLiteral("snap_point_view_mm")));
            if (const auto kind = string(record->value(QStringLiteral("snap_kind")),
                                         memberPath(recordPath, QStringLiteral("snap_kind")))) {
                oneOf(*kind, memberPath(recordPath, QStringLiteral("snap_kind")),
                      {"endpoint", "midpoint", "on-segment", "free"});
            }
            string(record->value(QStringLiteral("entity_kind")),
                   memberPath(recordPath, QStringLiteral("entity_kind")), 0, 64);
            boolean(record->value(QStringLiteral("closed")),
                    memberPath(recordPath, QStringLiteral("closed")));
            validatePointArray(record->value(QStringLiteral("points_view_mm")),
                               memberPath(recordPath, QStringLiteral("points_view_mm")), false);
            validateEvidence(record->value(QStringLiteral("evidence")),
                             memberPath(recordPath, QStringLiteral("evidence")));
        }
    }

    void validateOptionalViewboxRef(const QJsonValue& value,
                                    const QString& path,
                                    const PageRef& ownerPage)
    {
        if (value.isNull()) {
            return;
        }
        const auto id = string(value, path);
        if (!id || !uuid(*id, path)) {
            return;
        }
        if (!viewboxPages_.contains(*id)) {
            add(path, QStringLiteral("references an unknown viewbox ID"));
        } else if (ownerPage.valid && viewboxPages_.value(*id) != ownerPage.page) {
            add(path, QStringLiteral("viewbox belongs to a different source page"));
        }
    }

    void validatePointArray(const QJsonValue& value,
                            const QString& path,
                            bool requireOne)
    {
        const auto points = array(value, path);
        if (!points) {
            return;
        }
        if (requireOne && points->isEmpty()) {
            add(path, QStringLiteral("must contain at least one point"));
        }
        for (qsizetype i = 0; i < points->size(); ++i) {
            point2(points->at(i), itemPath(path, i));
        }
    }

    void validateEvidence(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"entity_signature", "extractor", "nearby_text"},
              {"entity_signature", "extractor", "nearby_text"});
        if (const auto digest = string(parsed->value(QStringLiteral("entity_signature")),
                                       memberPath(path, QStringLiteral("entity_signature")))) {
            sha256(*digest, memberPath(path, QStringLiteral("entity_signature")));
        }
        const QString extractorPath = memberPath(path, QStringLiteral("extractor"));
        if (const auto extractor = object(parsed->value(QStringLiteral("extractor")), extractorPath)) {
            shape(*extractor,
                  extractorPath,
                  {"name", "version"},
                  {"name", "version"});
            string(extractor->value(QStringLiteral("name")),
                   memberPath(extractorPath, QStringLiteral("name")), 1, 64);
            string(extractor->value(QStringLiteral("version")),
                   memberPath(extractorPath, QStringLiteral("version")), 1, 64);
        }
        stringArray(parsed->value(QStringLiteral("nearby_text")),
                    memberPath(path, QStringLiteral("nearby_text")), 512);
    }

    void validateElements(const QJsonValue& value, const QString& path)
    {
        const auto records = array(value, path);
        if (!records) {
            return;
        }
        for (qsizetype i = 0; i < records->size(); ++i) {
            const QString recordPath = itemPath(path, i);
            const auto record = object(records->at(i), recordPath);
            if (!record) {
                continue;
            }
            shape(*record,
                  recordPath,
                  {"id", "label", "page", "viewbox_id", "tracker_ids",
                   "start_point_view_mm", "polylines", "references"},
                  {"id", "legacy_id", "label", "page", "viewbox_id", "tracker_ids",
                   "start_point_view_mm", "polylines", "references"});
            const auto id = recordId(record->value(QStringLiteral("id")),
                                     memberPath(recordPath, QStringLiteral("id")),
                                     QStringLiteral("element"));
            const PageRef page = recordPage(record->value(QStringLiteral("page")),
                                            memberPath(recordPath, QStringLiteral("page")));
            if (id) {
                elementIds.push_back(*id);
                elementPages_.insert(*id, page.valid ? page.page : -1);
            }
            validateLegacyId(*record, recordPath);
            string(record->value(QStringLiteral("label")),
                   memberPath(recordPath, QStringLiteral("label")), 0, 256);
            validateRequiredViewboxRef(record->value(QStringLiteral("viewbox_id")),
                                       memberPath(recordPath, QStringLiteral("viewbox_id")), page);
            validateTrackerRefs(record->value(QStringLiteral("tracker_ids")),
                                memberPath(recordPath, QStringLiteral("tracker_ids")), page);
            point2(record->value(QStringLiteral("start_point_view_mm")),
                   memberPath(recordPath, QStringLiteral("start_point_view_mm")));
            validatePolylines(record->value(QStringLiteral("polylines")),
                              memberPath(recordPath, QStringLiteral("polylines")));
            validateReferences(record->value(QStringLiteral("references")),
                               memberPath(recordPath, QStringLiteral("references")));
        }
    }

    void validateRequiredViewboxRef(const QJsonValue& value,
                                    const QString& path,
                                    const PageRef& ownerPage)
    {
        if (value.isNull()) {
            add(path, QStringLiteral("must reference a viewbox ID"));
            return;
        }
        validateOptionalViewboxRef(value, path, ownerPage);
    }

    void validateTrackerRefs(const QJsonValue& value,
                             const QString& path,
                             const PageRef& ownerPage)
    {
        const auto ids = array(value, path);
        if (!ids) {
            return;
        }
        if (ids->isEmpty()) {
            add(path, QStringLiteral("must contain at least one target ID"));
        }
        QSet<QString> seen;
        for (qsizetype i = 0; i < ids->size(); ++i) {
            const QString idPath = itemPath(path, i);
            const auto id = string(ids->at(i), idPath);
            if (!id || !uuid(*id, idPath)) {
                continue;
            }
            if (seen.contains(*id)) {
                add(idPath, QStringLiteral("duplicate target reference"));
            }
            seen.insert(*id);
            if (!targetPages_.contains(*id)) {
                add(idPath, QStringLiteral("references an unknown target ID"));
            } else if (ownerPage.valid && targetPages_.value(*id) != ownerPage.page) {
                add(idPath, QStringLiteral("target belongs to a different source page"));
            }
        }
    }

    void validatePolylines(const QJsonValue& value, const QString& path)
    {
        const auto polylines = array(value, path);
        if (!polylines) {
            return;
        }
        if (polylines->isEmpty()) {
            add(path, QStringLiteral("must contain at least one polyline"));
        }
        for (qsizetype i = 0; i < polylines->size(); ++i) {
            const QString polylinePath = itemPath(path, i);
            const auto polyline = object(polylines->at(i), polylinePath);
            if (!polyline) {
                continue;
            }
            shape(*polyline,
                  polylinePath,
                  {"points_view_mm", "closed"},
                  {"points_view_mm", "closed"});
            validatePointArray(polyline->value(QStringLiteral("points_view_mm")),
                               memberPath(polylinePath, QStringLiteral("points_view_mm")), true);
            boolean(polyline->value(QStringLiteral("closed")),
                    memberPath(polylinePath, QStringLiteral("closed")));
        }
    }

    void validateReferences(const QJsonValue& value, const QString& path)
    {
        const auto references = array(value, path);
        if (!references) {
            return;
        }
        for (qsizetype i = 0; i < references->size(); ++i) {
            const QString referencePath = itemPath(path, i);
            const auto reference = object(references->at(i), referencePath);
            if (!reference) {
                continue;
            }
            shape(*reference,
                  referencePath,
                  {"id", "pick_pdf_pt", "raw_text", "nominal_mm", "lower_tolerance_mm",
                   "upper_tolerance_mm", "kind", "multiplicity", "units", "authority"},
                  {"id", "pick_pdf_pt", "raw_text", "nominal_mm", "lower_tolerance_mm",
                   "upper_tolerance_mm", "kind", "multiplicity", "units", "authority"});
            recordId(reference->value(QStringLiteral("id")),
                     memberPath(referencePath, QStringLiteral("id")),
                     QStringLiteral("element reference"));
            point2(reference->value(QStringLiteral("pick_pdf_pt")),
                   memberPath(referencePath, QStringLiteral("pick_pdf_pt")));
            string(reference->value(QStringLiteral("raw_text")),
                   memberPath(referencePath, QStringLiteral("raw_text")), 0, 1024);
            nullableNumber(reference->value(QStringLiteral("nominal_mm")),
                           memberPath(referencePath, QStringLiteral("nominal_mm")));
            nullableNumber(reference->value(QStringLiteral("lower_tolerance_mm")),
                           memberPath(referencePath, QStringLiteral("lower_tolerance_mm")), true);
            nullableNumber(reference->value(QStringLiteral("upper_tolerance_mm")),
                           memberPath(referencePath, QStringLiteral("upper_tolerance_mm")), true);
            if (const auto kind = string(reference->value(QStringLiteral("kind")),
                                         memberPath(referencePath, QStringLiteral("kind")))) {
                oneOf(*kind, memberPath(referencePath, QStringLiteral("kind")),
                      {"linear", "radius", "diameter", "angle", "thread", "count", "unknown"});
            }
            if (const auto multiplicity = integer(reference->value(QStringLiteral("multiplicity")),
                                                  memberPath(referencePath, QStringLiteral("multiplicity")))) {
                if (*multiplicity < 1) {
                    add(memberPath(referencePath, QStringLiteral("multiplicity")),
                        QStringLiteral("must be at least 1"));
                }
            }
            if (const auto units = string(reference->value(QStringLiteral("units")),
                                          memberPath(referencePath, QStringLiteral("units")))) {
                oneOf(*units, memberPath(referencePath, QStringLiteral("units")),
                      {"mm", "deg", "count", "unknown"});
            }
            if (const auto authority = string(reference->value(QStringLiteral("authority")),
                                              memberPath(referencePath, QStringLiteral("authority")))) {
                oneOf(*authority, memberPath(referencePath, QStringLiteral("authority")),
                      {"callout", "derived", "linework", "user"});
            }
        }
    }

    void validateDeviations(const QJsonValue& value, const QString& path)
    {
        const auto records = array(value, path);
        if (!records) {
            return;
        }
        for (qsizetype i = 0; i < records->size(); ++i) {
            const QString recordPath = itemPath(path, i);
            const auto record = object(records->at(i), recordPath);
            if (!record) {
                continue;
            }
            shape(*record,
                  recordPath,
                  {"id", "element_id", "cad_object_id", "host_geometry_id",
                   "sketch_revision", "status", "tolerance_mm", "metrics"},
                  {"id", "element_id", "cad_object_id", "host_geometry_id",
                   "sketch_revision", "geometry_index", "status", "tolerance_mm", "metrics",
                   "suggested_target", "proposal_id"});
            if (const auto id = recordId(record->value(QStringLiteral("id")),
                                         memberPath(recordPath, QStringLiteral("id")),
                                         QStringLiteral("deviation"))) {
                deviationIds.push_back(*id);
            }
            if (const auto elementId = string(record->value(QStringLiteral("element_id")),
                                              memberPath(recordPath, QStringLiteral("element_id")))) {
                if (uuid(*elementId, memberPath(recordPath, QStringLiteral("element_id")))
                    && !elementPages_.contains(*elementId)) {
                    add(memberPath(recordPath, QStringLiteral("element_id")),
                        QStringLiteral("references an unknown element ID"));
                }
            }
            if (const auto cadObject = string(record->value(QStringLiteral("cad_object_id")),
                                              memberPath(recordPath, QStringLiteral("cad_object_id")))) {
                if (stableId(*cadObject, memberPath(recordPath, QStringLiteral("cad_object_id")))
                    && !projectObjectId_.isEmpty() && *cadObject != projectObjectId_) {
                    add(memberPath(recordPath, QStringLiteral("cad_object_id")),
                        QStringLiteral("does not reference project.object_id"));
                }
            }
            if (const auto host = string(record->value(QStringLiteral("host_geometry_id")),
                                         memberPath(recordPath, QStringLiteral("host_geometry_id")))) {
                stableId(*host, memberPath(recordPath, QStringLiteral("host_geometry_id")));
            }
            validateNonnegativeInteger(record->value(QStringLiteral("sketch_revision")),
                                       memberPath(recordPath, QStringLiteral("sketch_revision")));
            if (record->contains(QStringLiteral("geometry_index"))) {
                validateNonnegativeInteger(record->value(QStringLiteral("geometry_index")),
                                           memberPath(recordPath, QStringLiteral("geometry_index")));
            }
            if (const auto status = string(record->value(QStringLiteral("status")),
                                           memberPath(recordPath, QStringLiteral("status")))) {
                oneOf(*status, memberPath(recordPath, QStringLiteral("status")),
                      {"within_tolerance", "deviates", "unmatched"});
            }
            if (const auto tolerance = number(record->value(QStringLiteral("tolerance_mm")),
                                              memberPath(recordPath, QStringLiteral("tolerance_mm")))) {
                if (*tolerance <= 0.0) {
                    add(memberPath(recordPath, QStringLiteral("tolerance_mm")),
                        QStringLiteral("must be greater than zero"));
                }
            }
            validateMetrics(record->value(QStringLiteral("metrics")),
                            memberPath(recordPath, QStringLiteral("metrics")));
            if (record->contains(QStringLiteral("suggested_target"))) {
                validateSuggestedTarget(record->value(QStringLiteral("suggested_target")),
                                        memberPath(recordPath, QStringLiteral("suggested_target")));
            }
            if (record->contains(QStringLiteral("proposal_id"))) {
                if (const auto proposal = string(record->value(QStringLiteral("proposal_id")),
                                                 memberPath(recordPath, QStringLiteral("proposal_id")))) {
                    uuid(*proposal, memberPath(recordPath, QStringLiteral("proposal_id")));
                }
            }
        }
    }

    void validateNonnegativeInteger(const QJsonValue& value, const QString& path)
    {
        if (const auto parsed = integer(value, path)) {
            if (*parsed < 0) {
                add(path, QStringLiteral("must be nonnegative"));
            }
        }
    }

    void validateMetrics(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"start_error_mm", "end_error_mm", "length_error_mm", "angle_error_deg",
               "max_error_mm"},
              {"start_error_mm", "end_error_mm", "length_error_mm", "angle_error_deg",
               "max_error_mm"});
        validateNonnegativeNumber(parsed->value(QStringLiteral("start_error_mm")),
                                  memberPath(path, QStringLiteral("start_error_mm")));
        validateNonnegativeNumber(parsed->value(QStringLiteral("end_error_mm")),
                                  memberPath(path, QStringLiteral("end_error_mm")));
        number(parsed->value(QStringLiteral("length_error_mm")),
               memberPath(path, QStringLiteral("length_error_mm")));
        number(parsed->value(QStringLiteral("angle_error_deg")),
               memberPath(path, QStringLiteral("angle_error_deg")));
        validateNonnegativeNumber(parsed->value(QStringLiteral("max_error_mm")),
                                  memberPath(path, QStringLiteral("max_error_mm")));
    }

    void validateNonnegativeNumber(const QJsonValue& value, const QString& path)
    {
        if (const auto parsed = number(value, path)) {
            if (*parsed < 0.0) {
                add(path, QStringLiteral("must be nonnegative"));
            }
        }
    }

    void validateSuggestedTarget(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"kind", "target_start_view_mm", "target_end_view_mm"},
              {"kind", "target_start_view_mm", "target_end_view_mm"});
        exactString(parsed->value(QStringLiteral("kind")), memberPath(path, QStringLiteral("kind")),
                    "replace_line_constraints");
        point2(parsed->value(QStringLiteral("target_start_view_mm")),
               memberPath(path, QStringLiteral("target_start_view_mm")));
        point2(parsed->value(QStringLiteral("target_end_view_mm")),
               memberPath(path, QStringLiteral("target_end_view_mm")));
    }

    void validateDatasetPolicy(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"training_consent", "content_classification", "source_rights"},
              {"training_consent", "content_classification", "source_rights"});
        boolean(parsed->value(QStringLiteral("training_consent")),
                memberPath(path, QStringLiteral("training_consent")));
        if (const auto classification = string(parsed->value(QStringLiteral("content_classification")),
                                               memberPath(path, QStringLiteral("content_classification")))) {
            oneOf(*classification, memberPath(path, QStringLiteral("content_classification")),
                  {"public", "internal", "customer_confidential", "restricted"});
        }
        if (const auto rights = string(parsed->value(QStringLiteral("source_rights")),
                                       memberPath(path, QStringLiteral("source_rights")))) {
            oneOf(*rights, memberPath(path, QStringLiteral("source_rights")),
                  {"unknown", "customer_provided", "licensed", "public_domain"});
        }
    }

    void validateProducerValidation(const QJsonValue& value, const QString& path)
    {
        const auto parsed = object(value, path);
        if (!parsed) {
            return;
        }
        shape(*parsed,
              path,
              {"valid", "validator", "errors", "warnings"},
              {"valid", "validator", "errors", "warnings"});

        // Deliberately type-check but do not trust or gate on producer `valid`.
        // The complete independent validation above is authoritative here.
        boolean(parsed->value(QStringLiteral("valid")), memberPath(path, QStringLiteral("valid")));
        string(parsed->value(QStringLiteral("validator")),
               memberPath(path, QStringLiteral("validator")), 1, 128);
        stringArray(parsed->value(QStringLiteral("errors")),
                    memberPath(path, QStringLiteral("errors")), 1024);
        stringArray(parsed->value(QStringLiteral("warnings")),
                    memberPath(path, QStringLiteral("warnings")), 1024);
    }

    const QJsonObject& root_;
    const CadAssistProjectExpectation& expected_;
    QHash<QString, QString> allRecordIds_;
    QHash<QString, int> calibrationPages_;
    QHash<QString, int> viewboxPages_;
    QHash<QString, int> targetPages_;
    QHash<QString, int> elementPages_;
    QString projectObjectId_;
};

CadAssistLoadResult oneIssue(const QString& path, const QString& message)
{
    CadAssistLoadResult result;
    result.issues.push_back({path, message});
    return result;
}

} // namespace

CadAssistSession::CadAssistSession(QJsonDocument document,
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
                                   QStringList deviationIds)
    : document_(std::move(document))
    , schema_(std::move(schema))
    , sessionId_(std::move(sessionId))
    , revision_(revision)
    , documentId_(std::move(documentId))
    , baseRevision_(baseRevision)
    , baseSha256_(std::move(baseSha256))
    , sourceSha256_(std::move(sourceSha256))
    , pageCount_(pageCount)
    , calibrationIds_(std::move(calibrationIds))
    , viewboxIds_(std::move(viewboxIds))
    , targetIds_(std::move(targetIds))
    , elementIds_(std::move(elementIds))
    , deviationIds_(std::move(deviationIds))
{
}

QString CadAssistLoadResult::errorSummary() const
{
    QStringList lines;
    lines.reserve(issues.size());
    for (const CadAssistValidationIssue& issue : issues) {
        lines.push_back(issue.path + QStringLiteral(": ") + issue.message);
    }
    return lines.join(QLatin1Char('\n'));
}

CadAssistLoadResult CadAssistContractReader::loadFile(
    const QString& path,
    const CadAssistProjectExpectation& expectedProject)
{
    const QFileInfo info(path);
    if (!info.exists() || !info.isFile()) {
        return oneIssue(QStringLiteral("$"),
                        QStringLiteral("contract file does not exist or is not a regular file: %1")
                            .arg(path));
    }
    if (info.size() > MaxInputBytes) {
        return oneIssue(QStringLiteral("$"),
                        QStringLiteral("contract exceeds the 50 MiB input limit"));
    }

    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return oneIssue(QStringLiteral("$"),
                        QStringLiteral("cannot open contract file: %1").arg(file.errorString()));
    }
    const QByteArray bytes = file.read(MaxInputBytes + 1);
    if (bytes.size() > MaxInputBytes) {
        return oneIssue(QStringLiteral("$"),
                        QStringLiteral("contract exceeds the 50 MiB input limit"));
    }
    if (file.error() != QFileDevice::NoError) {
        return oneIssue(QStringLiteral("$"),
                        QStringLiteral("cannot read contract file: %1").arg(file.errorString()));
    }
    return loadBytes(bytes, expectedProject, path);
}

CadAssistLoadResult CadAssistContractReader::loadBytes(
    const QByteArray& bytes,
    const CadAssistProjectExpectation& expectedProject,
    const QString& sourceName)
{
    if (bytes.size() > MaxInputBytes) {
        return oneIssue(QStringLiteral("$"),
                        QStringLiteral("contract exceeds the 50 MiB input limit"));
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(bytes, &parseError);
    if (parseError.error != QJsonParseError::NoError) {
        return oneIssue(QStringLiteral("$"),
                        QStringLiteral("invalid JSON in %1 at byte %2: %3")
                            .arg(sourceName)
                            .arg(parseError.offset)
                            .arg(parseError.errorString()));
    }
    if (!document.isObject()) {
        return oneIssue(QStringLiteral("$"), QStringLiteral("contract root must be an object"));
    }

    const QJsonObject root = document.object();
    Validator validator(root, expectedProject);
    validator.run();

    CadAssistLoadResult result;
    result.issues = std::move(validator.issues);
    if (!result.issues.isEmpty()) {
        return result;
    }

    result.session = std::shared_ptr<const CadAssistSession>(
        new CadAssistSession(document,
                             std::move(validator.schema),
                             std::move(validator.sessionId),
                             validator.revision,
                             std::move(validator.documentId),
                             validator.baseRevision,
                             std::move(validator.baseSha256),
                             std::move(validator.sourceSha256),
                             validator.pageCount,
                             std::move(validator.calibrationIds),
                             std::move(validator.viewboxIds),
                             std::move(validator.targetIds),
                             std::move(validator.elementIds),
                             std::move(validator.deviationIds)));
    return result;
}

} // namespace designstudio
