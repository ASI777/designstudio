#pragma once

#include "CadAssistContract.h"

#include <QString>

class ProjectModel;

namespace designstudio {

// Builds the only project expectation accepted by the Qt application. Evidence
// is bound to the saved document identity, in-memory revision, and exact saved
// bytes. Unsaved or modified projects fail closed before contract parsing.
CadAssistLoadResult loadCadAssistForProject(const QString& contractPath,
                                            const ProjectModel& project);

} // namespace designstudio
