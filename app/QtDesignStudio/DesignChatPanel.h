#pragma once
#include <QWidget>
#include <QProcess>
#include <QVector>
#include <QPixmap>
#include <QJsonArray>
#include <QJsonObject>
#include <QStringList>

class QTextEdit;
class QLineEdit;
class QPushButton;
class QToolButton;
class QLabel;
class QProgressBar;
class QScrollArea;
class QHBoxLayout;
class QVBoxLayout;
class QComboBox;
class QFrame;
class QMenu;
class ProjectModel;
class CoreBridge;
class CodexAppServerClient;
struct ProjFootprint;

// ── A single chat turn ────────────────────────────────────────────────────────
struct ChatMessage {
    enum Role { User, Assistant, System, Tool };
    Role    role;
    QString text;
    QString toolName;
    bool    streaming{false};
};

// ── Design Chat Panel ─────────────────────────────────────────────────────────
// Full-conversation panel that routes user input to the correct Python agent:
//   • "analyze" / "advise"  → circuit_advisor_agent.py  (LLM + SI/PI rules)
//   • "sipi" / "check"      → sipi_advisor.py            (deterministic, instant)
//   • "extract" / "pdf"     → datasheet_extractor_agent.py
//   • "drc"                 → drc_mcp.py run_full_review
//   • everything else       → Claude API via circuit_advisor_agent.py (free text Q&A)
//
// Streaming: QProcess stdout is appended token-by-token to the current
// assistant bubble, giving the feel of live streaming.
class DesignChatPanel : public QWidget {
    Q_OBJECT
public:
    explicit DesignChatPanel(ProjectModel* model, QWidget* parent = nullptr);
    ~DesignChatPanel() override;

    void appendSystem(const QString& msg);
    // Programmatically submit a prompt (used by the Footprint Lib agent hook).
    void submitPrompt(const QString& text);
    void setProjectPath(const QString& path);
    void setCore(CoreBridge* core) { m_core = core; }
    // Post a finished tool/job result into the chat (called by MainWindow for
    // native in-process jobs like routing/layout).
    void postToolResult(const QString& tool, const QString& text, bool ok = true);
    // Called by MainWindow after native routing finishes to run post-route cleanup.
    void runPostRouteCleanup();
    // Auto-finish chain (place → route → DRC after a successful /build).
    bool autoFinishing() const { return m_autoFinish; }
    void continueAutoFinishAfterRoute();
    void presentCodexApproval(const QString& token, const QString& stage,
                              const QString& summary, const QJsonObject& arguments);
    void completeCodexTool(const QString& token, bool success, const QString& text);
    void completeCodexTool(const QString& token, bool success,
                           const QJsonArray& contentItems);
    void runCodexDatasheetExtraction(const QString& token, const QString& source,
                                     const QString& mpn);
    void runCodexComponentPublication(const QString& token, const QString& manifestPath);
    void runCodexDatasheetProductBuild(const QString& token, const QString& intent);

public slots:
    // Browse + add datasheet PDFs for missing parts (also reachable via a menu).
    void provideDatasheets();
    // Dedicated: pick ONE datasheet PDF and extract/generate just its footprint
    // (no build). Invoked from the Components panel.
    void extractFootprintFromDatasheet();
    // Attach a visual reference PDF to the next Codex turn. The host keeps the
    // original PDF and sends rendered localImage evidence to app-server.
    void attachReferencePdf();
    // Attach one or more immutable PNG/JPEG/WebP references.  The importer
    // preserves originals, derives non-authoritative crops and selects at most
    // eight original-detail representative views.
    void attachReferenceImages();

signals:
    void agentStarted(const QString& agentName);
    void agentFinished(const QString& agentName, bool ok);
    void agentOutput(const QString& agentName, const QString& line);
    // Emitted after the user explicitly cancels Codex.  MainWindow uses this
    // to invalidate any still-visible approval cards without executing them.
    void codexRunStopped();
    // Native in-process job requested (run by MainWindow on the live model).
    void nativeJobRequested(const QString& job);
    void codexToolRequested(const QString& token, const QString& tool,
                            const QJsonObject& arguments);
    void codexToolApprovalResolved(const QString& token, bool approved);

private slots:
    void onSend();
    void newChat();                   // archive current conversation, start fresh
    void onProcessReadyOut();
    void onProcessReadyErr();
    void onProcessFinished(int exitCode, QProcess::ExitStatus);
    void onQuickAction(const QString& action);

private:
    ProjectModel* m_model;
    CoreBridge*   m_core{nullptr};
    QString       m_projectPath;
    QString       m_pendingQuery;   // free-text arg for the next job (e.g. DigiKey MPN)

    // Chat state
    QVector<ChatMessage> m_history;
    QString       m_streamingBuf;
    int           m_streamingMessageIndex{-1};
    bool          m_agentRunning{false};
    QString       m_activeAgent;
    bool          m_autoFinish{false};   // running the post-/build place→route→DRC chain
    QString       m_lastBuildIntent;     // last /build prompt, so /datasheets can resume it

    // UI
    QTextEdit*    m_chatView;
    QTextEdit*    m_technicalDetails{nullptr};
    QLineEdit*    m_input;
    QPushButton*  m_sendBtn;
    QToolButton*  m_attachButton{nullptr};
    QLabel*       m_statusLabel;
    QProgressBar* m_progress;
    QFrame*       m_choiceFrame{nullptr};
    QVBoxLayout*  m_choiceLayout{nullptr};
    QToolButton*  m_optionsButton{nullptr};
    QMenu*        m_optionsMenu{nullptr};
    QScrollArea*  m_fpStrip{nullptr};        // scrollable footprint-thumbnail row
    QHBoxLayout*  m_fpStripLayout{nullptr};
    QComboBox*    m_providerCombo{nullptr};
    QFrame*       m_approvalFrame{nullptr};
    QLabel*       m_approvalTitle{nullptr};
    QLabel*       m_approvalSummary{nullptr};
    QString       m_approvalToken;

    // Agent process
    QProcess*     m_proc{nullptr};
    QProcess*     m_datasheetProc{nullptr};
    QProcess*     m_componentPublishProc{nullptr};
    QProcess*     m_productBuildProc{nullptr};
    CodexAppServerClient* m_codex{nullptr};
    QJsonArray     m_modelCatalog;
    QString        m_selectedModel;
    QString        m_selectedEffort;
    QString        m_selectedServiceTier;
    QString        m_defaultModel;
    QString        m_defaultEffort;
    QString        m_defaultServiceTier;

    // Pending visual reference. The PDF is copied into the active workspace;
    // rendered page PNGs are sent as protocol-native localImage inputs on the
    // next Codex turn and then cleared.
    QString        m_attachedReferencePdf;
    QStringList    m_attachedReferencePages;
    QString        m_attachedReferenceManifest;
    QString        m_attachedReferenceLabel;

    // Helpers
    void addMessage(ChatMessage::Role role, const QString& text, const QString& tool = {});
    void startAssistantBubble(const QString& tool);
    void appendToCurrentBubble(const QString& text);
    void finalizeCurrentBubble();
    void renderAll();
    void updateDecisionChoices(const QString& assistantText);
    QString compactAssistantText(const QString& assistantText) const;
    void logMessage(const ChatMessage& msg);   // append one turn to the daily JSONL log
    QString bubbleHtml(const ChatMessage& msg) const;
    void showComponentPreview(const QString& manifestPath);
    void runAgent(const QString& script, const QStringList& args, const QString& label);
    void stopAgent();
    QString routeInput(const QString& text);
    void    recordOutcome(const QString& outcome);  // feed real result back to design memory
    QString exportCircuitState();
    QString formatDrcReport();        // runs native DRC, returns a chat-ready summary
    void    checkUnrealizedParts();   // after a build, report parts that need datasheets
    void    loadHistory();            // restore this user's chat history on startup
    QString chatLogPath() const;      // per-user persistent chat log
    void    updateFootprintStrip();   // (re)build the thumbnail row from the model
    QPixmap renderFootprintThumb(const struct ProjFootprint& fp, int size) const;
    void updateModelCatalog(const QJsonArray& models);
    void rebuildOptionsMenu();
    void applyCodexOptions();
    void persistCodexOptions() const;
    QJsonObject selectedModelInfo() const;
    QString optionsButtonText() const;
    void updateRunControl(bool running);
    bool usingCodex() const;
    void startCodex();
    QJsonArray referenceInputItems() const;
    bool validateAttachedReference(QString* error = nullptr) const;
    QString referenceWorkspaceRoot() const;
    static QJsonArray codexDynamicTools();
    static QString codexInstructions();

protected:
    bool eventFilter(QObject* obj, QEvent* ev) override;  // wheel→horizontal scroll
};
