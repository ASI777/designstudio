// InkscapeHost.h — embeds the *real* Inkscape 1.4.3 inside a DesignStudio panel.
//
// The staged Inkscape tree (default ~/Desktop/pcb_designer/usr, overridable via
// DESIGNSTUDIO_INKSCAPE_ROOT) is launched as a child process forced onto X11
// (GDK_BACKEND=x11). We then locate its top-level window (WM_CLASS instance
// "org.inkscape.Inkscape", bound to our child PID) and reparent it into a Qt
// container via QWindow::fromWinId + QWidget::createWindowContainer.
//
// Embedding requires DesignStudio itself to run on the "xcb" platform (see
// main.cpp). On Wayland/offscreen we fall back to launching Inkscape detached.
#pragma once
#include <QWidget>

class QProcess;
class QWindow;
class QVBoxLayout;
class QLabel;
class QTimer;

class InkscapeHost : public QWidget {
    Q_OBJECT
public:
    explicit InkscapeHost(QWidget* parent = nullptr);
    ~InkscapeHost() override;

    static QString inkscapeRoot();   // staged tree (…/pcb_designer/usr)
    static bool    available();      // launcher exists & is executable
    static bool    canEmbed();       // host app runs on the xcb platform

    void openFile(const QString& svgPath);   // (re)launch Inkscape on this file & embed
    void shutdown();                          // terminate child + detach the window
    bool isRunning() const;

signals:
    void embedded();
    void failed(const QString& reason);
    void status(const QString& msg);

private slots:
    void poll();

private:
    void    launch(const QString& svgPath);
    QString findWindowId() const;    // hex id of our Inkscape main window, or ""
    void    setOverlay(const QString& text);

    QProcess*    m_proc{};
    QString      m_file;
    qint64       m_pid{-1};
    QWindow*     m_foreign{};
    QWidget*     m_container{};
    QVBoxLayout* m_lay{};
    QLabel*      m_overlay{};
    QTimer*      m_poll{};
    int          m_tries{0};
};
