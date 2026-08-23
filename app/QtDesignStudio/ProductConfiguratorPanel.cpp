#include "ProductConfiguratorPanel.h"

#include "AgentdClient.h"

#include <QFrame>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QPushButton>
#include <QScrollArea>
#include <QVBoxLayout>

namespace {
QString compact(const QJsonValue& value) {
    if (value.isString()) return value.toString();
    if (value.isDouble()) return QString::number(value.toDouble(), 'g', 6);
    if (value.isBool()) return value.toBool() ? QStringLiteral("true") : QStringLiteral("false");
    if (value.isObject()) return QString::fromUtf8(
        QJsonDocument(value.toObject()).toJson(QJsonDocument::Compact));
    if (value.isArray()) return QString::fromUtf8(
        QJsonDocument(value.toArray()).toJson(QJsonDocument::Compact));
    return QStringLiteral("—");
}

QString joinStrings(const QJsonArray& values) {
    QStringList parts;
    for (const QJsonValue& value : values) parts.append(value.toString());
    return parts.join(QStringLiteral("; "));
}

QString configurationId(const QJsonObject& result) {
    return result.value(QStringLiteral("configuration")).toObject()
        .value(QStringLiteral("configuration_id")).toString();
}
} // namespace

ProductConfiguratorPanel::ProductConfiguratorPanel(QWidget* parent) : QWidget(parent) {
    auto* root = new QVBoxLayout(this);
    m_heading = new QLabel(QStringLiteral("Design alternatives appear when real candidates exist."), this);
    m_heading->setWordWrap(true);
    root->addWidget(m_heading);
    m_configuration = new QLabel(this);
    m_configuration->setTextInteractionFlags(Qt::TextSelectableByMouse);
    root->addWidget(m_configuration);
    m_selection = new QLabel(QStringLiteral("Selection: none"), this);
    m_selection->setWordWrap(true);
    m_selection->setTextInteractionFlags(Qt::TextSelectableByMouse);
    root->addWidget(m_selection);

    auto* scroll = new QScrollArea(this);
    scroll->setWidgetResizable(true);
    auto* cardHost = new QWidget(scroll);
    m_cards = new QVBoxLayout(cardHost);
    m_cards->addStretch(1);
    scroll->setWidget(cardHost);
    root->addWidget(scroll, 1);

    m_preview = new QLabel(QStringLiteral("Preview is an isolated visual overlay and never mutates the baseline."), this);
    m_preview->setWordWrap(true);
    root->addWidget(m_preview);
    auto* actions = new QHBoxLayout;
    m_revert = new QPushButton(QStringLiteral("Revert"), this);
    m_commit = new QPushButton(QStringLiteral("Save Revision"), this);
    actions->addWidget(m_revert);
    actions->addWidget(m_commit);
    root->addLayout(actions);
    connect(m_revert, &QPushButton::clicked, this, [this] {
        if (m_history.size() < 2) return;
        m_history.removeLast();
        m_currentConfigurationId = m_history.back();
        m_currentConfigurationState = m_stateByConfiguration.value(m_currentConfigurationId);
        m_preview->setText(QStringLiteral("Selected ancestor configuration; no snapshot was changed."));
        updateActions();
        emit statusMessage(QStringLiteral("Reverted by selecting immutable ancestor %1")
                               .arg(m_currentConfigurationId));
    });
    connect(m_commit, &QPushButton::clicked, this, [this] {
        if (!m_client || m_currentConfigurationId.isEmpty()) return;
        m_client->invoke(QStringLiteral("configuration/commit"),
                         {{QStringLiteral("configuration_id"), m_currentConfigurationId}},
                         QStringLiteral("configuration:write"));
    });
    updateActions();
}

void ProductConfiguratorPanel::setSelectedSemanticObject(
    const QString& semanticId, const QString& referenceDesignator, const QString& source)
{
    if (semanticId.isEmpty() && referenceDesignator.isEmpty()) {
        m_selection->setText(QStringLiteral("Selection: none"));
        return;
    }
    const QString object = referenceDesignator.isEmpty()
        ? semanticId : QStringLiteral("%1 (%2)").arg(referenceDesignator, semanticId);
    m_selection->setText(QStringLiteral("Selected object: %1 · source: %2")
                             .arg(object, source.isEmpty() ? QStringLiteral("unknown") : source));
}

void ProductConfiguratorPanel::setClient(AgentdClient* client) {
    if (m_client == client) return;
    if (m_client) disconnect(m_client, nullptr, this, nullptr);
    m_client = client;
    if (!m_client) return;
    connect(m_client, &AgentdClient::workspaceOpened, this,
            [this](const QString&, qint64, qint64, qint64, bool) { requestProduct(); });
    connect(m_client, &AgentdClient::responseReceived,
            this, &ProductConfiguratorPanel::handleResponse);
    connect(m_client, &AgentdClient::requestFailed, this,
            [this](const QString& method, const QString& message) {
        m_preview->setText(QStringLiteral("%1 failed: %2").arg(method, message));
        emit statusMessage(m_preview->text());
    });
}

void ProductConfiguratorPanel::requestProduct() {
    m_client->invoke(QStringLiteral("product/read"), {}, QStringLiteral("product:read"));
}

void ProductConfiguratorPanel::clearCards() {
    while (m_cards->count() > 1) {
        QLayoutItem* item = m_cards->takeAt(0);
        delete item->widget();
        delete item;
    }
}

void ProductConfiguratorPanel::rebuild(const QJsonObject& result) {
    clearCards();
    const QJsonObject workspace = result.value(QStringLiteral("workspace")).toObject();
    const QJsonObject product = workspace.value(QStringLiteral("product")).toObject();
    m_heading->setText(QStringLiteral("%1 · revision %2")
        .arg(product.value(QStringLiteral("name")).toString())
        .arg(workspace.value(QStringLiteral("revision")).toInteger()));
    m_applicationPrompt = product.value(QStringLiteral("name")).toString();
    const QString description = product.value(QStringLiteral("description")).toString();
    if (!description.isEmpty()) m_applicationPrompt += QStringLiteral(" ") + description;
    const QJsonObject baseline = result.value(QStringLiteral("baseline_configuration")).toObject();
    m_currentConfigurationId = baseline.value(QStringLiteral("configuration_id")).toString();
    m_currentConfigurationState = baseline.value(QStringLiteral("state")).toString();
    m_stateByConfiguration.clear();
    m_stateByConfiguration.insert(m_currentConfigurationId, m_currentConfigurationState);
    m_history = {m_currentConfigurationId};

    // The application contract is the user-facing whole-product view. The
    // legacy graph is still loaded below for compatibility with existing
    // workspaces, then replaced when the deterministic application result
    // arrives.
    m_client->invoke(QStringLiteral("application/resolve"), {
        {QStringLiteral("prompt"), m_applicationPrompt}}, QStringLiteral("application:read"));

    const QJsonObject graph = result.value(QStringLiteral("graph")).toObject();
    m_nodeIdBySlot.clear();
    m_graphNodeIds.clear();
    for (const QJsonValue& nodeValue : graph.value(QStringLiteral("nodes")).toArray()) {
        const QString nodeId = nodeValue.toObject().value(QStringLiteral("id")).toString();
        if (!nodeId.isEmpty()) m_graphNodeIds.append(nodeId);
    }
    for (const QJsonValue& slotValue : graph.value(QStringLiteral("slots")).toArray()) {
        const QJsonObject slot = slotValue.toObject();
        const QString slotId = slot.value(QStringLiteral("id")).toString();
        const QString nodeId = slot.value(QStringLiteral("node_id")).toString();
        if (!slotId.isEmpty() && !nodeId.isEmpty()) m_nodeIdBySlot.insert(slotId, nodeId);
    }
    const QJsonArray variants = graph.value(QStringLiteral("variants")).toArray();
    QJsonArray realVariants;
    for (const QJsonValue& value : variants) {
        const QJsonObject variant = value.toObject();
        const QString id = variant.value(QStringLiteral("id")).toString();
        const QString name = variant.value(QStringLiteral("name")).toString();
        const QString state = variant.value(QStringLiteral("state")).toString();
        const bool placeholder = id.endsWith(QStringLiteral(".unconfigured"), Qt::CaseInsensitive)
            || name.endsWith(QStringLiteral(".unconfigured"), Qt::CaseInsensitive)
            || state.compare(QStringLiteral("unconfigured"), Qt::CaseInsensitive) == 0;
        if (!placeholder) realVariants.append(variant);
    }
    if (realVariants.isEmpty()) {
        m_heading->setText(QStringLiteral("No design alternatives are available for this product yet."));
        m_preview->setText(QStringLiteral("The baseline remains active; placeholder configurations are hidden."));
        emit alternativesAvailable(false);
        updateActions();
        return;
    }
    emit alternativesAvailable(true);
    for (const QJsonValue& slotValue : graph.value(QStringLiteral("slots")).toArray()) {
        const QJsonObject slot = slotValue.toObject();
        const QString slotId = slot.value(QStringLiteral("id")).toString();
        auto* group = new QGroupBox(slot.value(QStringLiteral("name")).toString(), this);
        auto* groupLayout = new QVBoxLayout(group);
        for (const QJsonValue& variantValue : realVariants) {
            const QJsonObject variant = variantValue.toObject();
            if (variant.value(QStringLiteral("slot_id")).toString() != slotId) continue;
            const QString variantId = variant.value(QStringLiteral("id")).toString();
            auto* card = new QFrame(group);
            card->setFrameShape(QFrame::StyledPanel);
            auto* cardLayout = new QVBoxLayout(card);
            auto* title = new QLabel(variant.value(QStringLiteral("name")).toString(), card);
            cardLayout->addWidget(title);
            cardLayout->addWidget(new QLabel(
                QStringLiteral("Best for: a product trade-off comparison before detailed engineering."), card));
            cardLayout->addWidget(new QLabel(
                QStringLiteral("Benefits: compare fit, cost, and performance before choosing a direction."), card));
            cardLayout->addWidget(new QLabel(
                QStringLiteral("Drawbacks: sizing, thermal behavior, and manufacturing evidence are not yet proven."), card));
            auto* technical = new QGroupBox(QStringLiteral("Technical Details"), card);
            technical->setCheckable(true);
            technical->setChecked(false);
            auto* technicalLayout = new QVBoxLayout(technical);
            technicalLayout->addWidget(new QLabel(
                QStringLiteral("Internal slot: %1 · assembly path: %2")
                    .arg(slotId, slot.value(QStringLiteral("assembly_path")).toString()), technical));
            const QJsonObject parameters = variant.value(QStringLiteral("parameters")).toObject();
            for (auto it = parameters.begin(); it != parameters.end(); ++it)
                technicalLayout->addWidget(new QLabel(QStringLiteral("%1: %2").arg(it.key(), compact(it.value())), technical));
            const QJsonObject metrics = variant.value(QStringLiteral("metrics")).toObject();
            static const QStringList metricKeys{
                QStringLiteral("dimensions_mm"), QStringLiteral("mass_g"),
                QStringLiteral("power_w"), QStringLiteral("runtime_h"),
                QStringLiteral("clearance_mm"), QStringLiteral("cost"),
                QStringLiteral("thermal_effect_c"), QStringLiteral("confidence"),
                QStringLiteral("evidence")};
            for (const QString& key : metricKeys) {
                if (metrics.contains(key))
                    technicalLayout->addWidget(new QLabel(
                        QStringLiteral("%1: %2").arg(key, compact(metrics.value(key))), technical));
            cardLayout->addWidget(technical);
            }
            const QJsonArray failed = variant.value(QStringLiteral("failed_gates")).toArray();
            const QJsonArray incomplete = variant.value(QStringLiteral("incomplete_gates")).toArray();
            auto* gates = new QLabel(QStringLiteral("Gates: %1 failed · %2 incomplete")
                .arg(failed.size()).arg(incomplete.size()), card);
            gates->setStyleSheet(failed.isEmpty() && incomplete.isEmpty()
                ? QStringLiteral("color:#3fb950;") : QStringLiteral("color:#f85149;"));
            cardLayout->addWidget(gates);
            auto* row = new QHBoxLayout;
            auto* previewButton = new QPushButton(QStringLiteral("Preview"), card);
            auto* equipButton = new QPushButton(QStringLiteral("Apply Draft"), card);
            auto* compareButton = new QPushButton(QStringLiteral("Compare"), card);
            row->addWidget(previewButton); row->addWidget(equipButton); row->addWidget(compareButton);
            cardLayout->addLayout(row);
            connect(previewButton, &QPushButton::clicked, this,
                    [this, slotId, variantId] { preview(slotId, variantId); });
            connect(equipButton, &QPushButton::clicked, this,
                    [this, slotId, variantId] { equip(slotId, variantId); });
            connect(compareButton, &QPushButton::clicked, this,
                    [this, variant] {
                Q_UNUSED(variant);
                m_preview->setText(QStringLiteral(
                    "Compared with the baseline. Failed and incomplete gates remain visible on the card."));
                emit statusMessage(QStringLiteral("Alternative compared with baseline; failed gates remain explicit"));
            });
            groupLayout->addWidget(card);
        }
        m_cards->insertWidget(m_cards->count() - 1, group);
    }
    updateActions();
}

void ProductConfiguratorPanel::rebuildApplication(const QJsonObject& result) {
    const QJsonObject application = result.value(QStringLiteral("application")).toObject();
    const QJsonArray options = application.value(QStringLiteral("options")).toArray();
    if (options.size() != 3) return;
    clearCards();
    m_heading->setText(QStringLiteral("%1 · three product directions")
        .arg(application.value(QStringLiteral("family_name")).toString()));
    const QJsonArray questions = application.value(QStringLiteral("next_questions")).toArray();
    if (!questions.isEmpty()) {
        auto* questionBox = new QGroupBox(QStringLiteral("A few important questions"), this);
        auto* questionLayout = new QVBoxLayout(questionBox);
        for (const QJsonValue& value : questions) {
            const QJsonObject question = value.toObject();
            questionLayout->addWidget(new QLabel(
                QStringLiteral("%1\nWhy it matters: %2")
                    .arg(question.value(QStringLiteral("prompt")).toString(),
                         question.value(QStringLiteral("why_it_matters")).toString()), questionBox));
        }
        m_cards->insertWidget(m_cards->count() - 1, questionBox);
    }
    emit alternativesAvailable(true);
    for (const QJsonValue& value : options) {
        const QJsonObject option = value.toObject();
        auto* card = new QFrame(this);
        card->setFrameShape(QFrame::StyledPanel);
        auto* layout = new QVBoxLayout(card);
        layout->addWidget(new QLabel(option.value(QStringLiteral("name")).toString(), card));
        layout->addWidget(new QLabel(QStringLiteral("Best for: %1")
            .arg(option.value(QStringLiteral("best_for")).toString()), card));
        layout->addWidget(new QLabel(QStringLiteral("Benefits: %1")
            .arg(joinStrings(option.value(QStringLiteral("benefits")).toArray())), card));
        layout->addWidget(new QLabel(QStringLiteral("Drawbacks: %1")
            .arg(joinStrings(option.value(QStringLiteral("drawbacks")).toArray())), card));
        const QJsonObject estimates = option.value(QStringLiteral("estimates")).toObject();
        for (const QString& key : {QStringLiteral("size"), QStringLiteral("cost"), QStringLiteral("power"), QStringLiteral("performance")}) {
            if (estimates.contains(key)) {
                const QJsonObject estimate = estimates.value(key).toObject();
                layout->addWidget(new QLabel(QStringLiteral("%1: %2")
                    .arg(key.left(1).toUpper() + key.mid(1), estimate.value(QStringLiteral("value")).toString()), card));
            }
        }
        const QJsonArray assumptions = option.value(QStringLiteral("assumptions")).toArray();
        layout->addWidget(new QLabel(QStringLiteral("Assumptions: %1")
            .arg(joinStrings(assumptions)), card));
        const QJsonArray failed = option.value(QStringLiteral("failed_gates")).toArray();
        const QJsonArray unresolved = option.value(QStringLiteral("unresolved_gates")).toArray();
        auto* gates = new QLabel(QStringLiteral("Checks: %1 failed · %2 still incomplete")
            .arg(failed.size()).arg(unresolved.size()), card);
        gates->setStyleSheet(failed.isEmpty() && unresolved.isEmpty()
            ? QStringLiteral("color:#3fb950;") : QStringLiteral("color:#f85149;"));
        layout->addWidget(gates);
        if (!failed.isEmpty())
            layout->addWidget(new QLabel(QStringLiteral("Failed: %1").arg(joinStrings(failed)), card));
        if (!unresolved.isEmpty())
            layout->addWidget(new QLabel(QStringLiteral("Still to prove: %1").arg(joinStrings(unresolved)), card));
        auto* technical = new QGroupBox(QStringLiteral("Technical Details"), card);
        technical->setCheckable(true);
        technical->setChecked(false);
        auto* technicalLayout = new QVBoxLayout(technical);
        technicalLayout->addWidget(new QLabel(QStringLiteral("Evidence: %1")
            .arg(option.value(QStringLiteral("evidence_state")).toString()), technical));
        technicalLayout->addWidget(new QLabel(compact(option.value(QStringLiteral("technical_details"))), technical));
        layout->addWidget(technical);
        auto* actions = new QHBoxLayout;
        auto* previewButton = new QPushButton(QStringLiteral("Preview"), card);
        auto* applyButton = new QPushButton(QStringLiteral("Approve and apply draft"), card);
        actions->addWidget(previewButton);
        actions->addWidget(applyButton);
        layout->addLayout(actions);
        const QString optionId = option.value(QStringLiteral("id")).toString();
        connect(previewButton, &QPushButton::clicked, this,
                [this, optionId] { previewApplication(optionId); });
        connect(applyButton, &QPushButton::clicked, this,
                [this, optionId] { applyApplication(optionId); });
        m_cards->insertWidget(m_cards->count() - 1, card);
    }
    updateActions();
}

void ProductConfiguratorPanel::preview(const QString& slotId, const QString& variantId) {
    m_client->invoke(QStringLiteral("variant/preview"), {
        {QStringLiteral("configuration_id"), m_currentConfigurationId},
        {QStringLiteral("slot_id"), slotId},
        {QStringLiteral("variant_id"), variantId}}, QStringLiteral("variant:read"));
}

void ProductConfiguratorPanel::equip(const QString& slotId, const QString& variantId) {
    m_client->invoke(QStringLiteral("variant/equip"), {
        {QStringLiteral("configuration_id"), m_currentConfigurationId},
        {QStringLiteral("slot_id"), slotId},
        {QStringLiteral("variant_id"), variantId}}, QStringLiteral("configuration:write"));
}

void ProductConfiguratorPanel::previewApplication(const QString& optionId) {
    m_client->invoke(QStringLiteral("application/preview"), {
        {QStringLiteral("prompt"), m_applicationPrompt},
        {QStringLiteral("option_id"), optionId}}, QStringLiteral("application:read"));
}

void ProductConfiguratorPanel::applyApplication(const QString& optionId) {
    m_client->invoke(QStringLiteral("application/apply"), {
        {QStringLiteral("prompt"), m_applicationPrompt},
        {QStringLiteral("option_id"), optionId},
        {QStringLiteral("configuration_id"), m_currentConfigurationId},
        {QStringLiteral("approved"), true}}, QStringLiteral("configuration:write"));
}

void ProductConfiguratorPanel::handleResponse(const QString& method, const QJsonObject& result) {
    if (method == QStringLiteral("product/read")) {
        rebuild(result);
    } else if (method == QStringLiteral("application/resolve")) {
        rebuildApplication(result);
    } else if (method == QStringLiteral("application/preview")) {
        m_preview->setText(QStringLiteral(
            "Preview only · the selected product direction has not changed the baseline."));
        emit statusMessage(QStringLiteral("Product direction preview computed without persistence."));
    } else if (method == QStringLiteral("application/apply")) {
        const QString child = configurationId(result);
        if (!child.isEmpty()) {
            m_parentByConfiguration.insert(child, m_currentConfigurationId);
            m_currentConfigurationId = child;
            m_currentConfigurationState = QStringLiteral("sandbox");
            m_stateByConfiguration.insert(child, m_currentConfigurationState);
            m_history.append(child);
            m_preview->setText(QStringLiteral(
                "Draft applied in an isolated child; baseline and authoritative documents are unchanged."));
            emit statusMessage(QStringLiteral("Product direction applied as an isolated draft."));
            emit configurationChanged(m_currentConfigurationId,
                                      QStringLiteral("whole-product direction applied"),
                                      m_graphNodeIds);
        }
    } else if (method == QStringLiteral("variant/preview")) {
        m_preview->setText(QStringLiteral(
            "Preview only · the baseline and authoritative documents are unchanged."));
        emit statusMessage(QStringLiteral("Variant preview computed without persistence"));
    } else if (method == QStringLiteral("variant/equip")) {
        const QString child = configurationId(result);
        if (!child.isEmpty()) {
            m_parentByConfiguration.insert(child, m_currentConfigurationId);
            m_currentConfigurationId = child;
            m_currentConfigurationState = QStringLiteral("sandbox");
            m_stateByConfiguration.insert(child, m_currentConfigurationState);
            m_history.append(child);
            m_preview->setText(QStringLiteral("Draft applied in an isolated child; baseline unchanged."));
            emit statusMessage(QStringLiteral("Applied draft in an isolated child."));
            const QString nodeId = m_nodeIdBySlot.value(
                result.value(QStringLiteral("slot_id")).toString());
            emit configurationChanged(m_currentConfigurationId,
                                      QStringLiteral("variant applied to slot"),
                                      nodeId.isEmpty() ? m_graphNodeIds : QStringList{nodeId});
        }
    } else if (method == QStringLiteral("configuration/commit")) {
        const QString child = configurationId(result);
        if (!child.isEmpty()) {
            m_parentByConfiguration.insert(child, m_currentConfigurationId);
            m_currentConfigurationId = child;
            m_currentConfigurationState = QStringLiteral("committed");
            m_stateByConfiguration.insert(child, m_currentConfigurationState);
            m_history.append(child);
            m_preview->setText(QStringLiteral("Saved as an immutable product revision; draft unchanged."));
            emit statusMessage(QStringLiteral("Saved immutable product revision."));
        }
    }
    updateActions();
}

void ProductConfiguratorPanel::updateActions() {
    m_configuration->setText(m_currentConfigurationId.isEmpty()
        ? QStringLiteral("No active configuration")
        : QStringLiteral("Active product draft · %1")
              .arg(m_currentConfigurationState));
    m_revert->setEnabled(m_history.size() > 1);
    m_commit->setEnabled(m_client && m_client->state() == AgentdClient::State::Ready
                         && !m_currentConfigurationId.isEmpty()
                         && m_currentConfigurationState == QStringLiteral("sandbox"));
}
