#include "DatasheetExtractor.h"
#include "DimensionSolver.h"
#include <QFile>
#include <QXmlStreamReader>
#include <QRegularExpression>
#include <QVector>
#include <QRectF>
#include <QPointF>
#include <QHash>
#include <functional>
#include <algorithm>
#include <array>
#include <cmath>

// NOTE: PyMuPDF SVG places each element in a local frame via transform="matrix(...)"
// and uses M/L/H/V/C/Z path commands. We parse the commands and APPLY the matrix so
// all geometry lands in one page coordinate system. (This is the slice of lib2geom
// we replicate by hand: path reader + affine. Swap in Geom::PathVector for full
// curve fidelity later.)
namespace {

struct Mat { double a=1,b=0,c=0,d=1,e=0,f=0; };          // [a c e; b d f]
QPointF apply(const Mat& m, double x, double y) { return { m.a*x + m.c*y + m.e, m.b*x + m.d*y + m.f }; }

QVector<double> floats(const QString& s) {
    QVector<double> out;
    static const QRegularExpression re("[-+]?\\d*\\.?\\d+(?:[eE][-+]?\\d+)?");
    auto it = re.globalMatch(s);
    while (it.hasNext()) out.push_back(it.next().captured(0).toDouble());
    return out;
}

Mat parseMatrix(const QString& t) {
    Mat m;
    int i = t.indexOf("matrix");
    if (i < 0) return m;
    auto v = floats(t.mid(i));
    if (v.size() >= 6) { m.a=v[0]; m.b=v[1]; m.c=v[2]; m.d=v[3]; m.e=v[4]; m.f=v[5]; }
    return m;
}

QString colour6(const QString& attr, const QString& style, const char* key) {
    QString v = attr;
    if (v.isEmpty() && !style.isEmpty()) {
        QRegularExpression re(QString("%1\\s*:\\s*#?([0-9a-fA-F]{6})").arg(key));
        auto mm = re.match(style); if (mm.hasMatch()) v = mm.captured(1);
    }
    v = v.trimmed(); if (v.startsWith('#')) v = v.mid(1);
    return v.left(6).toLower();
}

double parseValue(const QString& t) { auto f = floats(t); return f.isEmpty() ? 0.0 : f.last(); }

// Parse an SVG path 'd' (M/L/H/V/C/Z, abs+rel), emit straight segments in PAGE coords
// (matrix m applied); fill bbox of all points. Curves use their chord endpoint.
struct Seg { double x1,y1,x2,y2; };
QVector<Seg> pathSegs(const QString& d, const Mat& m, QRectF& bbox) {
    struct Tk { bool cmd; QChar c; double v; };
    QVector<Tk> ts;
    static const QRegularExpression rx("([A-DF-Za-df-z])|([-+]?\\d*\\.?\\d+(?:[eE][-+]?\\d+)?)");
    auto it = rx.globalMatch(d);
    while (it.hasNext()) { auto mt = it.next();
        if (!mt.captured(1).isEmpty()) ts.push_back({true, mt.captured(1)[0], 0});
        else ts.push_back({false, QChar(), mt.captured(2).toDouble()}); }

    QVector<Seg> segs;
    double cx=0, cy=0, sx=0, sy=0; QChar cmd; bool started=false;
    double minx=1e18,miny=1e18,maxx=-1e18,maxy=-1e18;
    auto bump=[&](const QPointF& p){ minx=std::min(minx,p.x()); miny=std::min(miny,p.y());
                                     maxx=std::max(maxx,p.x()); maxy=std::max(maxy,p.y()); };
    auto addSeg=[&](double nx,double ny){ QPointF a=apply(m,cx,cy), b=apply(m,nx,ny);
                                          segs.push_back({a.x(),a.y(),b.x(),b.y()}); bump(a); bump(b); };
    int i=0;
    auto num=[&](double& o)->bool{ if(i<ts.size() && !ts[i].cmd){ o=ts[i].v; ++i; return true;} return false; };
    while (i < ts.size()) {
        if (ts[i].cmd) { cmd = ts[i].c; ++i; if (i>=ts.size() && cmd.toUpper()!='Z') break; }
        const bool rel = cmd.isLower(); const QChar C = cmd.toUpper();
        double a0,a1,a2,a3,a4,a5;
        if (C=='M') { if(!num(a0)||!num(a1)) break;
            cx=rel?cx+a0:a0; cy=rel?cy+a1:a1; sx=cx; sy=cy; started=true;
            bump(apply(m,cx,cy)); cmd = rel?'l':'L'; }      // extra pairs become lineto
        else if (C=='L') { if(!num(a0)||!num(a1)) break; double nx=rel?cx+a0:a0,ny=rel?cy+a1:a1; addSeg(nx,ny); cx=nx; cy=ny; }
        else if (C=='H') { if(!num(a0)) break; double nx=rel?cx+a0:a0; addSeg(nx,cy); cx=nx; }
        else if (C=='V') { if(!num(a0)) break; double ny=rel?cy+a0:a0; addSeg(cx,ny); cy=ny; }
        else if (C=='C') { if(!num(a0)||!num(a1)||!num(a2)||!num(a3)||!num(a4)||!num(a5)) break;
            double nx=rel?cx+a4:a4,ny=rel?cy+a5:a5; addSeg(nx,ny); cx=nx; cy=ny; }
        else if (C=='Z') { if(started){ addSeg(sx,sy); cx=sx; cy=sy; } }
        else { if(i<ts.size() && !ts[i].cmd) ++i; }         // skip unknown args safely
    }
    if (maxx>minx) bbox = QRectF(minx,miny,maxx-minx,maxy-miny);
    return segs;
}

} // namespace

namespace dimextract {

ProjFootprint solveFootprintFromSvg(const QString& svgPath, QString* msg) {
    auto fail = [&](const QString& m){ if (msg) *msg = m; return ProjFootprint{}; };
    QFile f(svgPath);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) return fail("cannot open SVG");

    QVector<DimSeg> gray; QVector<DimNum> nums; QVector<DimSeg> blackPerim; double Wview = 0;
    Mat textM; QString textFill; bool inText=false;

    QXmlStreamReader xml(&f);
    while (!xml.atEnd()) {
        xml.readNext();
        if (xml.isEndElement()) { if (xml.name()==QLatin1String("text")) inText=false; continue; }
        if (!xml.isStartElement()) continue;
        const QString tag = xml.name().toString();
        const QXmlStreamAttributes a = xml.attributes();
        const QString style = a.value(QLatin1String("style")).toString();
        const QString strokeC = colour6(a.value(QLatin1String("stroke")).toString(), style, "stroke");
        const QString fillC   = colour6(a.value(QLatin1String("fill")).toString(),   style, "fill");
        const Mat m = parseMatrix(a.value(QLatin1String("transform")).toString());

        if (tag == QLatin1String("svg")) {
            auto vb = floats(a.value(QLatin1String("viewBox")).toString());
            if (vb.size()==4) Wview = vb[2];
        }
        else if (tag == QLatin1String("path") || tag == QLatin1String("polyline")) {
            QRectF bb; auto segs = pathSegs(a.value(QLatin1String("d")).toString(), m, bb);
            if (strokeC == QLatin1String("4b4b4b"))
                for (auto& s : segs) gray.push_back({s.x1,s.y1,s.x2,s.y2});
            if (strokeC==QLatin1String("000000") || fillC==QLatin1String("000000")) {
                // PERIMETER tracking: keep copper outline edges, DROP the 45 deg hatch
                // fill lines (they are the "solder area" marker, not the shape, and they
                // are what bridges adjacent pads).
                for (auto& s : segs) {
                    const double ang = std::fmod(std::atan2(s.y2-s.y1, s.x2-s.x1)*180.0/M_PI + 180.0, 180.0);
                    const bool diag = (ang>37 && ang<53) || (ang>127 && ang<143);
                    if (!diag) blackPerim.push_back({s.x1,s.y1,s.x2,s.y2});
                }
            }
        }
        else if (tag == QLatin1String("text")) { inText=true; textM=m; textFill=fillC; }
        else if (tag == QLatin1String("tspan") && inText) {
            const auto xs = floats(a.value(QLatin1String("x")).toString());
            const auto ys = floats(a.value(QLatin1String("y")).toString());
            const double lx = xs.isEmpty()?0:xs.first(), ly = ys.isEmpty()?0:ys.first();
            const QPointF P = apply(textM, lx, ly);
            const QString txt = xml.readElementText();       // tspan has only chars
            const double v = parseValue(txt);
            if (v > 0 && txt.contains('.')) nums.push_back({P.x(), P.y(), v});
        }
    }
    if (xml.hasError()) return fail("SVG parse error: " + xml.errorString());
    if (gray.isEmpty() || nums.isEmpty())
        return fail(QString("no dimensions found (gray=%1 numbers=%2 perimeter=%3)")
                    .arg(gray.size()).arg(nums.size()).arg(blackPerim.size()));

    if (Wview > 0) {                                         // left layout only
        const double cut = Wview * 0.55;
        QVector<DimSeg> g; for (auto& s : gray)       if ((s.x1+s.x2)*0.5 < cut) g.push_back(s);
        QVector<DimNum> n; for (auto& z : nums)       if (z.cx < cut) n.push_back(z);
        QVector<DimSeg> p; for (auto& s : blackPerim) if ((s.x1+s.x2)*0.5 < cut) p.push_back(s);
        gray=g; nums=n; blackPerim=p;
    }

    // gray geometry extent (for spanning-shape exclusion + axis binning)
    double gMinX=1e18,gMaxX=-1e18,gMinY=1e18,gMaxY=-1e18;
    for (auto& s : gray){ gMinX=std::min({gMinX,s.x1,s.x2}); gMaxX=std::max({gMaxX,s.x1,s.x2});
                          gMinY=std::min({gMinY,s.y1,s.y2}); gMaxY=std::max({gMaxY,s.y1,s.y2}); }
    const double grayW=gMaxX-gMinX, grayH=gMaxY-gMinY;

    // CHAIN perimeter segments by SHARED ENDPOINTS into copper contours. With the
    // 45 deg hatch removed, each pad outline is its own closed loop (pads share no
    // corners) -> 24 separate pads instead of one merged blob.
    const int N = blackPerim.size();
    QVector<int> par(N); for (int i=0;i<N;++i) par[i]=i;
    std::function<int(int)> find=[&](int x){ while(par[x]!=x){par[x]=par[par[x]];x=par[x];} return x; };
    const double etol = 3.0;                                 // endpoint-coincidence tol
    auto pts=[&](const DimSeg& s){ return std::array<QPointF,2>{ QPointF(s.x1,s.y1), QPointF(s.x2,s.y2) }; };
    for (int i=0;i<N;++i) for (int j=i+1;j<N;++j) {
        bool touch=false;
        for (auto& pi : pts(blackPerim[i])) for (auto& pj : pts(blackPerim[j]))
            if (std::hypot(pi.x()-pj.x(), pi.y()-pj.y()) < etol) { touch=true; break; }
        if (touch) par[find(i)]=find(j);
    }
    QHash<int,QRectF> comp;
    for (int i=0;i<N;++i){ const DimSeg& s=blackPerim[i];
        QRectF r(QPointF(std::min(s.x1,s.x2),std::min(s.y1,s.y2)),
                 QPointF(std::max(s.x1,s.x2),std::max(s.y1,s.y2)));
        int k=find(i); comp[k]=comp.contains(k)?comp[k].united(r):r; }
    QVector<QRectF> copper;                                  // drop tiny noise + spanning body outline
    for (auto& r : comp)
        if (r.width()>0.5 && r.height()>0.5 && r.width()<0.6*grayW && r.height()<0.6*grayH)
            copper.push_back(r);

    // overall dims: bin each number to X or Y by its NEAREST gray segment's orientation,
    // then take the max value per axis (8.64 for X, 6.20 for Y).
    double overallX=0, overallY=0;
    for (auto& z : nums){
        double best=1e18; bool nbHoriz=true;
        for (auto& s : gray){ const double mx=(s.x1+s.x2)*0.5,my=(s.y1+s.y2)*0.5;
            const double dd=std::hypot(z.cx-mx,z.cy-my);
            if (dd<best){ best=dd; nbHoriz=std::fabs(s.x2-s.x1) >= std::fabs(s.y2-s.y1); } }
        if (nbHoriz) overallX=std::max(overallX,z.value); else overallY=std::max(overallY,z.value);
    }
    if (overallX<=0 || overallY<=0) return fail("could not determine overall dimensions");

    if (msg) *msg = QString("gray=%1 numbers=%2 copper=%3 overall=%4x%5mm")
                     .arg(gray.size()).arg(nums.size()).arg(copper.size()).arg(overallX).arg(overallY);
    return dimstudio::buildFootprint(gray, nums, copper, overallX, overallY);
}

} // namespace dimextract
