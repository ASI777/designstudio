#pragma once
#include <QMainWindow>
#include <QHash>
#include <QJsonObject>
#include <QList>
#include <QStringList>
#include <functional>
#include <memory>

class ProjectModel;
class PcbCanvas;
class SchematicView;
class LayerPanel;
class ComponentPanel;
class DrcPanel;
class DesignChatPanel;
class CadAssistPanel;
class ProductConfiguratorPanel;
class PhysicalDesignPanel;
class PropagationPanel;
class ProductHomePage;
class DesignFlowPanel;
class BuildMapPanel;
namespace designstudio { class EnclosureConceptTab; class Pcb3DView; class SemanticAssemblyPanel; }
namespace designstudio { class SemanticSelectionModel; }
class CoreBridge;
class AgentdClient;
class QLabel;
class QTabWidget;
class QDockWidget;
class QToolBar;
class QActionGroup;
class QProgressBar;
class QWidget;

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    enum class UiProfile {
        Standalone,
        FreeCadEmbedded
    };

    explicit MainWindow(QWidget* parent = nullptr,
                        UiProfile profile = UiProfile::Standalone);
    ~MainWindow();

    // FreeCAD installs one trusted dispatcher here. The agent can select only
    // registered operations; no source text or arbitrary Python crosses it.
    using NativeToolHandler = std::function<QJsonObject(const QString&, const QJsonObject&)>;
    using NativeToolCanceller = std::function<void()>;
    void setNativeToolHandler(NativeToolHandler handler,
                              NativeToolCanceller canceller = {}) {
        m_nativeToolHandler = std::move(handler);
        m_nativeToolCanceller = std::move(canceller);
    }

    bool openFile(const QString& path);
    bool openFileForVerification(const QString& path, QString* errorMessage = nullptr);
    bool openWorkspace(const QString& path, QString* errorMessage = nullptr);
    QString projectFilePath() const;
    void navigateTo(QWidget* widget);
    void setNonInteractive(bool enabled) noexcept { m_nonInteractive = enabled; }
    bool loadCadAssistContract(const QString& path, QString* errorMessage = nullptr);
    bool loadEnclosureGlb(const QString& path, QString* errorMessage = nullptr);
    bool exportEnclosureEvidence(const QString& path, QString* errorMessage = nullptr) const;
    bool exportVerificationReport(const QString& path, QString* errorMessage = nullptr);
    bool generateBalancingSphereDemo(const QString& outputDirectory,
                                      QString* errorMessage = nullptr);
    // Programmatic form of New Product used by native integrations and the
    // cold-start acceptance test. It follows the same one-shot create →
    // FreeCAD document → manifest open → persistent control sequence.
    QJsonObject createProductWorkspace(const QString& name,
                                       const QString& description = {});

Q_SIGNALS:
    void workspaceLifecycleChanged(const QString& state, const QString& detail);
    // Emitted when the user enters or leaves the Mechanical 3D tab so the
    // FreeCAD host module can swap the shared MDI surface to the shaded
    // mechanical document viewport without opening separate windows.
    void mechanicalViewActive(bool active);
    void semanticSelectionChanged(const QString& semanticId,
                                  const QString& referenceDesignator,
                                  const QString& source);

protected:
    void closeEvent(QCloseEvent*) override;
    void dragEnterEvent(QDragEnterEvent*) override;
    void dropEvent(QDropEvent*) override;

private:
    UiProfile m_uiProfile{UiProfile::Standalone};
    bool m_nonInteractive{false};

    // Model
    ProjectModel* m_model{};
    CoreBridge*   m_core{};
    AgentdClient* m_agentd{};

    // Views
    QTabWidget*    m_tabs{};
    ProductHomePage* m_productHome{};
    DesignFlowPanel* m_designFlow{};
    BuildMapPanel*   m_buildMap{};
    QWidget*         m_verificationView{};
    QWidget*         m_mechanicalViewport{};
    PcbCanvas*       m_pcbCanvas{};
    SchematicView*   m_schView{};
    designstudio::Pcb3DView* m_pcb3DView{};
    designstudio::EnclosureConceptTab* m_enclosureConcept{};
    designstudio::SemanticSelectionModel* m_selectionModel{};
    designstudio::SemanticAssemblyPanel* m_semanticAssemblyPanel{};
    PhysicalDesignPanel* m_physicalDesignPanel{};

    // Docks
    LayerPanel*         m_layerPanel{};
    ComponentPanel*     m_compPanel{};
    DrcPanel*           m_drcPanel{};
    DesignChatPanel*    m_chatPanel{};
    CadAssistPanel*      m_cadAssistPanel{};
    ProductConfiguratorPanel* m_configuratorPanel{};
    PropagationPanel* m_propagationPanel{};
    QDockWidget*         m_semanticAssemblyDock{};
    QDockWidget*         m_cadAssistDock{};
    QDockWidget*         m_configuratorDock{};
    QDockWidget*         m_propagationDock{};
    QDockWidget*         m_chatDock{};
    QDockWidget*         m_drcDock{};

    // Status bar
    QLabel*       m_statusLabel{};
    QLabel*       m_coordLabel{};
    QLabel*       m_coreStatusLabel{};
    QLabel*       m_controlPlaneStatusLabel{};
    QProgressBar* m_progressBar{};

    // Actions
    QAction* m_actNew{};
    QAction* m_actOpen{};
    QAction* m_actReload{};
    QAction* m_actSave{};
    QAction* m_actSaveAs{};
    QAction* m_actUndo{};
    QAction* m_actRedo{};
    QAction* m_actZoomIn{};
    QAction* m_actZoomOut{};
    QAction* m_actZoomFit{};
    QAction* m_actSelect{};
    QAction* m_actRoute{};
    QAction* m_actVia{};
    QAction* m_actRunDrc{};
    QAction* m_actAutoRoute{};
    QAction* m_actBoardSetup{};
    QAction* m_actPour{};
    QAction* m_actLoadCadAssist{};
    QAction* m_actLoadKicadSymbols{};
    QAction* m_actCopyCadAssistContext{};
    QAction* m_actToggleCodex{};
    QActionGroup* m_toolGroup{};
    QToolBar* m_contextToolbar{};
    QList<QAction*> m_pcbContextActions;
    QList<QAction*> m_schematicContextActions;
    QList<QAction*> m_pcb3DContextActions;
    QList<QAction*> m_mechanicalContextActions;
    QList<QAction*> m_verificationContextActions;

    void setupModel();
    void setupViews();
    void setupDocks();
    void setupMenus();
    void setupToolbar();
    void setupStatusBar();
    void tryLoadCore();
    void setupControlPlane();
    void loadStyleSheet();

    void onNew();
    void onOpen();
    void onReload();
    void onSave();
    void onSaveAs();
    void onAutoRoute();
    void onBoardSetup();
    void onTabChanged(int index);
    void updateTitle();
    void updateCadAssistAvailability();
    void clearCadAssistSession(const QString& reason);
    void showBoardSetupDialog();
    void onCodexToolRequested(const QString& token, const QString& tool,
                              const QJsonObject& arguments);
    void executeApprovedCodexTool(const QString& token);
    QJsonObject executeCodexTool(const QString& tool, const QJsonObject& arguments);
    QJsonObject createAgentWorkspace(const QJsonObject& arguments);
    QJsonObject applyElectricalConcept(const QJsonObject& arguments);
    QJsonObject applyPcbConcept(const QJsonObject& arguments);
    QJsonObject runAgentChecks();
    QJsonObject commitAgentConfiguration(const QJsonObject& arguments);
    QJsonObject productState() const;
    void refreshEngineeringViews();
    void updateWorkspaceActions(QWidget* workspace);
    void addRecentProduct(const QString& path);

    bool confirmSave();

    void saveForAgents();                          // flush model so agents read latest
    struct PendingCodexTool { QString name; QJsonObject arguments; };
    QHash<QString, PendingCodexTool> m_pendingCodexTools;
    NativeToolHandler m_nativeToolHandler;
    NativeToolCanceller m_nativeToolCanceller;
    QString m_activeCodexToolToken;
    bool m_activeCodexToolCancelled{false};
    QStringList m_completedAgentStages;
    QString m_activeProductName;
    QString m_activeProductDescription;
    QString m_activeApplicationFamily{QStringLiteral("custom_concept")};
    QString m_activeInterface;
    double m_activeInputMinV{18.0};
    double m_activeInputMaxV{30.0};
    double m_activeAxisCurrentA{2.0};
    int m_activeAxisCount{1};
};
