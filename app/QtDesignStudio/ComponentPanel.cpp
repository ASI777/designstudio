#include "ComponentPanel.h"
#include "ProjectModel.h"
#include "DatasheetExtractor.h"
#include <QTreeWidget>
#include <QTreeWidgetItem>
#include <QVBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QHeaderView>
#include <QFileDialog>
#include <QMessageBox>

ComponentPanel::ComponentPanel(ProjectModel* model, QWidget* parent)
    : QWidget(parent), m_model(model)
{
    setMinimumWidth(200);
    auto* vbox = new QVBoxLayout(this);
    vbox->setContentsMargins(8,8,8,8);
    vbox->setSpacing(6);

    auto* title = new QLabel("Components", this);
    title->setProperty("heading","true");
    vbox->addWidget(title);

    // Dedicated datasheet → footprint extraction lives here. Picks ONE datasheet
    // PDF and generates only that component's footprint (no full build).
    auto* extractBtn = new QPushButton(
        QString::fromUtf8("\xEF\xBC\x8B Datasheet \xE2\x86\x92 Symbol + Footprint + STEP"), this);
    extractBtn->setCursor(Qt::PointingHandCursor);
    extractBtn->setProperty("primary","true");
    extractBtn->setToolTip("Inspect a datasheet and preview its symbol, SMT/THT footprint, and STEP model");
    connect(extractBtn, &QPushButton::clicked, this,
            [this]{ emit extractDatasheetRequested(); });
    vbox->addWidget(extractBtn);

    // Deterministic path: solve a footprint from a datasheet layout SVG via the
    // dimension constraint solver (outline=datum, copper placed by measurement).
    auto* solveBtn = new QPushButton(
        QString::fromUtf8("\xE2\x9A\x99 Solve Footprint from SVG"), this);
    solveBtn->setCursor(Qt::PointingHandCursor);
    solveBtn->setToolTip("Pick a recommended-layout SVG; reconstruct the footprint by "
                         "solving its dimensions");
    connect(solveBtn, &QPushButton::clicked, this, [this]{ onSolveFromSvg(); });
    vbox->addWidget(solveBtn);

    auto* search = new QLineEdit(this);
    search->setPlaceholderText("Filter by ref or lib…");
    vbox->addWidget(search);

    m_tree = new QTreeWidget(this);
    m_tree->setColumnCount(3);
    m_tree->setHeaderLabels({"Ref","Library","Pads"});
    m_tree->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    m_tree->header()->setSectionResizeMode(1, QHeaderView::Stretch);
    m_tree->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    m_tree->setAlternatingRowColors(true);
    m_tree->setRootIsDecorated(false);
    vbox->addWidget(m_tree);

    m_countLabel = new QLabel("0 components", this);
    m_countLabel->setProperty("muted","true");
    vbox->addWidget(m_countLabel);

    connect(m_tree, &QTreeWidget::itemActivated, this, &ComponentPanel::onItemActivated);
    connect(m_model, &ProjectModel::loaded,   this, &ComponentPanel::refresh);
    connect(m_model, &ProjectModel::modified, this, &ComponentPanel::refresh);

    connect(search, &QLineEdit::textChanged, this, [this](const QString& text){
        for (int i = 0; i < m_tree->topLevelItemCount(); ++i) {
            auto* item = m_tree->topLevelItem(i);
            bool match = text.isEmpty()
                || item->text(0).contains(text, Qt::CaseInsensitive)
                || item->text(1).contains(text, Qt::CaseInsensitive);
            item->setHidden(!match);
        }
    });
}

void ComponentPanel::refresh() {
    m_tree->clear();
    for (auto& fp : m_model->footprints) {
        auto* item = new QTreeWidgetItem(m_tree);
        item->setText(0, fp.ref);
        item->setText(1, fp.lib);
        item->setText(2, QString::number(fp.pads.size()));
        item->setData(0, Qt::UserRole, fp.ref);
    }
    m_countLabel->setText(QString("%1 component%2")
        .arg(m_model->footprints.size())
        .arg(m_model->footprints.size() == 1 ? "" : "s"));
}

void ComponentPanel::selectComponent(const QString& ref) {
    for (int i = 0; i < m_tree->topLevelItemCount(); ++i) {
        auto* item = m_tree->topLevelItem(i);
        if (item->text(0) == ref) {
            m_tree->setCurrentItem(item);
            m_tree->scrollToItem(item);
            break;
        }
    }
}

void ComponentPanel::onItemActivated(QTreeWidgetItem* item, int) {
    emit componentFocused(item->text(0));
}

void ComponentPanel::onSolveFromSvg() {
    const QString path = QFileDialog::getOpenFileName(
        this, "Select recommended-layout SVG", QString(), "SVG files (*.svg)");
    if (path.isEmpty()) return;

    QString msg;
    ProjFootprint fp = dimextract::solveFootprintFromSvg(path, &msg);
    if (fp.pads.isEmpty()) {
        QMessageBox::warning(this, "Solve footprint",
            "Could not reconstruct a footprint.\n" + msg);
        return;
    }
    fp.ref = QString("J%1").arg(m_model->footprints.size() + 1);
    fp.x_mm = m_model->boardWidthMm  / 2.0;     // drop the part at board centre
    fp.y_mm = m_model->boardHeightMm / 2.0;
    m_model->footprints.append(fp);
    const QString placementError = m_model->legalizeFootprints();
    if (!placementError.isEmpty()) {
        m_model->footprints.removeLast();
        QMessageBox::warning(this, "Solve footprint",
            "The footprint cannot fit on the PCB substrate.\n" + placementError);
        return;
    }
    m_model->setModified(true);                 // repaints canvas + refreshes panels
    refresh();
    selectComponent(fp.ref);
    emit componentFocused(fp.ref);
    QMessageBox::information(this, "Solve footprint",
        QString("Placed %1 with %2 pads.\n%3").arg(fp.ref).arg(fp.pads.size()).arg(msg));
}
