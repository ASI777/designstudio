#pragma once
// Physics & mathematics engine for PCB electrical design.
//
// Models and their sources (textbooks present in `TextBook Datasets/`):
//   - Microstrip Z0 / εeff: Hammerstad–Jensen closed forms — Garg, Bahl,
//     Bozzi, "Microstrip Lines and Slotlines", 4th ed. (AD EMT), §2.4,
//     eqs. 2.116–2.121 (synthesis seeds the Newton solver), incl. the
//     strip-thickness correction. Stated accuracy ≤ 0.2% for Z0(t=0).
//   - Stripline Z0: Cohn's exact conformal-mapping solution via complete
//     elliptic integrals — Collin, "Foundations for Microwave Engineering"
//     (IL EMT); K(k) computed with the arithmetic–geometric mean (AGM),
//     which converges quadratically (Advanced Engineering Mathematics,
//     Mathematics/).
//   - Differential impedance: IPC-2141A coupled-line approximations
//     (±5% class; full coupled H-J on the roadmap).
//   - Conductor/dielectric loss, skin effect: standard microwave results
//     ("Microstrip Lines and Slotlines" §2.4.7; Rizzi, "Microwave
//     Engineering — Passive Circuits", AD EMT).
//   - Current capacity: IPC-2221 curve fit; adiabatic fusing: Onderdonk.
//   - Via parasitics: Johnson & Graham engineering formulas (cited as
//     rules of thumb; ±20% class).
//
// Conventions: lengths mm, frequency Hz, temperature °C, resistance Ω.
// All functions are pure; invalid inputs return NaN (single-value) or
// false (multi-output).

#include <cmath>

namespace dc::phys {

inline constexpr double kEta0 = 376.730313668;     // free-space impedance, Ω
inline constexpr double kC0MmPerNs = 299.792458;   // speed of light, mm/ns
inline constexpr double kRhoCu20 = 1.724e-8;       // Cu resistivity @20°C, Ω·m
inline constexpr double kAlphaCu = 0.00393;        // Cu temp. coefficient, 1/K
inline constexpr double kMu0 = 4e-7 * 3.14159265358979323846;

// ---------- mathematics ----------

// Complete elliptic integral of the first kind K(k), 0 <= k < 1, via AGM.
double ellipticK(double k);

// Robust 1-D root solve of f on [lo, hi] (f monotone): bisection with
// Newton acceleration. Returns NaN if no sign change.
double solveMonotone(double (*f)(double, const void*), const void* ctx,
                     double lo, double hi, double tol = 1e-9);

// ---------- transmission lines ----------

struct LineResult {
    double z0 = 0;        // characteristic impedance, Ω
    double eEff = 1;      // effective dielectric constant
    double delayPsPerMm = 0;
};

// Surface microstrip: trace width w over dielectric height h (to the
// reference plane), copper thickness t, relative permittivity er.
bool microstrip(double wMm, double hMm, double tMm, double er, LineResult& out);

// Width that hits a target Z0 for the given microstrip stack. NaN if the
// target is unreachable in w ∈ [0.01h, 50h].
double microstripWidthForZ0(double targetZ0, double hMm, double tMm, double er);

// Symmetric stripline: trace centred between planes spaced bMm apart.
bool stripline(double wMm, double bMm, double tMm, double er, LineResult& out);
double striplineWidthForZ0(double targetZ0, double bMm, double tMm, double er);

// Edge-coupled differential impedance (IPC-2141A approximations).
double differentialMicrostrip(double wMm, double sMm, double hMm, double tMm, double er);
double differentialStripline(double wMm, double sMm, double bMm, double tMm, double er);
// Width for a target Zdiff at a fixed gap s. NaN if unreachable.
double diffMicrostripWidthForZ(double targetZdiff, double sMm, double hMm, double tMm, double er);

// ---------- losses & skin effect ----------

double skinDepthMm(double fHz, double rhoOhmM = kRhoCu20);

// Microstrip attenuation at f: conductor (thin-strip Rs/Z0w approximation)
// + dielectric (tanδ). Returns dB per metre.
double microstripLossDbPerM(double wMm, double hMm, double tMm, double er,
                            double tanDelta, double fHz);

// Trace series resistance over its length at DC and at frequency f
// (skin-limited shell model, floored at DC value). Ω.
double traceResistanceDc(double wMm, double tMm, double lengthMm, double tempC = 20);
double traceResistanceAc(double wMm, double tMm, double lengthMm, double fHz, double tempC = 20);

// ---------- current capacity & thermal ----------

// IPC-2221 continuous current for a copper cross-section at a temperature
// rise deltaT (°C). external = outer layer (k = 0.048) vs internal (0.024).
double ipc2221CurrentA(double widthMm, double thickMm, double deltaTC, bool external);

// Onderdonk adiabatic fusing current for a pulse of `seconds`, ambient Ta.
double onderdonkFusingA(double widthMm, double thickMm, double seconds, double ambientC = 20);

// ---------- vias ----------

double viaInductanceNH(double heightMm, double drillMm);                       // L ≈ 0.2h(ln(4h/d)+1)
double viaCapacitancePF(double er, double boardThickMm, double padMm, double antipadMm);
double viaResistanceOhm(double heightMm, double drillMm, double platingMm = 0.025, double tempC = 20);
double viaThermalResistanceKW(double heightMm, double drillMm, double platingMm = 0.025);
double viaCurrentA(double drillMm, double platingMm, double deltaTC);          // barrel as internal trace

// ---------- coupling & planes ----------

// Saturated backward-crosstalk coefficient estimate for parallel traces at
// edge separation s over height h (rule of thumb: 1/(1+(s/h)^2)).
double crosstalkCoefficient(double sMm, double hMm);

double planeCapacitancePF(double areaMm2, double dielectricMm, double er);

} // namespace dc::phys
