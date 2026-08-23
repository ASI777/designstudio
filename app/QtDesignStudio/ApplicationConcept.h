#pragma once

#include <QString>

class ProjectModel;

namespace designstudio {

struct ApplicationConceptRequest {
    QString family;
    double inputMinV{18.0};
    double inputMaxV{30.0};
    QString interfaceName;
    int axisCount{1};
    double axisCurrentA{2.0};
};

struct ApplicationConceptResult {
    bool ok{false};
    QString family;
    QString summary;
    QString error;
    int axisCount{0};
};

// Uses the same stable family identifiers as application/resolve in agentd.
QString classifyApplicationFamily(const QString& ordinaryLanguage);
QString requirementProfileForFamily(const QString& family);
int axisCountFromPrompt(const QString& ordinaryLanguage, int fallback = 1);

// Replaces the electronics in model with a deterministic concept for the
// selected family. Unsupported/custom families fail explicitly; they never
// silently receive another family's circuit.
ApplicationConceptResult buildApplicationConcept(
    ProjectModel& model, const ApplicationConceptRequest& request);

} // namespace designstudio
