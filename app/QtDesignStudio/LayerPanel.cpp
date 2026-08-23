#include "LayerPanel.h"
#include "PcbCanvas.h"
#include <QCheckBox>
#include <QLabel>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QPushButton>
#include <QFont>

static const char* LAYER_NAMES[] = {
    "L1 Top Signal","L2 GND Plane","L3 Power Plane","L4 Bot Signal",
    "L5","L6","L7","L8"
};
static const char* LAYER_COLORS[] = {
    "#ff5555","#223322","#c8a000","#5599ff","#ff9900","#00cc88","#aa44ff","#ff44aa"
};

LayerPanel::LayerPanel(PcbCanvas* canvas, QWidget* parent)
    : QWidget(parent), m_canvas(canvas)
{
    setMinimumWidth(180);
    rebuild();
}

void LayerPanel::setLayerCount(int n) {
    m_layerCount = n;
    rebuild();
}

void LayerPanel::rebuild() {
    // Clear existing layout
    if (layout()) {
        QLayoutItem* item;
        while ((item = layout()->takeAt(0))) {
            delete item->widget();
            delete item;
        }
        delete layout();
    }
    m_rows.clear();

    auto* vbox = new QVBoxLayout(this);
    vbox->setSpacing(2);
    vbox->setContentsMargins(8,8,8,8);

    auto* title = new QLabel("Layers", this);
    QFont f = title->font();
    f.setPointSize(10); f.setBold(true);
    title->setFont(f);
    title->setProperty("heading","true");
    vbox->addWidget(title);

    for (int i = 0; i < m_layerCount && i < 8; ++i) {
        auto* row = new QWidget(this);
        auto* hbox = new QHBoxLayout(row);
        hbox->setContentsMargins(0,0,0,0);
        hbox->setSpacing(6);

        auto* check = new QCheckBox(row);
        check->setChecked(true);
        connect(check, &QCheckBox::toggled, this, [this,i](bool v){
            m_canvas->setLayerVisible(i, v);
        });

        auto* swatch = new QLabel(row);
        swatch->setFixedSize(14,14);
        swatch->setStyleSheet(QString("background:%1;border-radius:3px;").arg(LAYER_COLORS[i]));

        auto* name = new QLabel(LAYER_NAMES[i], row);

        // Clicking the row name sets active layer
        auto* btn = new QPushButton(row);
        btn->setFixedSize(14,14);
        btn->setText("•");
        btn->setToolTip("Set as active layer");
        btn->setStyleSheet("QPushButton{background:transparent;border:none;color:#8b949e;}"
                           "QPushButton:hover{color:#58a6ff;}");
        connect(btn, &QPushButton::clicked, this, [this,i]{ onLayerClicked(i); });

        hbox->addWidget(check);
        hbox->addWidget(swatch);
        hbox->addWidget(name, 1);
        hbox->addWidget(btn);

        m_rows.push_back({check, swatch, name});
        vbox->addWidget(row);
    }

    vbox->addStretch();
}

void LayerPanel::onLayerClicked(int layer) {
    m_canvas->setActiveLayer(layer);

    // Highlight active row
    for (int i = 0; i < m_rows.size(); ++i) {
        QString style = (i == layer)
            ? "color:#58a6ff;font-weight:600;"
            : "color:#c9d1d9;font-weight:400;";
        m_rows[i].name->setStyleSheet(style);
    }
    emit activeLayerChanged(layer);
}
