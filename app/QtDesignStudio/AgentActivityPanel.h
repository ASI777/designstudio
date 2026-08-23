#pragma once
#include <QWidget>
#include <QDateTime>
#include <QVector>
#include <QMap>

class QListWidget;
class QListWidgetItem;
class QLabel;
class QToolButton;

// ── One agent run entry ───────────────────────────────────────────────────────
struct AgentRun {
    enum State { Running, Done, Failed };
    QString   name;
    State     state{Running};
    QDateTime started;
    QDateTime ended;
    QStringList lines;  // live output lines
    QListWidgetItem* item{nullptr};
};

// ── Agent Activity Panel ──────────────────────────────────────────────────────
// Live feed of every agent run: shows who's running, streams its output lines,
// marks done/failed with elapsed time. Receives events from DesignChatPanel
// via signal→slot connections wired in MainWindow.
class AgentActivityPanel : public QWidget {
    Q_OBJECT
public:
    explicit AgentActivityPanel(QWidget* parent = nullptr);

public slots:
    void onAgentStarted(const QString& agentName);
    void onAgentFinished(const QString& agentName, bool ok);
    void onAgentOutput(const QString& agentName, const QString& text);

private slots:
    void onClear();
    void onItemExpand(QListWidgetItem* item);

private:
    QListWidget*  m_list;
    QLabel*       m_countLabel;
    QToolButton*  m_clearBtn;

    QMap<QString, AgentRun*> m_runs;  // agent name → current run
    int m_totalRuns{0};
    int m_failed{0};

    AgentRun* currentRun(const QString& name);
    void updateItem(AgentRun* run);
    QString elapsedStr(const AgentRun* run) const;
    static QString stateIcon(AgentRun::State s);
};
