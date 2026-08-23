# DesignStudio mechanical engine boundary

DesignStudio now has a backend-neutral mechanical analysis boundary. It is
designed to improve the camera-grip workflow without pretending that a language
model is a finite-element, contact, fatigue, or mould-flow solver.

## Runtime layers

```text
digest-bound FreeCAD/OpenCascade B-Rep
        |
        v
canonical geometry digest + mesh/loads/constraints
        |
        +--> DesignCore CPU reference solver (always available)
        |
        +--> optional DesignCore CUDA worker (cuBLAS + CSR kernel)
        |
        +--> interchangeable remote workers
              linear static | modal | thermal | contact | impact
              fatigue/creep | injection-flow/warpage
        |
        v
digest-bound result + evidence + incomplete/fail findings
        |
        v
AI advisory ranking and explanations
        |
        v
atomic publication after typed evidence preconditions
```

The AI layer can propose dimensions, material/profile changes, or a request to
run another worker. It cannot approve geometry, rewrite a solver result, or
declare a candidate production-ready.

## Native engine

The native implementation lives in:

- `core/include/designcore/mechanical.h` — C++ solver contracts;
- `core/src/mechanical.cpp` — backend selection and prepared-system dispatch;
- `core/src/mechanical_cpu.cpp` — deterministic CSR conjugate-gradient reference;
- `core/src/mechanical_formulations.cpp` — axial-bar and Euler–Bernoulli beam
  assembly with displacement, reaction, stress, rotation and strain-energy
  recovery;
- `core/src/mechanical_cuda.cu` — optional CUDA/cuBLAS path, enabled with
  `-DDESIGNCORE_ENABLE_CUDA=ON` when a CUDA toolkit is present;
- `core/include/designcore/c_api.h` and `core/src/c_api.cpp` — stable opaque C
  ABI for C#, Python `ctypes`, and future service workers;
- `core/tests/test_mechanical.cpp` — C++ and flat-ABI regression coverage.

The current exact native scope is linear-static equilibrium for a prepared SPD
stiffness matrix plus verified one-dimensional axial-bar chains and planar
Euler–Bernoulli cantilevers. It applies fixed displacement constraints, reports
residual and iteration evidence, and recovers formulation-specific reactions,
stress, rotation and strain energy. It does not claim that these reduced-order
models establish three-dimensional stress, contact, fatigue, buckling or
manufacturing suitability.

The CUDA backend is optional by design. On an RTX 4050, it is appropriate for
moderate sparse solves and repeated candidate evaluation. Large nonlinear,
contact, drop, fatigue, and mould-flow studies still belong in specialized
workers with their own validated material/contact models. A CPU fallback is
required for reproducibility and for machines without CUDA.

## Worker and AI contracts

- `docs/schemas/mechanical-analysis-job-v1.schema.json` requires an approved
  geometry digest before dispatch;
- `docs/schemas/mechanical-analysis-result-v1.schema.json` records worker,
  backend, metrics, evidence, warnings, and release eligibility;
- `docs/schemas/mechanical-feedback-v1.schema.json` makes AI output explicitly
  advisory;
- `services/ai-gateway/solver_workers.py` dispatches the native linear worker
  and truthful unavailable placeholders for future solver types;
- `services/ai-gateway/mechanical_feedback.py` ranks only evidence-bearing
  results and emits follow-up proposals when exact analysis or approval is
  missing.

The phrase “best design” must therefore mean “best candidate among the stated
requirements with the required computed and measured evidence,” not “the
candidate with the highest AI score.” If a required worker is unavailable, the
result remains explicitly incomplete and atomic publication preconditions are
not satisfied.
