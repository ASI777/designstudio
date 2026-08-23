#pragma once
#include <QWidget>
#include <QVector>
class QCheckBox;
class QLabel;
class PcbCanvas;

class LayerPanel : public QWidget {
    Q_OBJECT
public:
    explicit LayerPanel(PcbCanvas* canvas, QWidget* parent = nullptr);
    void setLayerCount(int n);

signals:
    void activeLayerChanged(int layer);

private:
    PcbCanvas* m_canvas;
    struct Row { QCheckBox* check{}; QLabel* swatch{}; QLabel* name{}; };
    QVector<Row> m_rows;
    int m_layerCount{4};

    void rebuild();
    void onLayerClicked(int layer);
};
