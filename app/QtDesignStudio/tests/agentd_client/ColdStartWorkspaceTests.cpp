#include "MainWindow.h"

#include <QApplication>
#include <QDir>
#include <QEventLoop>
#include <QFile>
#include <QFrame>
#include <QJsonObject>
#include <QLabel>
#include <QPushButton>
#include <QTemporaryDir>
#include <QTimer>

#include <cstdio>

namespace {
bool waitForState(QStringList& states, const QString& wanted, int timeoutMs = 8000) {
    if (states.contains(wanted)) return true;
    QEventLoop loop;
    QTimer timeout;
    timeout.setSingleShot(true);
    QObject::connect(&timeout, &QTimer::timeout, &loop, &QEventLoop::quit);
    QTimer poll;
    QObject::connect(&poll, &QTimer::timeout, &loop, [&] {
        if (states.contains(wanted)) loop.quit();
    });
    timeout.start(timeoutMs);
    poll.start(20);
    loop.exec();
    return states.contains(wanted);
}

bool waitForText(QLabel* label, const QString& wanted, int timeoutMs = 12000) {
    if (label && label->text().contains(wanted)) return true;
    QEventLoop loop;
    QTimer timeout;
    timeout.setSingleShot(true);
    QObject::connect(&timeout, &QTimer::timeout, &loop, &QEventLoop::quit);
    QTimer poll;
    QObject::connect(&poll, &QTimer::timeout, &loop, [&] {
        if (label && label->text().contains(wanted)) loop.quit();
    });
    timeout.start(timeoutMs);
    poll.start(20);
    loop.exec();
    return label && label->text().contains(wanted);
}

MainWindow::NativeToolHandler fakeFreeCad(bool succeed) {
    return [succeed](const QString& operation, const QJsonObject& arguments) {
        if (operation != QStringLiteral("create_product_workspace"))
            return QJsonObject{{QStringLiteral("ok"), true},
                               {QStringLiteral("message"), QStringLiteral("ignored test operation")}};
        if (!succeed)
            return QJsonObject{{QStringLiteral("ok"), false},
                               {QStringLiteral("message"), QStringLiteral("synthetic native failure")}};
        QFile file(arguments.value(QStringLiteral("mechanical_path")).toString());
        if (!file.open(QIODevice::WriteOnly) || file.write("test FCStd\n") <= 0)
            return QJsonObject{{QStringLiteral("ok"), false},
                               {QStringLiteral("message"), file.errorString()}};
        return QJsonObject{{QStringLiteral("ok"), true},
                           {QStringLiteral("message"), QStringLiteral("synthetic FCStd created")}};
    };
}
}

int main(int argc, char** argv) {
    QApplication app(argc, argv);
    if (argc != 3) return 2;
    QTemporaryDir temporary;
    if (!temporary.isValid()) return 3;
    qputenv("DESIGNSTUDIO_AGENTD", argv[1]);
    qputenv("DESIGNSTUDIO_SOURCE_ROOT", argv[2]);
    qputenv("DESIGNSTUDIO_WORKSPACE_PARENT", temporary.path().toUtf8());

    QString workspaceRoot;
    {
        MainWindow window;
        window.setNativeToolHandler(fakeFreeCad(true));
        QStringList states;
        QObject::connect(&window, &MainWindow::workspaceLifecycleChanged,
                         &window, [&](const QString& state, const QString&) {
            states.append(state);
        });
        const QJsonObject result = window.createProductWorkspace(
            QStringLiteral("Cold Start Keyboard"),
            QStringLiteral("synthetic acceptance fixture"));
        if (!result.value(QStringLiteral("ok")).toBool()) return 4;
        workspaceRoot = result.value(QStringLiteral("data")).toObject()
            .value(QStringLiteral("workspace_root")).toString();
        if (!waitForState(states, QStringLiteral("ready"))) return 5;
        const int creating = states.indexOf(QStringLiteral("creating"));
        const int starting = states.indexOf(QStringLiteral("starting"));
        const int ready = states.indexOf(QStringLiteral("ready"));
        if (creating < 0 || starting <= creating || ready <= starting) return 6;
        QDir root(workspaceRoot);
        if (!root.exists(QStringLiteral("manifest.json"))
            || !root.exists(QStringLiteral("mechanical/product.FCStd"))
            || !root.exists(QStringLiteral("electronics/product.dsproj"))) return 7;

        auto* buildMap = window.findChild<QWidget*>(QStringLiteral("build_map_panel"));
        auto* compile = window.findChild<QPushButton*>(QStringLiteral("build_map_compile"));
        auto* approve = window.findChild<QPushButton*>(QStringLiteral("build_map_approve"));
        auto* runAll = window.findChild<QPushButton*>(QStringLiteral("build_map_run_all"));
        auto* buildState = window.findChild<QLabel*>(QStringLiteral("build_map_state"));
        auto* budget = window.findChild<QLabel*>(QStringLiteral("build_map_budget"));
        auto* gpuGate = window.findChild<QLabel*>(QStringLiteral("build_map_gpu_gate"));
        if (!buildMap || !compile || !approve || !runAll || !buildState
            || !budget || !gpuGate || !compile->isEnabled()) return 12;
        compile->click();
        if (!waitForText(buildState, QStringLiteral("awaiting approval"))
            || !approve->isEnabled()) return 13;
        approve->click();
        if (!waitForText(buildState, QStringLiteral("approved"))
            || !runAll->isEnabled()) return 14;
        runAll->click();
        if (!waitForText(buildState, QStringLiteral("succeeded"), 20000)) {
            std::fprintf(stderr, "build-map final state: %s\n",
                         buildState->text().toUtf8().constData());
            return 15;
        }
        int packageCards = 0;
        for (QFrame* frame : buildMap->findChildren<QFrame*>()) {
            if (frame->objectName().startsWith(QStringLiteral("build_package_")))
                ++packageCards;
        }
        if (packageCards != 6
            || !budget->text().contains(QStringLiteral("72 consumed"))
            || !budget->text().contains(QStringLiteral("0 remaining"))
            || !gpuGate->text().contains(QStringLiteral("GPU phase locked"))) return 16;
    }

    // The same manifest must survive full desktop/control-process teardown.
    {
        MainWindow reopened;
        QStringList states;
        QObject::connect(&reopened, &MainWindow::workspaceLifecycleChanged,
                         &reopened, [&](const QString& state, const QString&) {
            states.append(state);
        });
        QString error;
        if (!reopened.openWorkspace(workspaceRoot, &error)) return 17;
        if (!waitForState(states, QStringLiteral("ready"))) return 18;
    }

    // A failed native FreeCAD operation must identify the operation and leave
    // no incomplete workspace behind.
    {
        const int before = QDir(temporary.path()).entryList(
            {QStringLiteral("*.dsworkspace")}, QDir::Dirs | QDir::NoDotAndDotDot
        ).size();
        MainWindow failing;
        failing.setNativeToolHandler(fakeFreeCad(false));
        QString lastState;
        QString lastDetail;
        QObject::connect(&failing, &MainWindow::workspaceLifecycleChanged,
                         &failing, [&](const QString& state, const QString& detail) {
            lastState = state;
            lastDetail = detail;
        });
        const QJsonObject result = failing.createProductWorkspace(
            QStringLiteral("Rollback Fixture"));
        if (result.value(QStringLiteral("ok")).toBool()) return 19;
        const int after = QDir(temporary.path()).entryList(
            {QStringLiteral("*.dsworkspace")}, QDir::Dirs | QDir::NoDotAndDotDot
        ).size();
        if (after != before || lastState != QStringLiteral("offline")
            || !lastDetail.contains(QStringLiteral("FreeCAD document creation failed")))
            return 20;
    }

    std::puts("COLD_START_AND_BUILD_MAP_WORKFLOW_OK");
    return 0;
}
