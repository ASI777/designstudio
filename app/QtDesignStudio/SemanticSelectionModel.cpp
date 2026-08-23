#include "SemanticSelectionModel.h"

namespace designstudio {

void SemanticSelectionModel::select(const QString& semanticId,
                                    const QString& referenceDesignator,
                                    const QString& source)
{
    const QString nextSource = source.isEmpty() ? QStringLiteral("unknown") : source;
    if (semanticId_ == semanticId && referenceDesignator_ == referenceDesignator
        && source_ == nextSource)
        return;
    semanticId_ = semanticId;
    referenceDesignator_ = referenceDesignator;
    source_ = nextSource;
    emit selectionChanged(semanticId_, referenceDesignator_, source_);
}

void SemanticSelectionModel::clear(const QString& source)
{
    select({}, {}, source.isEmpty() ? QStringLiteral("unknown") : source);
}

} // namespace designstudio
