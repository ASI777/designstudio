// InkscapeHost.cpp — see InkscapeHost.h.
#include "InkscapeHost.h"

#include <QProcess>
#include <QProcessEnvironment>
#include <QWindow>
#include <QVBoxLayout>
#include <QLabel>
#include <QTimer>
#include <QGuiApplication>
#include <QFileInfo>
#include <QDir>
#include <QRegularExpression>

// ── Static discovery ─────────────────────────────────────────────────────────
QString InkscapeHost::inkscapeRoot() {
    const QByteArray env = qgetenv("DESIGNSTUDIO_INKSCAPE_ROOT");
    if (!env.isEmpty()) return QString::fromLocal8Bit(env);
    return QDir::homePath() + "/Desktop/pcb_designer/usr";
}

bool InkscapeHost::available() {
    return QFileInfo(inkscapeRoot() + "/bin/inkscape").isExecutable();
}

bool InkscapeHost::canEmbed() {
    return QGuiApplication::platformName() == QLatin1String("xcb");
}

// ── Construction ─────────────────────────────────────────────────────────────
InkscapeHost::InkscapeHost(QWidget* parent) : QWidget(parent) {
    m_lay = new QVBoxLayout(this);
    m_lay->setContentsMargins(0, 0, 0, 0);
    m_lay->setSpacing(0);

    m_overlay = new QLabel(this);
    m_overlay->setAlignment(Qt::AlignCenter);
    m_overlay->setWordWrap(true);
    m_overlay->setStyleSheet("color:#cfcfcf; padding:24px;");
    m_overlay->setText("Embedded Inkscape — open a footprint to launch.");
    m_lay->addWidget(m_overlay);

    m_poll = new QTimer(this);
    m_poll->setInterval(350);
    connect(m_poll, &QTimer::timeout, this, &InkscapeHost::poll);
}

InkscapeHost::~InkscapeHost() { shutdown(); }

bool InkscapeHost::isRunning() const {
    return m_proc && m_proc->state() != QProcess::NotRunning;
}

void InkscapeHost::setOverlay(const QString& text) {
    if (m_overlay) { m_overlay->setText(text); m_overlay->show(); }
}

// ── Launch + embed ───────────────────────────────────────────────────────────
void InkscapeHost::openFile(const QString& svgPath) {
    if (!available()) {
        setOverlay("Inkscape not found at:\n" + inkscapeRoot() +
                   "\n\nSet DESIGNSTUDIO_INKSCAPE_ROOT to the staged tree.");
        emit failed("inkscape-not-found");
        return;
    }
    shutdown();                 // restart cleanly if already running
    m_file = svgPath;
    launch(svgPath);
}

void InkscapeHost::launch(const QString& svgPath) {
    const QString root = inkscapeRoot();
    const QString bin  = root + "/bin/inkscape";
    const QString libs = root + "/lib/x86_64-linux-gnu/inkscape";
    const QString data = root + "/share";

    m_proc = new QProcess(this);
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    env.insert("GDK_BACKEND", "x11");           // force X11 so we can reparent
    env.insert("INKSCAPE_DATADIR", data);
    const QString prev = env.value("LD_LIBRARY_PATH");
    env.insert("LD_LIBRARY_PATH", prev.isEmpty() ? libs : libs + ":" + prev);
    m_proc->setProcessEnvironment(env);
    m_proc->setProgram(bin);
    m_proc->setArguments({ svgPath });
    connect(m_proc, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, [this](int, QProcess::ExitStatus) { emit status("Inkscape closed"); });
    m_proc->start();
    m_pid  = m_proc->processId();
    m_tries = 0;

    if (canEmbed()) {
        setOverlay("Launching Inkscape…");
        m_poll->start();
    } else {
        setOverlay("Inkscape opened in a separate window.\n\n"
                   "Embedding needs the X11/xcb platform — relaunch DesignStudio "
                   "with QT_QPA_PLATFORM=xcb to dock it here.");
        emit status("Inkscape launched (detached — host is not xcb)");
    }
}

void InkscapeHost::poll() {
    if (m_container) { m_poll->stop(); return; }
    if (!isRunning()) { m_poll->stop(); setOverlay("Inkscape exited before a window appeared."); return; }
    if (++m_tries > 40) {                          // ~14 s
        m_poll->stop();
        setOverlay("Could not locate the Inkscape window to embed.");
        emit failed("window-not-found");
        return;
    }

    const QString id = findWindowId();
    if (id.isEmpty()) return;

    bool ok = false;
    const WId xid = id.startsWith("0x") ? id.mid(2).toULongLong(&ok, 16)
                                        : id.toULongLong(&ok, 10);
    if (!ok || xid == 0) return;

    m_foreign = QWindow::fromWinId(xid);
    if (!m_foreign) { setOverlay("QWindow::fromWinId failed for " + id); return; }

    m_container = QWidget::createWindowContainer(m_foreign, this);
    m_container->setFocusPolicy(Qt::StrongFocus);
    m_container->setMinimumSize(480, 360);
    m_overlay->hide();
    m_lay->addWidget(m_container);

    m_poll->stop();
    emit status("Inkscape embedded");
    emit embedded();
}

// Enumerate X windows and return the hex id of our Inkscape main window.
// Strategy: candidates are top-levels whose WM_CLASS instance is
// "org.inkscape.Inkscape" and whose geometry is real (skip the tiny helper
// window). Prefer the one whose _NET_WM_PID matches our child PID; else the one
// whose title carries the file name; else the largest.
QString InkscapeHost::findWindowId() const {
    QProcess xw;
    xw.start("xwininfo", {"-root", "-tree"});
    if (!xw.waitForFinished(2000)) { xw.kill(); return {}; }
    const QString out = QString::fromLocal8Bit(xw.readAllStandardOutput());

    static const QRegularExpression re(
        R"RX((0x[0-9a-f]+)\s+(?:"([^"]*)"\s*)?:\s*\("org\.inkscape\.Inkscape"[^)]*\)\s+(\d+)x(\d+))RX");
    static const QRegularExpression pidRe(R"(_NET_WM_PID\(\w+\)\s*=\s*(\d+))");

    const QString base = QFileInfo(m_file).fileName();
    QString byPid, byTitle, biggest;
    long bestArea = 0;

    auto it = re.globalMatch(out);
    while (it.hasNext()) {
        const auto m = it.next();
        const QString id    = m.captured(1);
        const QString title = m.captured(2);
        const long w = m.captured(3).toLong();
        const long h = m.captured(4).toLong();
        if (w < 50 || h < 50) continue;                  // skip helper windows
        const long area = w * h;
        if (area > bestArea) { bestArea = area; biggest = id; }
        if (!base.isEmpty() && title.contains(base)) byTitle = id;

        if (m_pid > 0 && byPid.isEmpty()) {
            QProcess xp;
            xp.start("xprop", {"-id", id, "_NET_WM_PID"});
            if (xp.waitForFinished(800)) {
                const auto pm = pidRe.match(QString::fromLocal8Bit(xp.readAllStandardOutput()));
                if (pm.hasMatch() && pm.captured(1).toLongLong() == m_pid) byPid = id;
            }
        }
    }
    if (!byPid.isEmpty())   return byPid;
    if (!byTitle.isEmpty()) return byTitle;
    return biggest;
}

// ── Teardown ─────────────────────────────────────────────────────────────────
void InkscapeHost::shutdown() {
    if (m_poll) m_poll->stop();

    if (m_proc) {                       // stop the child before tearing down the view
        if (m_proc->state() != QProcess::NotRunning) {
            m_proc->terminate();
            if (!m_proc->waitForFinished(1500)) m_proc->kill();
        }
        m_proc->deleteLater();
        m_proc = nullptr;
    }
    if (m_container) {
        m_lay->removeWidget(m_container);
        m_container->deleteLater();     // releases the foreign QWindow wrapper too
        m_container = nullptr;
        m_foreign   = nullptr;
    }
    m_pid = -1;
    if (m_overlay) m_overlay->show();
}
