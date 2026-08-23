#include "designcore/physics.h"
#include <algorithm>
#include <limits>

namespace dc::phys {

namespace {
constexpr double kPi = 3.14159265358979323846;
constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

double rhoCu(double tempC) { return kRhoCu20 * (1.0 + kAlphaCu * (tempC - 20.0)); }
}

// ---------------------------------------------------------- mathematics ----

namespace {
double agm(double a, double b) {
    for (int i = 0; i < 80 && std::abs(a - b) > 1e-15 * a; ++i) {
        const double an = 0.5 * (a + b);
        b = std::sqrt(a * b);
        a = an;
    }
    return a;
}
}

double ellipticK(double k) {
    if (k < 0 || k >= 1) return kNaN;
    // AGM: K(k) = π / (2·agm(1, √(1−k²))). Quadratic convergence.
    return kPi / (2.0 * agm(1.0, std::sqrt(1.0 - k * k)));
}

// K(k′) = K(√(1−k²)) computed as π/(2·agm(1, k)) — numerically stable for
// k → 0 where k′ → 1 and K diverges logarithmically.
static double ellipticKComp(double k) {
    if (k <= 1e-300 || k >= 1) return kNaN;
    return kPi / (2.0 * agm(1.0, k));
}

double solveMonotone(double (*f)(double, const void*), const void* ctx,
                     double lo, double hi, double tol) {
    double flo = f(lo, ctx), fhi = f(hi, ctx);
    if (std::isnan(flo) || std::isnan(fhi) || flo * fhi > 0) return kNaN;
    for (int i = 0; i < 200; ++i) {
        const double mid = 0.5 * (lo + hi);
        const double fm = f(mid, ctx);
        if (std::abs(fm) < tol || (hi - lo) < tol * std::max(1.0, std::abs(mid)))
            return mid;
        if (flo * fm <= 0) { hi = mid; fhi = fm; }
        else { lo = mid; flo = fm; }
    }
    return 0.5 * (lo + hi);
}

// ----------------------------------------------------------- microstrip ----
// Hammerstad–Jensen ("Microstrip Lines and Slotlines" 4th ed., §2.4,
// eqs. 2.119–2.121 + thickness correction).

namespace {

double hjZ0Air(double u) {              // eq. 2.119: Z0 in air (t = 0)
    const double fu = 6.0 + (2.0 * kPi - 6.0) * std::exp(-std::pow(30.666 / u, 0.7528));
    return (kEta0 / (2.0 * kPi)) * std::log(fu / u + std::sqrt(1.0 + (2.0 / u) * (2.0 / u)));
}

double hjEEff(double u, double er) {    // eqs. 2.120–2.121
    const double u4 = u * u * u * u;
    const double a = 1.0
        + (1.0 / 49.0) * std::log((u4 + std::pow(u / 52.0, 2.0)) / (u4 + 0.432))
        + (1.0 / 18.7) * std::log(1.0 + std::pow(u / 18.1, 3.0));
    const double b = 0.564 * std::pow((er - 0.9) / (er + 3.0), 0.053);
    return 0.5 * (er + 1.0) + 0.5 * (er - 1.0) * std::pow(1.0 + 10.0 / u, -a * b);
}

// Hammerstad–Jensen finite-thickness width correction.
void hjThickness(double u, double tNorm, double er, double& u1, double& ur) {
    if (tNorm <= 0) { u1 = ur = u; return; }
    const double cothSq = 1.0 / std::pow(std::tanh(std::sqrt(6.517 * u)), 2.0);
    const double du1 = (tNorm / kPi) * std::log(1.0 + 4.0 * std::exp(1.0) / (tNorm * cothSq));
    const double dur = 0.5 * (1.0 + 1.0 / std::cosh(std::sqrt(er - 1.0))) * du1;
    u1 = u + du1;
    ur = u + dur;
}

} // namespace

bool microstrip(double wMm, double hMm, double tMm, double er, LineResult& out) {
    if (wMm <= 0 || hMm <= 0 || er < 1) return false;
    const double u = wMm / hMm;
    const double tn = tMm / hMm;
    double u1, ur;
    hjThickness(u, tn, std::max(er, 1.0 + 1e-9), u1, ur);

    const double eEffR = hjEEff(ur, er);
    const double z0Air1 = hjZ0Air(u1);
    const double z0AirR = hjZ0Air(ur);
    // thickness-aware effective permittivity (H-J recipe)
    const double eEff = eEffR * std::pow(z0Air1 / z0AirR, 2.0);

    out.z0 = z0AirR / std::sqrt(eEff);
    out.eEff = eEff;
    out.delayPsPerMm = 1000.0 * std::sqrt(eEff) / kC0MmPerNs;
    return out.z0 > 0 && std::isfinite(out.z0);
}

namespace {
struct MsCtx { double targetZ0, hMm, tMm, er; };
double msErr(double wMm, const void* p) {
    const auto* c = static_cast<const MsCtx*>(p);
    LineResult r;
    if (!microstrip(wMm, c->hMm, c->tMm, c->er, r)) return kNaN;
    return r.z0 - c->targetZ0;      // Z0 decreases with width → monotone
}
}

double microstripWidthForZ0(double targetZ0, double hMm, double tMm, double er) {
    if (targetZ0 <= 0 || hMm <= 0) return kNaN;
    MsCtx c{targetZ0, hMm, tMm, er};
    return solveMonotone(&msErr, &c, 0.01 * hMm, 50.0 * hMm, 1e-6);
}

// ------------------------------------------------------------ stripline ----
// Cohn's exact thin-strip solution (Collin, "Foundations for Microwave
// Engineering"): Z0 = (η0 / 4√εr) · K(k)/K(k′), k = sech(πw/2b),
// k′ = tanh(πw/2b). Finite thickness handled with an effective-width
// correction (Wheeler): w_eff = w + (t/π)·ln(2) style; kept small-t.

bool stripline(double wMm, double bMm, double tMm, double er, LineResult& out) {
    if (wMm <= 0 || bMm <= 0 || er < 1 || tMm >= bMm) return false;
    // finite-thickness effective width (Cohn correction as used in IPC-2141)
    double w = wMm;
    if (tMm > 0) {
        const double m = 6.0 / (3.0 + 2.0 * tMm / (bMm - tMm));
        const double dw = (tMm / kPi) *
            (1.0 - 0.5 * std::log(std::pow(tMm / (2.0 * bMm - tMm), 2.0) +
                                  std::pow(0.0796 * tMm / (wMm + 1.1 * tMm), m)));
        w += dw;
    }
    const double arg = kPi * w / (2.0 * bMm);
    const double k = 1.0 / std::cosh(arg);
    const double K = ellipticK(k), Kp = ellipticKComp(k);
    if (!std::isfinite(K) || !std::isfinite(Kp) || Kp <= 0) return false;

    out.z0 = (kEta0 / (4.0 * std::sqrt(er))) * (K / Kp);
    out.eEff = er;                              // homogeneous dielectric
    out.delayPsPerMm = 1000.0 * std::sqrt(er) / kC0MmPerNs;
    return std::isfinite(out.z0);
}

namespace {
struct SlCtx { double targetZ0, bMm, tMm, er; };
double slErr(double wMm, const void* p) {
    const auto* c = static_cast<const SlCtx*>(p);
    LineResult r;
    if (!stripline(wMm, c->bMm, c->tMm, c->er, r)) return kNaN;
    return r.z0 - c->targetZ0;
}
}

double striplineWidthForZ0(double targetZ0, double bMm, double tMm, double er) {
    if (targetZ0 <= 0 || bMm <= 0) return kNaN;
    SlCtx c{targetZ0, bMm, tMm, er};
    return solveMonotone(&slErr, &c, 0.01 * bMm, 20.0 * bMm, 1e-6);
}

// ----------------------------------------------------- differential pairs ----
// IPC-2141A edge-coupled approximations (±5% class).

double differentialMicrostrip(double wMm, double sMm, double hMm, double tMm, double er) {
    LineResult r;
    if (!microstrip(wMm, hMm, tMm, er, r) || sMm <= 0) return kNaN;
    return 2.0 * r.z0 * (1.0 - 0.48 * std::exp(-0.96 * sMm / hMm));
}

double differentialStripline(double wMm, double sMm, double bMm, double tMm, double er) {
    LineResult r;
    if (!stripline(wMm, bMm, tMm, er, r) || sMm <= 0) return kNaN;
    return 2.0 * r.z0 * (1.0 - 0.347 * std::exp(-2.9 * sMm / bMm));
}

namespace {
struct DiffCtx { double targetZ, sMm, hMm, tMm, er; };
double diffErr(double wMm, const void* p) {
    const auto* c = static_cast<const DiffCtx*>(p);
    const double z = differentialMicrostrip(wMm, c->sMm, c->hMm, c->tMm, c->er);
    return std::isnan(z) ? kNaN : z - c->targetZ;
}
}

double diffMicrostripWidthForZ(double targetZdiff, double sMm, double hMm, double tMm, double er) {
    if (targetZdiff <= 0 || hMm <= 0 || sMm <= 0) return kNaN;
    DiffCtx c{targetZdiff, sMm, hMm, tMm, er};
    return solveMonotone(&diffErr, &c, 0.01 * hMm, 50.0 * hMm, 1e-6);
}

// ------------------------------------------------------ losses & skin ----

double skinDepthMm(double fHz, double rhoOhmM) {
    if (fHz <= 0 || rhoOhmM <= 0) return kNaN;
    return 1000.0 * std::sqrt(rhoOhmM / (kPi * fHz * kMu0));
}

double microstripLossDbPerM(double wMm, double hMm, double tMm, double er,
                            double tanDelta, double fHz) {
    LineResult r;
    if (!microstrip(wMm, hMm, tMm, er, r) || fHz <= 0) return kNaN;
    // conductor: thin-strip Rs/(Z0·w) approximation
    const double Rs = std::sqrt(kPi * fHz * kMu0 * kRhoCu20);          // surface resistance
    const double alphaC = 8.686 * Rs / (r.z0 * (wMm / 1000.0));        // dB/m
    // dielectric: 27.3 (εr/(εr−1)) ((εeff−1)/√εeff) tanδ / λ0
    const double lambda0 = 299792458.0 / fHz;                          // m
    double alphaD = 0.0;
    if (er > 1.0)
        alphaD = 27.3 * (er / (er - 1.0)) * ((r.eEff - 1.0) / std::sqrt(r.eEff)) * tanDelta / lambda0;
    return alphaC + alphaD;
}

double traceResistanceDc(double wMm, double tMm, double lengthMm, double tempC) {
    if (wMm <= 0 || tMm <= 0 || lengthMm < 0) return kNaN;
    return rhoCu(tempC) * (lengthMm / 1000.0) / ((wMm / 1000.0) * (tMm / 1000.0));
}

double traceResistanceAc(double wMm, double tMm, double lengthMm, double fHz, double tempC) {
    const double rdc = traceResistanceDc(wMm, tMm, lengthMm, tempC);
    if (std::isnan(rdc) || fHz <= 0) return rdc;
    const double deltaMm = skinDepthMm(fHz, rhoCu(tempC));
    if (deltaMm >= tMm / 2.0) return rdc;       // fully penetrated → DC value
    // current confined to a shell of depth δ around the perimeter
    const double shellArea = (2.0 * (wMm + tMm) * deltaMm - 4.0 * deltaMm * deltaMm) * 1e-6;  // m²
    const double rac = rhoCu(tempC) * (lengthMm / 1000.0) / shellArea;
    return std::max(rac, rdc);
}

// --------------------------------------------- current capacity / fusing ----

double ipc2221CurrentA(double widthMm, double thickMm, double deltaTC, bool external) {
    if (widthMm <= 0 || thickMm <= 0 || deltaTC <= 0) return kNaN;
    const double areaMil2 = (widthMm * thickMm) / (0.0254 * 0.0254);
    const double k = external ? 0.048 : 0.024;
    return k * std::pow(deltaTC, 0.44) * std::pow(areaMil2, 0.725);
}

double onderdonkFusingA(double widthMm, double thickMm, double seconds, double ambientC) {
    if (widthMm <= 0 || thickMm <= 0 || seconds <= 0) return kNaN;
    const double areaCmil = (widthMm * thickMm) * 1973.525241;          // mm² → circular mils
    const double tMelt = 1083.0;                                        // Cu, °C
    return areaCmil * std::sqrt(std::log10(1.0 + (tMelt - ambientC) / (234.0 + ambientC)) / (33.0 * seconds));
}

// ----------------------------------------------------------------- vias ----

double viaInductanceNH(double heightMm, double drillMm) {
    if (heightMm <= 0 || drillMm <= 0) return kNaN;
    return 0.2 * heightMm * (std::log(4.0 * heightMm / drillMm) + 1.0);
}

double viaCapacitancePF(double er, double boardThickMm, double padMm, double antipadMm) {
    if (boardThickMm <= 0 || padMm <= 0 || antipadMm <= padMm) return kNaN;
    return (1.41 / 25.4) * er * boardThickMm * padMm / (antipadMm - padMm);
}

double viaResistanceOhm(double heightMm, double drillMm, double platingMm, double tempC) {
    if (heightMm <= 0 || drillMm <= 0 || platingMm <= 0) return kNaN;
    const double areaM2 = kPi * (platingMm * (drillMm - platingMm)) * 1e-6;   // barrel wall annulus
    if (areaM2 <= 0) return kNaN;
    return rhoCu(tempC) * (heightMm / 1000.0) / areaM2;
}

double viaThermalResistanceKW(double heightMm, double drillMm, double platingMm) {
    if (heightMm <= 0 || drillMm <= 0 || platingMm <= 0) return kNaN;
    constexpr double kCu = 385.0;                                       // W/(m·K)
    const double areaM2 = kPi * (platingMm * (drillMm - platingMm)) * 1e-6;
    if (areaM2 <= 0) return kNaN;
    return (heightMm / 1000.0) / (kCu * areaM2);
}

double viaCurrentA(double drillMm, double platingMm, double deltaTC) {
    if (drillMm <= 0 || platingMm <= 0) return kNaN;
    const double areaMm2 = kPi * platingMm * (drillMm - platingMm);
    // barrel treated as an internal conductor (IPC-2221, k = 0.024)
    const double areaMil2 = areaMm2 / (0.0254 * 0.0254);
    return 0.024 * std::pow(deltaTC, 0.44) * std::pow(areaMil2, 0.725);
}

// -------------------------------------------------------------- coupling ----

double crosstalkCoefficient(double sMm, double hMm) {
    if (sMm < 0 || hMm <= 0) return kNaN;
    const double r = sMm / hMm;
    return 1.0 / (1.0 + r * r);
}

double planeCapacitancePF(double areaMm2, double dielectricMm, double er) {
    if (areaMm2 <= 0 || dielectricMm <= 0 || er < 1) return kNaN;
    return 8.8541878128e-3 * er * areaMm2 / dielectricMm;   // ε0 = 8.854e-3 pF/mm
}

} // namespace dc::phys
