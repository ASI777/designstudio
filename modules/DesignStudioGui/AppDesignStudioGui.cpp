// SPDX-License-Identifier: LGPL-2.1-or-later

#include <Base/Interpreter.h>
#include <Base/PyObjectBase.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/MainWindow.h>

#include <QDockWidget>
#include <QAction>
#include <QCoreApplication>
#include <QCloseEvent>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDebug>
#include <QFile>
#include <QFileInfo>
#include <QDir>
#include <QEventLoop>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPointer>
#include <QProcess>
#include <QProcessEnvironment>
#include <QSaveFile>
#include <QStandardPaths>
#include <QStatusBar>
#include <QTemporaryDir>
#include <QMdiArea>
#include <QMdiSubWindow>
#include <QString>
#include <QToolBar>
#include <QTimer>
#include <QVector>
#include <atomic>
#include <chrono>
#include <exception>
#include <string>
#include <thread>
#include <utility>

#include "MainWindow.h"

namespace
{
QPointer<MainWindow> workspaceController;
QPointer<QWidget> workspaceSurface;
QPointer<QMdiSubWindow> workspaceDocument;
QVector<QPointer<QDockWidget>> workspaceDocks;
QVector<QPointer<QToolBar>> workspaceToolbars;
QVector<QPair<QPointer<QToolBar>, bool>> previousHostToolbars;
std::atomic_bool testShutdownObserverArmed{false};
QPointer<QProcess> activeNativeWorker;
std::atomic_bool activeNativeWorkerCancelled{false};

QByteArray jsonDigest(QJsonObject value, const QString& digestKey)
{
    value.remove(digestKey);
    return QCryptographicHash::hash(
        QJsonDocument(value).toJson(QJsonDocument::Compact),
        QCryptographicHash::Sha256).toHex();
}

bool readJsonObject(const QString& path, QJsonObject* result)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) return false;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
    if (!document.isObject()) return false;
    *result = document.object();
    return true;
}

bool writeJsonObject(const QString& path, QJsonObject value,
                     const QString& digestKey = QStringLiteral("receipt_digest"))
{
    value.insert(digestKey, QString::fromLatin1(jsonDigest(value, digestKey)));
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)) return false;
    file.write(QJsonDocument(value).toJson(QJsonDocument::Indented));
    return file.commit();
}

bool writeRawJsonObject(const QString& path, const QJsonObject& value)
{
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)) return false;
    file.write(QJsonDocument(value).toJson(QJsonDocument::Compact));
    return file.commit();
}

void armTestShutdownObserver(Gui::MainWindow* host)
{
    const QString requestPath = QString::fromUtf8(qgetenv("DESIGNSTUDIO_SHUTDOWN_REQUEST"));
    const QString shutdownPath = QString::fromUtf8(qgetenv("DESIGNSTUDIO_SHUTDOWN_RECEIPT"));
    const QString readinessPath = QString::fromUtf8(qgetenv("DESIGNSTUDIO_STARTUP_RECEIPT"));
    const QString token = QString::fromUtf8(qgetenv("DESIGNSTUDIO_STARTUP_RECEIPT_TOKEN"));
    if (!host || requestPath.isEmpty() || shutdownPath.isEmpty()
        || readinessPath.isEmpty() || token.isEmpty()
        || testShutdownObserverArmed.exchange(true))
        return;
    // FreeCAD's MainWindow close handler quits its custom GUI loop only when
    // this property is present.  Set it on the GUI thread while arming.
    host->setProperty("QuitOnClosed", true);

    std::thread([host, requestPath, shutdownPath, readinessPath, token] {
      for (;;) {
        QJsonObject readiness;
        if (!readJsonObject(readinessPath, &readiness)) {
            if (QFileInfo::exists(readinessPath)) {
                const qint64 pid = QCoreApplication::applicationPid();
                writeJsonObject(shutdownPath, {
                    {QStringLiteral("schema"), QStringLiteral("design-studio.startup-shutdown/1")},
                    {QStringLiteral("status"), QStringLiteral("fail")},
                    {QStringLiteral("pid"), pid},
                    {QStringLiteral("token"), token},
                    {QStringLiteral("readiness_receipt_digest"), QString()},
                    {QStringLiteral("error"), QStringLiteral("readiness receipt is malformed")},
                });
                QCoreApplication::postEvent(host, new QCloseEvent());
                return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            continue;
        }
        const QString readinessDigest = readiness.value(QStringLiteral("receipt_digest")).toString();
        const qint64 pid = QCoreApplication::applicationPid();
        const bool readinessValid =
            readiness.value(QStringLiteral("schema")).toString()
                == QStringLiteral("design-studio.startup-readiness/1")
            && readiness.value(QStringLiteral("status")).toString() == QStringLiteral("pass")
            && readiness.value(QStringLiteral("token")).toString() == token
            && readiness.value(QStringLiteral("pid")).toInteger() == pid
            && readinessDigest.toLatin1()
                == jsonDigest(readiness, QStringLiteral("receipt_digest"));
        if (!readinessValid) {
            writeJsonObject(shutdownPath, {
                {QStringLiteral("schema"), QStringLiteral("design-studio.startup-shutdown/1")},
                {QStringLiteral("status"), QStringLiteral("fail")},
                {QStringLiteral("pid"), pid},
                {QStringLiteral("token"), token},
                {QStringLiteral("readiness_receipt_digest"), readinessDigest},
                {QStringLiteral("error"), QStringLiteral("readiness receipt is stale, invalid, or digest-mismatched")},
            });
            QCoreApplication::postEvent(host, new QCloseEvent());
            return;
        }

        const QString armedPath = requestPath + QStringLiteral(".armed");
        if (!QFileInfo::exists(armedPath)) {
            writeJsonObject(armedPath, {
                {QStringLiteral("schema"), QStringLiteral("design-studio.startup-shutdown-observer/1")},
                {QStringLiteral("status"), QStringLiteral("armed")},
                {QStringLiteral("pid"), pid},
                {QStringLiteral("token"), token},
                {QStringLiteral("readiness_receipt_digest"), readinessDigest},
            });
        }

        QJsonObject request;
        if (!readJsonObject(requestPath, &request)) {
            if (QFileInfo::exists(requestPath)) {
                writeJsonObject(shutdownPath, {
                    {QStringLiteral("schema"), QStringLiteral("design-studio.startup-shutdown/1")},
                    {QStringLiteral("status"), QStringLiteral("fail")},
                    {QStringLiteral("pid"), pid},
                    {QStringLiteral("token"), token},
                    {QStringLiteral("readiness_receipt_digest"), readinessDigest},
                    {QStringLiteral("error"), QStringLiteral("shutdown request is malformed")},
                });
                QCoreApplication::postEvent(host, new QCloseEvent());
                return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            continue;
        }
        const QString requestDigest = request.value(QStringLiteral("request_digest")).toString();
        const bool requestValid =
            request.value(QStringLiteral("schema")).toString()
                == QStringLiteral("design-studio.startup-shutdown-request/1")
            && request.value(QStringLiteral("token")).toString() == token
            && request.value(QStringLiteral("pid")).toInteger() == pid
            && request.value(QStringLiteral("readiness_receipt_digest")).toString()
                == readinessDigest
            && requestDigest.toLatin1()
                == jsonDigest(request, QStringLiteral("request_digest"));
        if (!requestValid) {
            writeJsonObject(shutdownPath, {
                {QStringLiteral("schema"), QStringLiteral("design-studio.startup-shutdown/1")},
                {QStringLiteral("status"), QStringLiteral("fail")},
                {QStringLiteral("pid"), pid},
                {QStringLiteral("token"), token},
                {QStringLiteral("readiness_receipt_digest"), readinessDigest},
                {QStringLiteral("error"), QStringLiteral("shutdown request is stale, malformed, or digest-mismatched")},
            });
            QCoreApplication::postEvent(host, new QCloseEvent());
            return;
        }
        writeJsonObject(requestPath + QStringLiteral(".accepted"), request,
                        QStringLiteral("request_digest"));
        // QCoreApplication::quit() is explicitly thread-safe.  The installed
        // FreeCAD host can remain inside a native startup event dispatcher
        // after workbench activation, so a queued Python/Qt callback is not a
        // reliable shutdown mechanism.  Record the authenticated acceptance,
        // then request Qt's normal zero-status event-loop termination.  The
        // shell fixture still requires the process to disappear and the
        // launcher to record exit_status=0 before this receipt is accepted.
        writeJsonObject(shutdownPath, {
            {QStringLiteral("schema"), QStringLiteral("design-studio.startup-shutdown/1")},
            {QStringLiteral("status"), QStringLiteral("pass")},
            {QStringLiteral("pid"), pid},
            {QStringLiteral("token"), token},
            {QStringLiteral("readiness_receipt_digest"), readinessDigest},
            {QStringLiteral("request_digest"), requestDigest},
            {QStringLiteral("created_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)},
        });
        QCoreApplication::postEvent(host, new QCloseEvent());
        return;
      }
    }).detach();
}

QMdiArea* hostMdiArea(Gui::MainWindow* host)
{
    if (!host) return nullptr;
    if (auto* mdi = qobject_cast<QMdiArea*>(host->centralWidget())) return mdi;
    return host->findChild<QMdiArea*>();
}

bool installNativeDocks(MainWindow* workspace,
                        Gui::MainWindow* host,
                        QVector<QPointer<QDockWidget>>& installed)
{
    if (!workspace || !host) return false;
    const auto docks = workspace->findChildren<QDockWidget*>(
        QString(), Qt::FindDirectChildrenOnly);
    for (QDockWidget* dock : docks) {
        if (!dock) return false;
        workspace->removeDockWidget(dock);
        dock->setParent(host);
        const QString name = dock->objectName();
        const Qt::DockWidgetArea area = name == QStringLiteral("dock_drc")
            ? Qt::BottomDockWidgetArea
            : name == QStringLiteral("dock_left")
                ? Qt::LeftDockWidgetArea : Qt::RightDockWidgetArea;
        host->addDockWidget(area, dock);
        installed.append(dock);
        if (name == QStringLiteral("dock_chat") || name == QStringLiteral("dock_left"))
            dock->show();
        else
            dock->hide();
    }
    return true;
}

bool installContextToolbar(
    MainWindow* workspace,
    Gui::MainWindow* host,
    QVector<QPointer<QToolBar>>& installed,
    QVector<QPair<QPointer<QToolBar>, bool>>& previous)
{
    if (!workspace || !host) return false;
    previous.clear();
    QPointer<QToolBar> nativeDesignStudioToolbar;
    const auto hostToolbars = host->findChildren<QToolBar*>(
        QString(), Qt::FindDirectChildrenOnly);
    for (QToolBar* toolbar : hostToolbars) {
        if (!toolbar) return false;
        previous.append({toolbar, toolbar->isVisible()});
        // Keep the workbench's own native mechanical commands reachable. The
        // embedded Qt toolbar owns electronics context; Reference Form and
        // other FreeCAD task panels remain host-native commands.
        const bool nativeDesignStudio =
            toolbar->windowTitle() == QStringLiteral("DesignStudio")
            || toolbar->objectName().contains(QStringLiteral("DesignStudio"),
                                              Qt::CaseInsensitive);
        toolbar->setVisible(nativeDesignStudio);
        if (nativeDesignStudio && !nativeDesignStudioToolbar)
            nativeDesignStudioToolbar = toolbar;
    }
    const auto toolbars = workspace->findChildren<QToolBar*>(
        QString(), Qt::FindDirectChildrenOnly);
    for (QToolBar* toolbar : toolbars) {
        if (!toolbar) return false;
        workspace->removeToolBar(toolbar);
        toolbar->setParent(host);
        host->addToolBar(Qt::TopToolBarArea, toolbar);
        installed.append(toolbar);
        // Product Home intentionally has no specialist tools. Tab changes in
        // MainWindow reveal the appropriate allowlist automatically.
        toolbar->setVisible(!toolbar->actions().isEmpty());
    }
    // FreeCAD persists visibility for workbench-native toolbars and can hide an
    // adopted contextual toolbar on Product Home. Put the always-available
    // Codex dock toggle on the native DesignStudio toolbar so it remains visible
    // across every product tab. QAction automatically detaches when the
    // workspace controller is destroyed.
    QAction* codexToggle = host->findChild<QAction*>(
        QStringLiteral("action_toggle_codex_side_panel"));
    if (nativeDesignStudioToolbar && codexToggle
        && !nativeDesignStudioToolbar->actions().contains(codexToggle)) {
        nativeDesignStudioToolbar->addAction(codexToggle);
    }
    return true;
}

void restoreHostChrome(
    const QVector<QPair<QPointer<QToolBar>, bool>>& previous)
{
    for (const auto& entry : previous) {
        if (entry.first) entry.first->setVisible(entry.second);
    }
}

// Resolve the workspace mechanical FCStd so the Mechanical 3D tab can attach
// the shaded FreeCAD viewport to the open project. The electronics project
// lives at <workspace>/electronics/product.dsproj, so its grandparent is the
// workspace root; the launcher environment is the fallback for desktop opens.
QString mechanicalDocumentPath()
{
    if (!workspaceController) return {};
    const QString projectPath = workspaceController->projectFilePath();
    if (!projectPath.isEmpty()) {
        QDir electronicsDir = QFileInfo(projectPath).dir();
        if (electronicsDir.dirName() == QStringLiteral("electronics")
            && electronicsDir.cdUp()) {
            const QString candidate = QDir(electronicsDir.filePath(
                QStringLiteral("mechanical"))).filePath(
                QStringLiteral("product.FCStd"));
            if (QFileInfo::exists(candidate)) return candidate;
        }
    }
    const QString envRoot = qEnvironmentVariable("DESIGNSTUDIO_WORKSPACE_PATH");
    if (!envRoot.isEmpty()) {
        const QString candidate = QDir(QDir(envRoot).filePath(
            QStringLiteral("mechanical"))).filePath(QStringLiteral("product.FCStd"));
        if (QFileInfo::exists(candidate)) return candidate;
    }
    return {};
}

void reportMechanicalViewportFailure(Gui::MainWindow* host,
                                     const QString& detail)
{
    const QString compact = detail.simplified().isEmpty()
        ? QStringLiteral("unknown error")
        : detail.simplified();
    const QString message = QStringLiteral("Mechanical 3D failed: %1")
        .arg(compact);
    qWarning().noquote() << "DESIGNSTUDIO_MECHANICAL_3D_FAILED:" << detail;
    if (host && host->statusBar())
        host->statusBar()->showMessage(message, 15000);
}

// Swap the shared FreeCAD MDI surface between the embedded product workspace
// and the mechanical document's shaded 3D viewport. The viewport stays a
// normal FreeCAD document (no widget reparenting), but the user experiences
// it as a tab attached to the project: entering "Mechanical 3D" activates it,
// leaving returns to the DesignStudio product surface.
void activateMechanicalViewport(Gui::MainWindow* host, bool active)
{
    if (!host) return;
    auto* mdi = hostMdiArea(host);
    if (!mdi) {
        if (active)
            reportMechanicalViewportFailure(
                host, QStringLiteral("FreeCAD MDI area is unavailable"));
        return;
    }
    if (!workspaceDocument) {
        if (active)
            reportMechanicalViewportFailure(
                host, QStringLiteral("DesignStudio workspace document is unavailable"));
        return;
    }
    if (!workspaceController) {
        if (active)
            reportMechanicalViewportFailure(
                host, QStringLiteral("DesignStudio workspace controller is unavailable"));
        return;
    }

    if (!active) {
        if (workspaceDocument && !workspaceDocument->isVisible())
            workspaceDocument->show();
        mdi->setActiveSubWindow(workspaceDocument.data());
        return;
    }

    const QString path = mechanicalDocumentPath();
    if (path.isEmpty()) {
        reportMechanicalViewportFailure(
            host, QStringLiteral(
                "workspace mechanical/product.FCStd was not found; "
                "verify the workspace electronics and mechanical directories"));
        return;
    }

    // Reuse the workbench python so document opening, helper hiding and the
    // appearance pass stay defined in exactly one place. The python side
    // caches prepared documents, so repeat entries are near-instant.
    QString escaped(path);
    escaped.replace(QStringLiteral("\\"), QStringLiteral("\\\\"))
           .replace(QStringLiteral("'"), QStringLiteral("\\'"));
    const QString snippet = QStringLiteral(
        "from DesignStudio import commands\n"
        "designstudio_mechanical_document_name = "
        "commands.ensure_mechanical_document(r'%1')\n").arg(escaped);

    // First entry opens and tessellates a ~160-feature assembly, which can
    // take seconds. Defer past the current tab-change event so the placeholder
    // paints, show a busy cursor, then run on the GUI thread — FreeCAD
    // document APIs are not thread-safe, so no worker pool here.
    host->setCursor(Qt::WaitCursor);
    QTimer::singleShot(0, host, [host, mdi, snippet, path]() {
        QString docName;
        try {
            const std::string returned = Base::Interpreter().runStringWithKey(
                snippet.toUtf8().constData(),
                "designstudio_mechanical_document_name");
            docName = QString::fromUtf8(returned.c_str()).trimmed();
        } catch (const Base::Exception& error) {
            reportMechanicalViewportFailure(
                host, QStringLiteral("%1 (%2)")
                    .arg(QString::fromUtf8(error.what()), path));
            host->unsetCursor();
            return;
        } catch (const std::exception& error) {
            reportMechanicalViewportFailure(
                host, QStringLiteral("%1 (%2)")
                    .arg(QString::fromUtf8(error.what()), path));
            host->unsetCursor();
            return;
        } catch (...) {
            reportMechanicalViewportFailure(
                host, QStringLiteral("unknown interpreter error (%1)").arg(path));
            host->unsetCursor();
            return;
        }

        if (docName.isEmpty()) {
            reportMechanicalViewportFailure(
                host, QStringLiteral(
                    "FreeCAD did not return the mechanical document name (%1)")
                    .arg(path));
            host->unsetCursor();
            return;
        }

        QString escapedDocName(docName);
        escapedDocName.replace(QStringLiteral("\\"), QStringLiteral("\\\\"))
                      .replace(QStringLiteral("'"), QStringLiteral("\\'"));
        const QString resetViewSnippet = QStringLiteral(
            "from DesignStudio import commands\n"
            "designstudio_mechanical_view_reset = "
            "commands.reset_mechanical_view(r'%1')\n").arg(escapedDocName);
        bool activated = false;
        bool defaultViewFailed = false;
        QString defaultViewError;
        for (QMdiSubWindow* sub : mdi->subWindowList()) {
            if (!sub || !sub->widget() || sub == workspaceDocument.data()) continue;
            const QString title = sub->windowTitle() + QLatin1Char(' ')
                + sub->widget()->windowTitle();
            if (title.contains(docName, Qt::CaseSensitive)) {
                sub->show();
                mdi->setActiveSubWindow(sub);
                try {
                    const std::string reset = Base::Interpreter().runStringWithKey(
                        resetViewSnippet.toUtf8().constData(),
                        "designstudio_mechanical_view_reset");
                    defaultViewFailed = QString::fromUtf8(reset.c_str()).trimmed()
                        != QStringLiteral("True");
                    if (defaultViewFailed)
                        defaultViewError = QStringLiteral(
                            "FreeCAD did not expose an active mechanical view");
                } catch (const Base::Exception& error) {
                    defaultViewFailed = true;
                    defaultViewError = QString::fromUtf8(error.what());
                } catch (const std::exception& error) {
                    defaultViewFailed = true;
                    defaultViewError = QString::fromUtf8(error.what());
                } catch (...) {
                    defaultViewFailed = true;
                    defaultViewError = QStringLiteral("unknown view reset error");
                }
                activated = true;
                break;
            }
        }
        if (!activated) {
            reportMechanicalViewportFailure(
                host, QStringLiteral(
                    "FreeCAD opened '%1' but its MDI viewport was not found (%2)")
                    .arg(docName, path));
        } else if (defaultViewFailed) {
            reportMechanicalViewportFailure(
                host, QStringLiteral("Mechanical view reset failed: %1 (%2)")
                    .arg(defaultViewError, path));
        } else {
            if (host->statusBar())
                host->statusBar()->showMessage(
                    QStringLiteral("Mechanical 3D: %1").arg(path), 5000);
        }
        host->unsetCursor();
    });
}

void destroyFailedWorkspace(
    MainWindow* workspace,
    Gui::MainWindow* host,
    QPointer<QWidget>& surface,
    QPointer<QMdiSubWindow>& document,
    const QVector<QPointer<QDockWidget>>& docks,
    const QVector<QPointer<QToolBar>>& toolbars,
    const QVector<QPair<QPointer<QToolBar>, bool>>& previous)
{
    // The candidate owns all of these objects.  Remove the objects which were
    // temporarily reparented into FreeCAD before deleting the candidate
    // controller.  This keeps rollback local to the failed construction and
    // prevents a later host event from seeing a dangling child.
    for (const auto& toolbar : toolbars) {
        if (toolbar && host) {
            host->removeToolBar(toolbar);
            delete toolbar;
        }
    }
    for (const auto& dock : docks) {
        if (dock && host) {
            host->removeDockWidget(dock);
            delete dock;
        }
    }
    if (document) {
        document->setAttribute(Qt::WA_DeleteOnClose, false);
        document->setWidget(nullptr);
        delete document;
        document = nullptr;
    }
    if (surface) {
        delete surface;
        surface = nullptr;
    }
    if (workspace) {
        workspace->setParent(nullptr);
        delete workspace;
    }
    restoreHostChrome(previous);
}

void destroyLiveWorkspace(Gui::MainWindow* host)
{
    // This is intentionally explicit rather than relying on QObject parent
    // destruction.  The lifecycle regression fixture uses it to exercise the
    // same close/reopen boundary a user reaches from the MDI tab.
    // Snapshot every owned object and clear the published state before any
    // QObject destructor can emit destroyed().  In particular, deleting the
    // MDI subwindow synchronously destroys/updates QPointers; consulting the
    // globals after that point used to skip deletion of the detached surface
    // and controller and leaked one hidden controller per reopen.
    MainWindow* controller = workspaceController.data();
    QWidget* surface = workspaceSurface.data();
    QMdiSubWindow* document = workspaceDocument.data();
    const auto docks = workspaceDocks;
    const auto toolbars = workspaceToolbars;
    const auto previous = previousHostToolbars;
    workspaceController = nullptr;
    workspaceSurface = nullptr;
    workspaceDocument = nullptr;
    workspaceDocks.clear();
    workspaceToolbars.clear();
    previousHostToolbars.clear();

    for (const auto& toolbar : toolbars) {
        if (toolbar && host) {
            host->removeToolBar(toolbar);
            delete toolbar;
        }
    }
    for (const auto& dock : docks) {
        if (dock && host) {
            host->removeDockWidget(dock);
            delete dock;
        }
    }
    if (document) {
        document->setAttribute(Qt::WA_DeleteOnClose, false);
        document->setWidget(nullptr);
        delete document;
    }
    if (surface) {
        delete surface;
    }
    if (controller) {
        controller->setParent(nullptr);
        delete controller;
    }
    restoreHostChrome(previous);
}

QJsonObject runEmbeddedTrustedNativeTool(const QString& operation,
                                         const QJsonObject& arguments)
{
    const QByteArray operationBytes = operation.toUtf8();
    const QByteArray argumentBytes = QJsonDocument(arguments).toJson(QJsonDocument::Compact);
    PyGILState_STATE gil = PyGILState_Ensure();
    PyObject* module = PyImport_ImportModule("DesignStudio.live_tools");
    if (!module) {
        PyErr_Clear();
        PyGILState_Release(gil);
        return {{QStringLiteral("ok"), false},
                {QStringLiteral("message"), QStringLiteral("cannot import trusted FreeCAD live tools")}};
    }
    PyObject* function = PyObject_GetAttrString(module, "execute");
    PyObject* result = function && PyCallable_Check(function)
        ? PyObject_CallFunction(function, "ss", operationBytes.constData(), argumentBytes.constData())
        : nullptr;
    QJsonObject response;
    if (result) {
        const char* utf8 = PyUnicode_AsUTF8(result);
        const QJsonDocument parsed = utf8 ? QJsonDocument::fromJson(QByteArray(utf8)) : QJsonDocument{};
        response = parsed.isObject() ? parsed.object() : QJsonObject{
            {QStringLiteral("ok"), false},
            {QStringLiteral("message"), QStringLiteral("trusted FreeCAD tool returned invalid JSON")}};
    }
    else {
        PyErr_Clear();
        response = {{QStringLiteral("ok"), false},
                    {QStringLiteral("message"), QStringLiteral("trusted FreeCAD tool failed")}};
    }
    Py_XDECREF(result);
    Py_XDECREF(function);
    Py_DECREF(module);
    PyGILState_Release(gil);
    return response;
}

QString nativeToolWorkerPath()
{
    PyGILState_STATE gil = PyGILState_Ensure();
    PyObject* module = PyImport_ImportModule("DesignStudio.native_tool_worker");
    PyObject* file = module ? PyObject_GetAttrString(module, "__file__") : nullptr;
    const char* utf8 = file ? PyUnicode_AsUTF8(file) : nullptr;
    QString path = utf8 ? QString::fromUtf8(utf8) : QString{};
    if (PyErr_Occurred()) PyErr_Clear();
    Py_XDECREF(file);
    Py_XDECREF(module);
    PyGILState_Release(gil);
    if (path.endsWith(QStringLiteral(".pyc"))) path.chop(1);
    return QFileInfo(path).fileName() == QStringLiteral("native_tool_worker.py")
        && QFileInfo(path).isFile() ? QFileInfo(path).canonicalFilePath() : QString{};
}

QString freeCadCommand()
{
    const QString configured = QString::fromUtf8(qgetenv("DESIGNSTUDIO_FREECAD_CMD"));
    if (!configured.isEmpty() && QFileInfo(configured).isExecutable()) return configured;
    const QDir application(QCoreApplication::applicationDirPath());
    for (const QString& name : {QStringLiteral("DesignStudioCmd"),
                                QStringLiteral("freecadcmd"),
                                QStringLiteral("FreeCADCmd")}) {
        const QString sibling = application.filePath(name);
        if (QFileInfo(sibling).isExecutable()) return sibling;
        const QString discovered = QStandardPaths::findExecutable(name);
        if (!discovered.isEmpty()) return discovered;
    }
    return {};
}

bool isEmbeddedOnlyOperation(const QString& operation)
{
    return operation == QStringLiteral("show_workspace_view")
        || operation == QStringLiteral("open_reference_form")
        || operation == QStringLiteral("select_semantic_object")
        || operation == QStringLiteral("import_freecad_project");
}

void cancelTrustedNativeTool()
{
    activeNativeWorkerCancelled.store(true);
    if (activeNativeWorker && activeNativeWorker->state() != QProcess::NotRunning)
        activeNativeWorker->terminate();
}

QJsonObject runTrustedNativeTool(const QString& operation, const QJsonObject& arguments)
{
    if (isEmbeddedOnlyOperation(operation))
        return runEmbeddedTrustedNativeTool(operation, arguments);
    if (activeNativeWorker)
        return {{QStringLiteral("ok"), false},
                {QStringLiteral("message"), QStringLiteral("another native CAD worker is active")}};

    QString workspaceRoot = arguments.value(QStringLiteral("workspace_root")).toString();
    if (workspaceRoot.isEmpty()) {
        const QString mechanical = arguments.value(QStringLiteral("mechanical_path")).toString();
        workspaceRoot = mechanical.isEmpty() ? QString{} : QFileInfo(mechanical).absolutePath();
    }
    const QFileInfo rootInfo(workspaceRoot);
    const QString canonicalRoot = rootInfo.isDir() ? rootInfo.canonicalFilePath() : QString{};
    if (canonicalRoot.isEmpty())
        return {{QStringLiteral("ok"), false},
                {QStringLiteral("message"), QStringLiteral("native CAD tool requires an existing workspace")}};

    const QString workerPath = nativeToolWorkerPath();
    const QString command = freeCadCommand();
    if (workerPath.isEmpty() || command.isEmpty())
        return {{QStringLiteral("ok"), false},
                {QStringLiteral("message"), QStringLiteral("supervised FreeCAD worker is unavailable")}};

    QTemporaryDir stage(QDir(canonicalRoot).filePath(
        QStringLiteral(".designstudio-native-worker-XXXXXX")));
    if (!stage.isValid())
        return {{QStringLiteral("ok"), false},
                {QStringLiteral("message"), QStringLiteral("cannot create native CAD shadow workspace")}};
    const QString requestPath = QDir(stage.path()).filePath(QStringLiteral("request.json"));
    const QString responsePath = QDir(stage.path()).filePath(QStringLiteral("response.json"));
    const QJsonObject request{
        {QStringLiteral("schema"), QStringLiteral("design-studio.native-tool-request/1")},
        {QStringLiteral("operation"), operation},
        {QStringLiteral("arguments"), arguments},
        {QStringLiteral("stage_root"), stage.path()},
    };
    if (!writeRawJsonObject(requestPath, request))
        return {{QStringLiteral("ok"), false},
                {QStringLiteral("message"), QStringLiteral("cannot write native CAD worker request")}};

    QProcess process;
    process.setProcessChannelMode(QProcess::SeparateChannels);
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    environment.insert(QStringLiteral("PYTHONDONTWRITEBYTECODE"), QStringLiteral("1"));
    environment.insert(QStringLiteral("DESIGNSTUDIO_NATIVE_TOOL_REQUEST"), requestPath);
    environment.insert(QStringLiteral("DESIGNSTUDIO_NATIVE_TOOL_RESPONSE"), responsePath);
    process.setProcessEnvironment(environment);
    activeNativeWorkerCancelled.store(false);
    activeNativeWorker = &process;
    process.start(command, {workerPath});
    if (!process.waitForStarted(5000)) {
        activeNativeWorker = nullptr;
        return {{QStringLiteral("ok"), false},
                {QStringLiteral("message"), QStringLiteral("cannot start supervised FreeCAD worker: ")
                                                    + process.errorString()}};
    }
    while (!process.waitForFinished(50)) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 50);
        if (!activeNativeWorkerCancelled.load()) continue;
        if (!process.waitForFinished(500)) {
            process.kill();
            process.waitForFinished(1000);
        }
        break;
    }
    const bool cancelled = activeNativeWorkerCancelled.exchange(false);
    activeNativeWorker = nullptr;
    const auto controlTransaction = [&](const QString& flag, QString* error) {
        QProcess control;
        control.setProcessChannelMode(QProcess::SeparateChannels);
        QProcessEnvironment controlEnvironment = environment;
        controlEnvironment.insert(QStringLiteral("DESIGNSTUDIO_NATIVE_TOOL_CONTROL"),
                                  QStringLiteral("1"));
        control.setProcessEnvironment(controlEnvironment);
        // FreeCAD parses option-like arguments before the Python script sees
        // them.  --pass makes recovery/ack flags script arguments rather than
        // unknown FreeCAD command-line options.
        control.start(command, {workerPath, QStringLiteral("--pass"), flag, canonicalRoot});
        bool succeeded = control.waitForStarted(5000) && control.waitForFinished(10000);
        if (!succeeded) {
            control.kill();
            control.waitForFinished(1000);
        }
        succeeded = succeeded && control.exitStatus() == QProcess::NormalExit
                    && control.exitCode() == 0;
        if (!succeeded && error) {
            *error = QString::fromUtf8(control.readAllStandardError()).trimmed();
            if (error->size() > 600) *error = error->left(600);
        }
        return succeeded;
    };
    if (cancelled) {
        // SIGTERM normally lets the worker roll back in-process.  If Stop had
        // to escalate to SIGKILL, replay the durable workspace journal before
        // the GUI reports that the approved revision was preserved.  Backups
        // live outside the QTemporaryDir specifically for this recovery pass.
        QString error;
        const bool recovered = controlTransaction(QStringLiteral("--recover-root"), &error);
        if (!recovered) {
            return {{QStringLiteral("ok"), false}, {QStringLiteral("cancelled"), true},
                    {QStringLiteral("rollback_failed"), true},
                    {QStringLiteral("message"),
                     QStringLiteral("Stopped by user, but transaction recovery failed; workspace is blocked")
                         + (error.isEmpty() ? QString{} : QStringLiteral(": ") + error)}};
        }
        return {{QStringLiteral("ok"), false}, {QStringLiteral("cancelled"), true},
                {QStringLiteral("message"),
                 QStringLiteral("Stopped by user; last approved revision preserved.")}};
    }

    QJsonObject response;
    if (!readJsonObject(responsePath, &response)) {
        QString error = QString::fromUtf8(process.readAllStandardError()).trimmed();
        if (error.size() > 600) error = error.left(600);
        QString recoveryError;
        const bool recovered = controlTransaction(QStringLiteral("--recover-root"), &recoveryError);
        return {{QStringLiteral("ok"), false},
                {QStringLiteral("message"), QStringLiteral("native CAD worker failed closed")
                    + (error.isEmpty() ? QString{} : QStringLiteral(": ") + error)
                    + (recovered ? QStringLiteral("; transaction recovered")
                                 : QStringLiteral("; transaction recovery failed: ") + recoveryError)}};
    }
    if (!response.value(QStringLiteral("ok")).toBool()) {
        QString recoveryError;
        if (!controlTransaction(QStringLiteral("--recover-root"), &recoveryError)
                && !recoveryError.contains(QStringLiteral("missing a journal"))) {
            response.insert(QStringLiteral("rollback_failed"), true);
            response.insert(QStringLiteral("message"), response.value(QStringLiteral("message")).toString()
                            + QStringLiteral("; transaction recovery failed: ") + recoveryError);
        }
        return response;
    }

    const QString mechanicalPath = arguments.value(QStringLiteral("mechanical_path")).toString();
    if (!mechanicalPath.isEmpty() && QFileInfo::exists(mechanicalPath)) {
        const QJsonObject refresh = runEmbeddedTrustedNativeTool(
            QStringLiteral("reload_after_worker"),
            {{QStringLiteral("mechanical_path"), mechanicalPath}});
        if (!refresh.value(QStringLiteral("ok")).toBool()) {
            QString recoveryError;
            const bool recovered = controlTransaction(QStringLiteral("--recover-root"), &recoveryError);
            response.insert(QStringLiteral("ok"), false);
            response.insert(QStringLiteral("message"),
                            QStringLiteral("FreeCAD could not reload the published CAD revision: ")
                                + refresh.value(QStringLiteral("message")).toString()
                                + (recovered ? QStringLiteral("; transaction recovered")
                                             : QStringLiteral("; transaction recovery failed: ") + recoveryError));
            return response;
        }
    }
    QJsonObject data = response.value(QStringLiteral("data")).toObject();
    QJsonObject transaction = data.value(QStringLiteral("worker_transaction")).toObject();
    QString acknowledgementError;
    if (transaction.value(QStringLiteral("host_ack_required")).toBool()
            && !controlTransaction(QStringLiteral("--ack-root"), &acknowledgementError)) {
        QString recoveryError;
        const bool recovered = controlTransaction(QStringLiteral("--recover-root"), &recoveryError);
        response.insert(QStringLiteral("ok"), false);
        response.insert(QStringLiteral("message"),
                        QStringLiteral("CAD revision acknowledgement failed: ") + acknowledgementError
                            + (recovered ? QStringLiteral("; transaction recovered")
                                         : QStringLiteral("; transaction recovery failed: ") + recoveryError));
        return response;
    }
    if (transaction.value(QStringLiteral("host_ack_required")).toBool()) {
        transaction.insert(QStringLiteral("status"), QStringLiteral("committed"));
        transaction.insert(QStringLiteral("host_acknowledged"), true);
    }
    data.insert(QStringLiteral("worker_transaction"), transaction);
    response.insert(QStringLiteral("data"), data);
    return response;
}

class Module final: public Py::ExtensionModule<Module>
{
public:
    Module()
        : Py::ExtensionModule<Module>("DesignStudioGui")
    {
        add_varargs_method(
            "showWorkspace",
            &Module::showWorkspace,
            "showWorkspace([project_path]) -- show the unified native product workspace"
        );
        add_varargs_method(
            "hideWorkspace",
            &Module::hideWorkspace,
            "hideWorkspace() -- hide the native schematic and PCB workspace"
        );
        add_varargs_method(
            "closeWorkspace",
            &Module::closeWorkspace,
            "closeWorkspace() -- close and destroy the embedded product workspace"
        );
        initialize("Unified native DesignStudio product workspace for FreeCAD.");
    }

private:
    Py::Object showWorkspace(const Py::Tuple& args)
    {
        char* encodedPath = nullptr;
        if (!PyArg_ParseTuple(args.ptr(), "|et", "utf-8", &encodedPath)) {
            throw Py::Exception();
        }

        auto* host = Gui::getMainWindow();
        if (!host) {
            if (encodedPath) {
                PyMem_Free(encodedPath);
            }
            throw Py::RuntimeError("FreeCAD main window is unavailable");
        }

        // A destroyed MDI document clears its QPointer automatically.  Treat
        // any partially populated state as a failed previous transaction and
        // start from a clean candidate instead of dereferencing a half-built
        // workspace.
        if (workspaceController && (!workspaceSurface || !workspaceDocument)) {
            destroyLiveWorkspace(host);
        }

        if (!workspaceController) {
            QMdiArea* mdi = hostMdiArea(host);
            if (!mdi) {
                if (encodedPath) PyMem_Free(encodedPath);
                throw Py::RuntimeError("FreeCAD MDI area is unavailable");
            }
            mdi->setViewMode(QMdiArea::TabbedView);
            mdi->setTabsClosable(true);
            mdi->setTabsMovable(true);
            QPointer<MainWindow> candidateController = new MainWindow(
                host, MainWindow::UiProfile::FreeCadEmbedded);
            QPointer<QWidget> candidateSurface;
            QPointer<QMdiSubWindow> candidateDocument;
            QVector<QPointer<QDockWidget>> candidateDocks;
            QVector<QPointer<QToolBar>> candidateToolbars;
            QVector<QPair<QPointer<QToolBar>, bool>> candidatePreviousToolbars;

            auto rollback = [&] {
                destroyFailedWorkspace(candidateController, host,
                                       candidateSurface, candidateDocument,
                                       candidateDocks, candidateToolbars,
                                       candidatePreviousToolbars);
            };

            if (!candidateController) {
                if (encodedPath) PyMem_Free(encodedPath);
                throw Py::RuntimeError("DesignStudio workspace controller could not be created");
            }
            candidateController->setObjectName(
                QStringLiteral("DesignStudioProductWorkspace"));
            candidateController->setNativeToolHandler(runTrustedNativeTool, cancelTrustedNativeTool);
            // MainWindow is retained only as a non-visible controller for the
            // standalone diagnostics build.  Its central product widget is
            // detached and becomes the MDI document, so no QMainWindow is ever
            // nested in FreeCAD's document area.
            candidateSurface = candidateController->takeCentralWidget();
            if (!candidateSurface) {
                rollback();
                if (encodedPath) PyMem_Free(encodedPath);
                throw Py::RuntimeError("DesignStudio product surface could not be created");
            }
            candidateSurface->setObjectName(
                QStringLiteral("DesignStudioProductSurface"));
            candidateController->setWindowFlags(Qt::Widget);
            candidateController->hide();
            candidateDocument = mdi->addSubWindow(candidateSurface, Qt::Widget);
            if (!candidateDocument) {
                rollback();
                if (encodedPath) PyMem_Free(encodedPath);
                throw Py::RuntimeError("FreeCAD MDI document could not be created");
            }
            candidateDocument->setObjectName(
                QStringLiteral("DesignStudioWorkspaceDocument"));
            candidateDocument->setWindowTitle(QObject::tr("DesignStudio Product"));
            candidateDocument->setAttribute(Qt::WA_DeleteOnClose, false);
            if (!installNativeDocks(candidateController, host, candidateDocks)
                || !installContextToolbar(candidateController, host,
                                           candidateToolbars,
                                           candidatePreviousToolbars)) {
                rollback();
                if (encodedPath) PyMem_Free(encodedPath);
                throw Py::RuntimeError("DesignStudio workspace host integration failed");
            }

            // Commit only after every host mutation succeeded.  The QPointers
            // then become the sole long-lived state for this one embedded
            // workspace and are cleared automatically if FreeCAD destroys it.
            workspaceController = candidateController;
            workspaceSurface = candidateSurface;
            workspaceDocument = candidateDocument;
            workspaceDocks = std::move(candidateDocks);
            workspaceToolbars = std::move(candidateToolbars);
            previousHostToolbars = std::move(candidatePreviousToolbars);
        }

        bool opened = true;
        if (encodedPath && encodedPath[0] != '\0') {
            const QString path = QString::fromUtf8(encodedPath);
            const QFileInfo input(path);
            QString error;
            opened = workspaceController && (input.isDir()
                || input.fileName() == QStringLiteral("manifest.json")
                ? workspaceController->openWorkspace(path, &error)
                : workspaceController->openFile(path));
        }
        if (encodedPath) {
            PyMem_Free(encodedPath);
        }
        if (!opened) {
            throw Py::RuntimeError("DesignStudio could not open the electronics project");
        }

        // Mechanical 3D tab: attach the shaded FreeCAD viewport to this
        // project. Reconnect on every showWorkspace so a rebuilt controller
        // never keeps a stale lambda captured over a dead pointer.
        static QMetaObject::Connection mechanicalViewConnection;
        if (mechanicalViewConnection)
            QObject::disconnect(mechanicalViewConnection);
        mechanicalViewConnection = QObject::connect(
            workspaceController, &MainWindow::mechanicalViewActive, host,
            [host](bool active) { activateMechanicalViewport(host, active); });

        workspaceDocument->show();
        workspaceSurface->show();
        workspaceDocument->showMaximized();
        for (const auto& dock : workspaceDocks) {
            if (dock && (dock->objectName() == QStringLiteral("dock_chat")
                         || dock->objectName() == QStringLiteral("dock_left")))
                dock->show();
        }
        for (const auto& previous : previousHostToolbars) {
            if (!previous.first) continue;
            const bool nativeDesignStudio =
                previous.first->windowTitle() == QStringLiteral("DesignStudio")
                || previous.first->objectName().contains(
                    QStringLiteral("DesignStudio"), Qt::CaseInsensitive);
            previous.first->setVisible(nativeDesignStudio);
        }
        for (const auto& toolbar : workspaceToolbars)
            if (toolbar) toolbar->setVisible(!toolbar->actions().isEmpty());
        armTestShutdownObserver(host);
        return Py::None();
    }

    Py::Object hideWorkspace(const Py::Tuple& args)
    {
        if (!PyArg_ParseTuple(args.ptr(), "")) {
            throw Py::Exception();
        }
        if (workspaceDocument) workspaceDocument->hide();
        for (const auto& dock : workspaceDocks) if (dock) dock->hide();
        for (const auto& toolbar : workspaceToolbars) if (toolbar) toolbar->hide();
        for (const auto& previous : previousHostToolbars)
            if (previous.first) previous.first->setVisible(previous.second);
        return Py::None();
    }

    Py::Object closeWorkspace(const Py::Tuple& args)
    {
        if (!PyArg_ParseTuple(args.ptr(), "")) {
            throw Py::Exception();
        }
        destroyLiveWorkspace(Gui::getMainWindow());
        return Py::None();
    }
};
}  // namespace

PyMOD_INIT_FUNC(DesignStudioGui)
{
    if (!Gui::Application::Instance) {
        PyErr_SetString(PyExc_ImportError, "DesignStudioGui requires the FreeCAD GUI");
        PyMOD_Return(nullptr);
    }
    PyMOD_Return(Base::Interpreter().addModule(new Module));
}
