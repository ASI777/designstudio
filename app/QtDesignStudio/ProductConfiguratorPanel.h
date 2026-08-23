#pragma once

#include <QHash>
#include <QJsonObject>
#include <QStringList>
#include <QWidget>

class AgentdClient;
class QLabel;
class QPushButton;
class QVBoxLayout;

// Whole-product option browser backed exclusively by immutable control-plane
// operations. Preview is display-only; applying and committing return child
// snapshots. Raw rule fields remain behind Technical Details.
class ProductConfiguratorPanel final : public QWidget {
    Q_OBJECT
public:
    explicit ProductConfiguratorPanel(QWidget* parent = nullptr);
    void setClient(AgentdClient* client);
    // Shows the shared semantic object currently selected in another product
    // surface; this panel remains read-only until a typed proposal is applied.
    void setSelectedSemanticObject(const QString& semanticId,
                                   const QString& referenceDesignator,
                                   const QString& source);

signals:
    void statusMessage(const QString& message);
    void alternativesAvailable(bool available);
    // A typed product edit is the trigger for coupled physical-intelligence
    // propagation.  Node IDs come from the authoritative product graph, not
    // from display labels or meshes.
    void configurationChanged(const QString& configurationId,
                              const QString& reason,
                              const QStringList& changedNodeIds);

private:
    AgentdClient* m_client{};
    QLabel* m_heading{};
    QLabel* m_configuration{};
    QLabel* m_selection{};
    QLabel* m_preview{};
    QVBoxLayout* m_cards{};
    QPushButton* m_revert{};
    QPushButton* m_commit{};
    QString m_currentConfigurationId;
    QStringList m_history;
    QHash<QString, QString> m_parentByConfiguration;
    QHash<QString, QString> m_stateByConfiguration;
    QHash<QString, QString> m_nodeIdBySlot;
    QStringList m_graphNodeIds;
    QString m_currentConfigurationState;
    QString m_applicationPrompt;

    void requestProduct();
    void rebuild(const QJsonObject& result);
    void rebuildApplication(const QJsonObject& result);
    void preview(const QString& slotId, const QString& variantId);
    void equip(const QString& slotId, const QString& variantId);
    void previewApplication(const QString& optionId);
    void applyApplication(const QString& optionId);
    void handleResponse(const QString& method, const QJsonObject& result);
    void updateActions();
    void clearCards();
};
