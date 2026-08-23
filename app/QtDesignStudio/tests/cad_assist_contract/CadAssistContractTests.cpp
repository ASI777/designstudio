#include "CadAssistContract.h"

#include <QCoreApplication>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>

#include <iostream>
#include <optional>

using designstudio::CadAssistContractReader;
using designstudio::CadAssistLoadResult;
using designstudio::CadAssistProjectExpectation;

namespace {

QByteArray readAll(const QString& path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        std::cerr << "cannot read fixture: " << path.toStdString() << '\n';
        return {};
    }
    return file.readAll();
}

bool fail(const QString& message)
{
    std::cerr << message.toStdString() << '\n';
    return false;
}

bool tokenIndex(const QJsonValue& token, qsizetype arraySize, qsizetype* result)
{
    if (!token.isDouble()) {
        return false;
    }
    const double raw = token.toDouble();
    const qsizetype index = static_cast<qsizetype>(raw);
    if (raw != static_cast<double>(index) || index < 0 || index >= arraySize) {
        return false;
    }
    *result = index;
    return true;
}

std::optional<QJsonValue> valueAt(const QJsonValue& root,
                                  const QJsonArray& path,
                                  qsizetype depth = 0)
{
    if (depth == path.size()) {
        return root;
    }
    const QJsonValue token = path.at(depth);
    if (root.isObject() && token.isString()) {
        const QJsonObject object = root.toObject();
        if (!object.contains(token.toString())) {
            return std::nullopt;
        }
        return valueAt(object.value(token.toString()), path, depth + 1);
    }
    if (root.isArray()) {
        const QJsonArray array = root.toArray();
        qsizetype index = -1;
        if (!tokenIndex(token, array.size(), &index)) {
            return std::nullopt;
        }
        return valueAt(array.at(index), path, depth + 1);
    }
    return std::nullopt;
}

bool setAt(QJsonValue* root,
           const QJsonArray& path,
           const QJsonValue& replacement,
           qsizetype depth = 0)
{
    if (!root || depth >= path.size()) {
        return false;
    }
    const bool leaf = depth + 1 == path.size();
    const QJsonValue token = path.at(depth);
    if (root->isObject() && token.isString()) {
        QJsonObject object = root->toObject();
        const QString key = token.toString();
        if (leaf) {
            object.insert(key, replacement);
            *root = object;
            return true;
        }
        if (!object.contains(key)) {
            return false;
        }
        QJsonValue child = object.value(key);
        if (!setAt(&child, path, replacement, depth + 1)) {
            return false;
        }
        object.insert(key, child);
        *root = object;
        return true;
    }
    if (root->isArray()) {
        QJsonArray array = root->toArray();
        qsizetype index = -1;
        if (!tokenIndex(token, array.size(), &index)) {
            return false;
        }
        if (leaf) {
            array.replace(index, replacement);
            *root = array;
            return true;
        }
        QJsonValue child = array.at(index);
        if (!setAt(&child, path, replacement, depth + 1)) {
            return false;
        }
        array.replace(index, child);
        *root = array;
        return true;
    }
    return false;
}

bool applyOperation(QJsonValue* document, const QJsonObject& operation, QString* error)
{
    const QString op = operation.value(QStringLiteral("op")).toString();
    const QJsonArray path = operation.value(QStringLiteral("path")).toArray();
    if (op == QStringLiteral("set")) {
        if (!setAt(document, path, operation.value(QStringLiteral("value")))) {
            *error = QStringLiteral("cannot set fixture path");
            return false;
        }
        return true;
    }
    if (op == QStringLiteral("copy")) {
        const auto source = valueAt(*document, operation.value(QStringLiteral("from")).toArray());
        if (!source || !setAt(document, path, *source)) {
            *error = QStringLiteral("cannot copy fixture path");
            return false;
        }
        return true;
    }
    *error = QStringLiteral("unknown fixture operation '%1'").arg(op);
    return false;
}

QJsonDocument parseFixture(const QString& path, bool* ok)
{
    const QByteArray bytes = readAll(path);
    QJsonParseError error;
    const QJsonDocument document = QJsonDocument::fromJson(bytes, &error);
    *ok = !bytes.isEmpty() && error.error == QJsonParseError::NoError && document.isObject();
    if (!*ok) {
        std::cerr << "invalid test fixture " << path.toStdString() << ": "
                  << error.errorString().toStdString() << '\n';
    }
    return document;
}

CadAssistProjectExpectation matchingExpectation()
{
    CadAssistProjectExpectation expected;
    expected.documentId = QStringLiteral("motor-controller-r1");
    expected.baseRevision = 42;
    expected.baseSha256 = QStringLiteral(
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef");
    return expected;
}

bool positive(const QString& fixtureDir)
{
    const QString path = fixtureDir + QStringLiteral("/valid-session.json");
    const CadAssistLoadResult result = CadAssistContractReader::loadFile(path, matchingExpectation());
    if (!result.ok()) {
        return fail(QStringLiteral("valid fixture was rejected:\n%1").arg(result.errorSummary()));
    }
    if (!result.session
        || result.session->schema() != QLatin1String(CadAssistContractReader::ExpectedSchema)
        || result.session->documentId() != QStringLiteral("motor-controller-r1")
        || result.session->baseRevision() != 42
        || result.session->baseSha256() != *matchingExpectation().baseSha256
        || result.session->pageCount() != 1
        || result.session->calibrationIds().size() != 1
        || result.session->viewboxIds().size() != 1
        || result.session->targetIds().size() != 2
        || result.session->elementIds().size() != 1
        || result.session->deviationIds().size() != 1) {
        return fail(QStringLiteral("validated immutable session metadata is incomplete"));
    }

    // The fixture says producer_validation.valid=false. Acceptance here proves
    // that the consumer's own validation, not that advisory claim, is decisive.
    const QJsonObject advisory = result.session->documentSnapshot()
                                     .object()
                                     .value(QStringLiteral("producer_validation"))
                                     .toObject();
    if (advisory.value(QStringLiteral("valid")).toBool(true)) {
        return fail(QStringLiteral("positive fixture no longer exercises advisory false"));
    }
    return true;
}

bool negative(const QString& fixtureDir)
{
    bool validBase = false;
    const QJsonDocument base = parseFixture(fixtureDir + QStringLiteral("/valid-session.json"),
                                            &validBase);
    bool validCases = false;
    const QJsonDocument casesDocument = parseFixture(
        fixtureDir + QStringLiteral("/negative-cases.json"), &validCases);
    if (!validBase || !validCases) {
        return false;
    }

    const QJsonArray cases = casesDocument.object().value(QStringLiteral("cases")).toArray();
    if (cases.isEmpty()) {
        return fail(QStringLiteral("negative fixture contains no cases"));
    }

    for (qsizetype i = 0; i < cases.size(); ++i) {
        const QJsonObject testCase = cases.at(i).toObject();
        const QString name = testCase.value(QStringLiteral("name")).toString();
        QJsonValue mutated = base.object();
        const QJsonArray operations = testCase.value(QStringLiteral("operations")).toArray();
        for (qsizetype j = 0; j < operations.size(); ++j) {
            QString mutationError;
            if (!applyOperation(&mutated, operations.at(j).toObject(), &mutationError)) {
                return fail(QStringLiteral("negative case '%1' is malformed: %2")
                                .arg(name, mutationError));
            }
        }

        CadAssistProjectExpectation expected = matchingExpectation();
        if (testCase.contains(QStringLiteral("expected_document_id"))) {
            expected.documentId = testCase.value(QStringLiteral("expected_document_id")).toString();
        }
        if (testCase.contains(QStringLiteral("expected_base_revision"))) {
            expected.baseRevision = static_cast<qint64>(
                testCase.value(QStringLiteral("expected_base_revision")).toDouble());
        }
        const QByteArray bytes = QJsonDocument(mutated.toObject()).toJson(QJsonDocument::Compact);
        const CadAssistLoadResult result = CadAssistContractReader::loadBytes(
            bytes, expected, QStringLiteral("negative-case:") + name);
        if (result.ok()) {
            return fail(QStringLiteral("negative case '%1' was accepted").arg(name));
        }
        const QString expectedError = testCase.value(QStringLiteral("expected_error")).toString();
        if (expectedError.isEmpty() || !result.errorSummary().contains(expectedError)) {
            return fail(QStringLiteral("negative case '%1' did not report '%2':\n%3")
                            .arg(name, expectedError, result.errorSummary()));
        }
    }
    return true;
}

bool limits(const QString& fixtureDir)
{
    Q_UNUSED(fixtureDir);
    const QByteArray oversized(CadAssistContractReader::MaxInputBytes + 1, ' ');
    const CadAssistLoadResult sizeResult = CadAssistContractReader::loadBytes(oversized);
    if (sizeResult.ok() || !sizeResult.errorSummary().contains(QStringLiteral("50 MiB"))) {
        return fail(QStringLiteral("oversized in-memory contract was not rejected"));
    }

    // JSON cannot legally encode NaN/Infinity. A numeric overflow must fail at
    // parsing rather than becoming a non-finite coordinate in the DTO.
    const QByteArray overflowJson =
        "{\"schema\":\"urn:design-studio:schema:cad-assist-session:1\","
        "\"point\":[1e999999,0]}";
    const CadAssistLoadResult overflowResult = CadAssistContractReader::loadBytes(overflowJson);
    if (overflowResult.ok()) {
        return fail(QStringLiteral("non-finite numeric overflow was accepted"));
    }
    return true;
}

} // namespace

int main(int argc, char** argv)
{
    QCoreApplication application(argc, argv);
    const QStringList arguments = application.arguments();
    if (arguments.size() != 3) {
        std::cerr << "usage: cad_assist_contract_tests <positive|negative|limits> <fixture-dir>\n";
        return 2;
    }
    const QString mode = arguments.at(1);
    const QString fixtureDir = arguments.at(2);
    bool ok = false;
    if (mode == QStringLiteral("positive")) {
        ok = positive(fixtureDir);
    } else if (mode == QStringLiteral("negative")) {
        ok = negative(fixtureDir);
    } else if (mode == QStringLiteral("limits")) {
        ok = limits(fixtureDir);
    } else {
        std::cerr << "unknown mode: " << mode.toStdString() << '\n';
        return 2;
    }
    return ok ? 0 : 1;
}
