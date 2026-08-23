#include "SupportJobClient.h"

#include <QElapsedTimer>
#include <QEventLoop>
#include <QCryptographicHash>
#include <QJsonDocument>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QTimer>

namespace designstudio {
namespace {

struct HttpResult {
    int status{};
    QByteArray body;
    QString error;
};

HttpResult request(QNetworkAccessManager& manager, const QByteArray& method,
                   const QUrl& url, const QByteArray& body, int timeoutMs)
{
    QNetworkRequest request(url);
    request.setHeader(QNetworkRequest::ContentTypeHeader,
                      QStringLiteral("application/json"));
    request.setRawHeader("Accept", "application/json");
    QNetworkReply* reply = manager.sendCustomRequest(request, method, body);
    QEventLoop loop;
    QTimer timer;
    timer.setSingleShot(true);
    QObject::connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
    QObject::connect(&timer, &QTimer::timeout, &loop, &QEventLoop::quit);
    timer.start(timeoutMs);
    loop.exec();
    if (!timer.isActive() && !reply->isFinished()) {
        reply->abort();
        reply->deleteLater();
        return {0, {}, QStringLiteral("gateway request timed out")};
    }
    timer.stop();
    HttpResult result;
    result.status = reply->attribute(
        QNetworkRequest::HttpStatusCodeAttribute).toInt();
    result.body = reply->readAll();
    if (reply->error() != QNetworkReply::NoError)
        result.error = reply->errorString();
    reply->deleteLater();
    return result;
}

QUrl endpoint(const QUrl& base, const QString& path)
{
    QUrl result = base;
    QString normalized = result.path();
    if (normalized.endsWith(QLatin1Char('/'))) normalized.chop(1);
    result.setPath(normalized + path);
    return result;
}

bool parseObject(const QByteArray& bytes, QJsonObject* object, QString* error)
{
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(bytes, &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        if (error) {
            *error = QStringLiteral("gateway returned invalid JSON: %1")
                         .arg(parseError.errorString());
        }
        return false;
    }
    *object = document.object();
    return true;
}

} // namespace

SupportJobClientResult SupportJobClient::submitAndWait(
    const QUrl& gatewayBaseUrl, const QJsonObject& specification,
    int overallTimeoutMs)
{
    SupportJobClientResult result;
    if (!gatewayBaseUrl.isValid()
        || (gatewayBaseUrl.scheme() != QStringLiteral("http")
            && gatewayBaseUrl.scheme() != QStringLiteral("https"))
        || gatewayBaseUrl.host().isEmpty()) {
        result.error = QStringLiteral("AI gateway URL is invalid");
        return result;
    }
    const bool allowRemote = qEnvironmentVariableIntValue(
        "DS_AI_GATEWAY_ALLOW_REMOTE") == 1;
    const QString host = gatewayBaseUrl.host().toLower();
    if (!allowRemote && host != QStringLiteral("localhost")
        && host != QStringLiteral("127.0.0.1")
        && host != QStringLiteral("::1")) {
        result.error = QStringLiteral(
            "remote AI gateway is disabled; use the localhost SSH tunnel");
        return result;
    }
    QNetworkAccessManager manager;
    QElapsedTimer elapsed;
    elapsed.start();
    const QByteArray specificationBytes =
        QJsonDocument(specification).toJson(QJsonDocument::Compact);
    const QString specificationSha = QString::fromLatin1(
        QCryptographicHash::hash(
            specificationBytes, QCryptographicHash::Sha256).toHex());
    const QJsonObject payload{
        {QStringLiteral("spec"), specification},
        {QStringLiteral("idempotency_key"), QStringLiteral("desktop:%1")
             .arg(specificationSha)}
    };
    const HttpResult created = request(
        manager, "POST", endpoint(gatewayBaseUrl, QStringLiteral("/v1/support-jobs")),
        QJsonDocument(payload).toJson(QJsonDocument::Compact),
        qMin(30'000, overallTimeoutMs));
    if (created.status != 200 && created.status != 202) {
        result.error = created.error.isEmpty()
            ? QStringLiteral("support job submission failed with HTTP %1")
                  .arg(created.status)
            : created.error;
        return result;
    }
    QJsonObject job;
    if (!parseObject(created.body, &job, &result.error)) return result;
    result.jobId = job.value(QStringLiteral("job_id")).toString();
    if (result.jobId.isEmpty()) {
        result.error = QStringLiteral("gateway response did not contain a job ID");
        return result;
    }
    QString status = job.value(QStringLiteral("status")).toString();
    while (status != QStringLiteral("completed")) {
        if (status == QStringLiteral("failed") || status == QStringLiteral("cancelled")) {
            result.error = job.value(QStringLiteral("error")).toString(
                QStringLiteral("support job ended as %1").arg(status));
            return result;
        }
        if (elapsed.elapsed() >= overallTimeoutMs) {
            request(manager, "POST",
                    endpoint(gatewayBaseUrl,
                             QStringLiteral("/v1/support-jobs/%1/cancel")
                                 .arg(result.jobId)),
                    QByteArrayLiteral("{}"), 5'000);
            result.error = QStringLiteral("support optimization timed out and was cancelled");
            return result;
        }
        QEventLoop delay;
        QTimer::singleShot(500, &delay, &QEventLoop::quit);
        delay.exec();
        const HttpResult polled = request(
            manager, "GET",
            endpoint(gatewayBaseUrl,
                     QStringLiteral("/v1/support-jobs/%1").arg(result.jobId)),
            {}, qMin(15'000, overallTimeoutMs - int(elapsed.elapsed())));
        if (polled.status != 200 || !parseObject(polled.body, &job, &result.error)) {
            if (result.error.isEmpty())
                result.error = QStringLiteral("support job polling failed");
            return result;
        }
        status = job.value(QStringLiteral("status")).toString();
    }
    const HttpResult fetched = request(
        manager, "GET",
        endpoint(gatewayBaseUrl,
                 QStringLiteral("/v1/support-jobs/%1/result").arg(result.jobId)),
        {}, qMax(1, qMin(15'000, overallTimeoutMs - int(elapsed.elapsed()))));
    if (fetched.status != 200
        || !parseObject(fetched.body, &result.result, &result.error)) {
        if (result.error.isEmpty())
            result.error = QStringLiteral("completed support result could not be fetched");
        return result;
    }
    result.ok = true;
    return result;
}

} // namespace designstudio
