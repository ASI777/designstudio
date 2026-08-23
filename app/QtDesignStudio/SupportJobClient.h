#pragma once

#include <QJsonObject>
#include <QString>
#include <QUrl>

namespace designstudio {

struct SupportJobClientResult {
    bool ok{};
    QString error;
    QString jobId;
    QJsonObject result;
};

class SupportJobClient final {
public:
    static SupportJobClientResult submitAndWait(
        const QUrl& gatewayBaseUrl, const QJsonObject& specification,
        int overallTimeoutMs = 180'000);
};

} // namespace designstudio
