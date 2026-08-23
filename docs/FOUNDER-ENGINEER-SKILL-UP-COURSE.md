# Founder-Engineer Skill-Up Course

Date: 2026-08-23  
Duration: 52 weeks  
Designed for: full-time employment plus a technically serious electromechanical startup

## 1. Executive verdict

You can build a compelling prototype and can become capable of leading this startup.
The current evidence does **not** show that you can yet build and operate a survivable
industrial software company without stronger engineering discipline, domain depth,
customer evidence, and a small expert team.

The gap is not mainly GPU access. The gap is the ability to:

1. Select one commercially valuable problem and reject attractive distractions.
2. Explain and debug critical code without depending on an AI agent.
3. Convert mechanical and manufacturing knowledge into tested invariants.
4. Correlate simulation and generated geometry with physical measurements.
5. Maintain a clean, reviewable, releasable software system.
6. Speak with users, charge for a result, and learn from rejection.

The recommended wedge is:

> Turn a product brief, PCB, component models, and reference images into an editable,
> manufacturable electronics enclosure with assembly, validation, and release evidence.

Do not initially claim to replace SolidWorks, Fusion, Altair, or every mechanical
engineer. Win one workflow for one customer segment.

## 2. Evidence used for this assessment

This assessment is based on the current DesignStudio worktree and the conversations
that produced it. It is not a personality or intelligence assessment.

Observed repository evidence on 2026-08-23:

- Approximately 155,000 lines across C++, headers, Python, TypeScript, C#, and CUDA.
- 93 modified tracked files and 2,603 untracked files.
- Approximately 17,993 tracked added lines in the current diff.
- Large source files include `DesignChatPanel.cpp` at 3,132 lines and
  `MainWindow.cpp` at 3,084 lines.
- Generated/build output exists inside the source tree, and the repository hygiene
  checker reports generated data tracked as source.
- The current CMake configuration succeeds and the native code compiles.
- The project contains real tests, typed schemas, exact BREP gates, a C ABI, and
  explicit unavailable states for unimplemented solver workers.

Observed machine evidence:

- Intel Core i5-13420H, 12 logical CPUs.
- Approximately 14 GiB usable RAM and 41 GiB free disk space.
- NVIDIA RTX 4050 Mobile is visible on PCIe.
- No NVIDIA kernel driver is active, `nvidia-smi` cannot communicate with the GPU,
  and `nvcc` is not installed.
- ROCm tools are not installed; this NVIDIA laptop is not a local ROCm target anyway.

These facts demonstrate unusual breadth and persistence. They also demonstrate that
scope control, source control hygiene, modularity, and operational readiness are
currently the highest risks.

## 3. Brutal, confidence-adjusted skill assessment

Scores are out of 5. A low score means “not proven by current evidence,” not “unable
to learn.”

| Area | Score | Evidence-based judgment | Proof required to raise the score |
|---|---:|---|---|
| Vision and ambition | 4.5 | You see a valuable convergence of AI, ECAD, CAD, simulation, and manufacturing. | Convert the vision into one sellable wedge and a two-page product specification. |
| Persistence | 4.0 | You continue through difficult integration and runtime problems. | Sustain a weekly cadence for six months without uncontrolled scope expansion. |
| AI-assisted orchestration | 4.0 | You can direct agents across C++, Python, Qt, FreeCAD, schemas, tests, and docs. | Demonstrate repeatable agent evals, cost controls, and independent review of every critical change. |
| Independent coding mastery | 2.0 | A large amount of code exists, but authorship and unaided understanding cannot be inferred from AI-assisted output. | Explain, modify, and debug selected critical modules in a timed no-AI review. |
| Software architecture | 2.5 | Typed boundaries and evidence gates are strong; very large classes and broad coupling remain. | Split monoliths, enforce dependency direction, publish architecture tests, and operate from a clean clone. |
| C++ systems engineering | 2.0 | Native library, ABI, solver, and Qt integration compile, but performance, ownership, concurrency, and failure behavior are not deeply proven. | Complete the C++ and performance capstones and pass an external code review. |
| CAD/BREP engineering | 2.0 | You understand that BREP must be authoritative, but earlier reasoning sometimes mixed rendering, UI, geometry, and kernel responsibilities. | Build and explain a robust parametric feature graph and pass geometry round-trip/property tests. |
| Mechanical/manufacturing | 1.5 | Rules and ribs exist, but professional mould design, tolerancing, materials, fatigue, and validation are incomplete. | Produce a reviewed design, manufacture it, measure it, and document corrections with a practising engineer. |
| FEA/numerical methods | 1.5 | A reference linear solver exists, but meshing, element formulation, contacts, convergence, and experimental correlation are not demonstrated. | Hand-solve canonical cases, reproduce them numerically, then correlate a physical test. |
| ML fundamentals | 1.5 | Strong interest in AI systems; no evidence yet of training, ablation, data curation, or benchmark methodology at research/production depth. | Train and evaluate small models, publish error analysis, and reproduce a paper-scale result. |
| Agent/evaluation engineering | 2.5 | Typed contracts and release gating are promising. | Build a 100+ case domain eval, compare providers, measure cost/latency/error, and prevent regressions. |
| Product judgment | 1.5 | The vision repeatedly expands across rendering, web, CAD, FEA, generative design, and multiple industries before one narrow outcome is validated. | Conduct 30 interviews, reject at least three feature ideas using evidence, and obtain a paid pilot. |
| Execution and Git hygiene | 1.0 | 93 modified files and 2,603 untracked files make review, rollback, attribution, and release unsafe. | Zero unexplained dirty files, small commits, protected main, CI, release tags, and reproducible artifacts. |
| Commercial/sales ability | 1.0 | No verified customer interviews, design partners, pricing tests, or revenue evidence was found. | Close one paid design-partner pilot and document the sales cycle. |
| Founder readiness today | 2.0 | Capable prototype orchestrator; not yet an independently validated technical founder operating a focused company. | Complete the minimum founder gates in Section 14. |

### What this means plainly

- You are ahead of a beginner in breadth, ambition, and AI leverage.
- You are behind a production technical founder in depth, focus, change management,
  customer discovery, and validated domain judgment.
- The repository currently proves that you can create a lot. It does not yet prove
  that you can decide what should not be created, keep it maintainable, or sell it.
- Compute credits can accelerate experiments. They cannot repair weak problem
  selection, unclear requirements, invalid physics, or poor source control.

## 4. Career strategy that reinforces the startup

### Primary job target

Target roles such as:

- C++/ML systems engineer in CAD, CAE, robotics, manufacturing, simulation, or AI
  infrastructure.
- Applied AI engineer building evaluated tool-using systems rather than prompt-only
  chatbots.
- Geometry, visualization, or scientific-computing software engineer.

These roles pay for skills that directly strengthen DesignStudio: C++, numerical
methods, GPU programming, distributed jobs, geometry kernels, testing, and production
AI evaluation.

### Do not lead with these claims yet

- “ML research scientist” without model-training and research evidence.
- “Senior CAE engineer” without solver setup and physical correlation evidence.
- “CAD kernel expert” without robust topology/constraint work.
- “CTO of an industrial platform” before a focused product, users, and a maintainable
  release process exist.

### Portfolio required for the job track

1. A clean, public CPU/CUDA/ROCm sparse-solver benchmark with profiling and a technical
   report.
2. A parametric BREP enclosure generator with deterministic STEP round-trip tests.
3. A 100-case mechanical-agent evaluation suite with provider cost/quality results.
4. A manufactured enclosure with CAD-to-physical dimensional and stiffness/thermal
   correlation.
5. Two meaningful pull requests to established open-source projects such as FreeCAD,
   Open CASCADE-related tooling, Gmsh, CalculiX integrations, or an ML systems project.

## 5. Sustainable weekly operating schedule

Assumption: a full-time job or intensive job search remains the financial foundation.
Target 15-17 deliberate hours per week outside employment. More is not automatically
better; chronic exhaustion degrades engineering judgment.

| Time | Activity |
|---|---|
| Monday, 90 min | Theory and handwritten problem solving; no AI. |
| Tuesday, 90 min | Job interview preparation or portfolio write-up. |
| Wednesday, 90 min | Guided implementation with tests. |
| Thursday, 90 min | Domain study and review with an expert/source. |
| Friday | No startup work. Recovery and relationships. |
| Saturday, 5 hours | One startup vertical slice; finish or remove it. |
| Sunday, 4-5 hours | User interviews, physical experiment, review, and next-week plan. |

Every fourth week is a consolidation week: fewer features, more cleanup, measurement,
writing, and review.

## 6. Rules for using AI during the course

AI is a multiplier and reviewer, not the owner of engineering truth.

1. Write the acceptance test and failure modes before asking for implementation.
2. Ask the model to identify assumptions and missing evidence.
3. Never accept a physics, tolerance, material, safety, or manufacturing claim without
   a primary source or expert review.
4. Run tests from a clean environment; do not accept “looks correct.”
5. Keep an AI-change ledger: prompt, model, files, tests, unresolved risks, reviewer.
6. Spend at least two hours each week debugging or implementing without AI.
7. Be able to explain every critical function, data structure, equation, and failure
   path that reaches production.
8. Compare at least two models only on a fixed evaluation set. Do not switch providers
   because one demo felt better.
9. AI may propose geometry; exact kernel checks decide validity. AI may rank solver
   evidence; the solver and release policy decide approval.
10. If you cannot write a failing test for a reported problem, remain in diagnosis.

The official OpenAI eval-driven guide is a useful model for this discipline: start with
a small end-to-end system, label examples with domain experts, connect evals to business
metrics, and improve from measured failures rather than impressionistic judgments:
<https://developers.openai.com/cookbook/examples/partners/eval_driven_system_design/receipt_inspection>.

Anthropic likewise recommends defining success criteria and empirical evaluations
before prompt engineering:
<https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview>.

## 7. Free and conditional compute strategy

Promotional compute is for bounded experiments, not the permanent business model.

| Resource | What is genuinely available | Important condition | Best course use |
|---|---|---|---|
| AMD AI Developer Program | The current portal advertises $100 AMD Developer Cloud credits, AI Academy, and one month of DeepLearning.AI membership. | Approval and current portal terms apply; other AMD pages describe an initial 25 MI300X hours, so verify the award before planning. Credits expire. | Port the sparse solver to HIP/ROCm, benchmark MI300X, and publish results. |
| AMD Developer Cloud | MI300X access and complimentary-hours applications. | Storage is not necessarily covered; destroy instances when finished and export results first. | One reproducible performance sprint, not persistent hosting. |
| NVIDIA Developer Program | Free membership and a complimentary self-paced DLI course. | This is training, not guaranteed ongoing GPU capacity. | CUDA C++ fundamentals and Nsight profiling. |
| NVIDIA Inception | Free startup program, training, partner offers, and cloud-credit offers. | Requires an incorporated company, working website, and at least one developer; credit amount is not guaranteed. | Apply after a clean demo, company site, and benchmark exist. |
| OpenAI for Startups | Community/resources; API credits may be available through participating VC referral codes. | No general unlimited free API. VC-backed benefits are conditional. | Use only after a fixed domain eval and token budget exist. |
| OpenAI Researcher Access | Up to $1,000 API credits for eligible responsible-AI research. | It is a reviewed research program, not a commercial-startup subsidy. | Apply only for a genuine publishable research question, never misrepresent eligibility. |
| Anthropic | Public documentation and workbench; selected programs such as the Anthology Fund include credits. | General Claude API use is paid; large free-credit offers are selective. | Use small paid or partner-funded runs for fixed cross-model evals. |
| AWS Activate | Self-funded startups can begin with credits; provider-backed startups can receive much larger allocations. Current official material advertises up to $5,000 self-funded and up to $200,000 provider-backed. | Requires a real startup profile; larger tiers require an approved provider/accelerator relationship. | Claude through Bedrock, queues, storage, and burst compute after MVP evidence. |
| Google for Startups Cloud | Current AI program advertises up to $350,000 for qualifying funded AI startups. | Designed for eligible funded startups; not an automatic solo-developer grant. | Vertex AI/Claude/Gemini evaluation and production only after funding/eligibility. |
| Microsoft for Startups | Accepted startups receive Azure credits and startup resources. | Allocation depends on offer and current program; verify in the portal. | Windows/enterprise integration or Azure deployment when customer demand requires it. |
| Kaggle Notebooks | Free P100 or T4-class notebook GPU access, roughly 30 hours/week subject to quota, with 12-hour sessions. | Availability and quotas vary; not production hosting. | Small model training, ablations, and reproducible public notebooks. |
| Google Colab | Free notebook access with possible GPUs/TPUs. | Hardware and limits are explicitly not guaranteed. | Tutorials and short experiments. |
| Hugging Face ZeroGPU | Free use of shared GPU Spaces; eligible free accounts can host up to two ZeroGPU Spaces. | Free personal quota is short and designed for demos, not training. | Public interactive model demo and portfolio. |

Official resource links:

- AMD program: <https://developer.amd.com/ai-developer-program/>
- AMD cloud access: <https://www.amd.com/en/developer/resources/cloud-access.html>
- AMD AI Academy: <https://developer.amd.com/amd-ai-academy/>
- NVIDIA Inception: <https://www.nvidia.com/en-us/startups/>
- NVIDIA DLI: <https://learn.nvidia.com/en-us/training/self-paced-courses>
- OpenAI for Startups: <https://openai.com/startups>
- OpenAI Researcher Access: <https://openai.com/form/researcher-access-program/>
- Anthropic startup fund example: <https://www.anthropic.com/news/anthropic-partners-with-menlo-ventures-to-launch-anthology-fund>
- AWS Activate: <https://aws.amazon.com/startups/credits>
- Google AI startup program: <https://cloud.google.com/startup/ai>
- Microsoft for Startups: <https://learn.microsoft.com/en-us/startups/microsoft-for-startups/overview>
- Kaggle notebooks: <https://www.kaggle.com/docs/notebooks>
- Colab FAQ: <https://research.google.com/colaboratory/faq.html>
- Hugging Face ZeroGPU: <https://huggingface.co/docs/hub/spaces-zerogpu>

### Compute-credit application packet

Prepare this before requesting credits:

1. Public project page and 90-second demo.
2. One-paragraph problem and customer description.
3. Exact experiment: model, dataset, hardware, expected hours, output metrics.
4. Why the requested accelerator is necessary.
5. Repository and reproducibility instructions.
6. Data-license and privacy statement.
7. Stop condition and budget cap.
8. Planned public technical report or open-source contribution.

Never activate expiring credits until the dataset, container, test command, and output
storage are ready.

## 8. The 52-week course

Each four-week sprint has four parallel outputs:

- **Knowledge:** concepts you can explain without AI.
- **Engineering:** tested code or physical evidence.
- **Career:** a visible portfolio/interview artifact.
- **Startup:** customer or product-risk reduction.

### Sprint 1, weeks 1-4: Founder operating system and repository recovery

Study:

- Git object model, branches, rebasing, bisecting, worktrees, release tags.
- Build reproducibility, dependency locking, artifact retention, and CI.
- Difference between source, generated evidence, cache, release artifact, and backup.

Work:

- Week 1: inventory every dirty path and classify it as source, test fixture, generated
  evidence, build output, or disposable cache.
- Week 2: establish a clean baseline branch and reproducible clean-clone build.
- Week 3: move generated artifacts to explicit artifact storage/manifests and enforce
  repository hygiene in CI.
- Week 4: write the first architecture decision record and incident postmortem.

Gate:

- Zero unexplained dirty files.
- A new contributor can clone, build, and test using one documented command.
- Every retained artifact has provenance and a retention rule.

### Sprint 2, weeks 5-8: Modern C++ and defensive coding

Study:

- Value semantics, RAII, ownership, smart pointers, move semantics, ranges, templates,
  error handling, ABI boundaries, undefined behavior, sanitizers, and fuzzing.
- CMake targets, include visibility, link interfaces, and packaging.

Work:

- Refactor one critical path into small ownership-explicit components.
- Add AddressSanitizer and UndefinedBehaviorSanitizer jobs.
- Fuzz one binary parser or C API boundary.
- Write benchmarks before and after refactoring.

Career artifact:

- “How I found and fixed a cross-domain indexing bug in a GLB pipeline,” including
  the invariant, failing test, and complexity analysis.

Gate:

- Explain the lifetime and failure behavior of every object in the chosen path.
- No function added during the sprint exceeds 60 lines without written justification.

Recommended resources:

- <https://www.learncpp.com/>
- <https://en.cppreference.com/>
- <https://cmake.org/cmake/help/latest/guide/tutorial/>
- MIT performance engineering: <https://ocw.mit.edu/courses/6-172-performance-engineering-of-software-systems-fall-2018/>

### Sprint 3, weeks 9-12: Architecture, interfaces, and distributed jobs

Study:

- Dependency inversion, ports/adapters, state machines, idempotency, retries, job
  leases, cancellation, observability, schema evolution, and threat modeling.

Work:

- Draw the authoritative data-flow graph from intent to BREP to simulation to release.
- Turn one implicit workflow into a versioned state machine.
- Add idempotency and digest checks to a worker path.
- Split at least one 1,000+ line component along tested boundaries.

Gate:

- Replaying a job cannot silently duplicate or corrupt state.
- A stale geometry or solver result cannot be released.

### Sprint 4, weeks 13-16: Mathematics and numerical reliability

Study:

- Linear algebra, eigenvalues, conditioning, sparse matrices, numerical differentiation,
  optimization, dimensional analysis, uncertainty, and floating-point error.

Work:

- Derive conjugate gradient on paper.
- Implement a small dense reference and compare it with the sparse solver.
- Add residual, conditioning, scaling, and convergence tests.
- Reproduce at least five canonical systems with known solutions.

Gate:

- Explain why the algorithm requires a symmetric positive-definite matrix.
- Detect invalid inputs and non-convergence without presenting false success.

Recommended resources:

- MIT Linear Algebra: <https://ocw.mit.edu/courses/18-06-linear-algebra-spring-2010/>
- Convex Optimization lectures/materials: <https://web.stanford.edu/~boyd/cvxbook/>

### Sprint 5, weeks 17-20: Computational geometry and parametric BREP

Study:

- Topology versus geometry, curves and surfaces, NURBS, BREP validity, tolerances,
  booleans, fillets, offsets, tessellation, topology naming, feature history, constraint
  graphs, and STEP data exchange.

Work:

- Build a small typed feature graph: sketch, pad, pocket, fillet, shell, rib, pattern.
- Implement transactional recompute and stable semantic references.
- Add randomized/property tests for feature sequences.
- Round-trip 100 generated parts through STEP and compare topology, volume, and critical
  dimensions.

Gate:

- At least 99% of supported benchmark programs produce valid solids.
- No renderer or mesh is treated as the engineering authority.

Recommended resources:

- Open CASCADE overview: <https://dev.opencascade.org/doc/overview/html/index.html>
- FreeCAD developer documentation: <https://freecad.github.io/SourceDoc/>
- Gmsh documentation: <https://gmsh.info/doc/texinfo/gmsh.html>

### Sprint 6, weeks 21-24: Mechanical design and manufacturing

Study:

- Statics, strength of materials, joints, fasteners, tolerance stacks, GD&T basics,
  materials, creep, fatigue, thermal expansion, injection moulding, CNC, and additive
  processes.

Work:

- Define a reviewed injection-moulding profile for one material/process pair.
- Generate walls, ribs, bosses, screw towers, snaps, draft, root fillets, and parting
  assumptions from explicit parameters.
- Build a tolerance stack for the camera grip PCB, connector, fasteners, and shell.
- Print or machine one prototype and measure at least 20 critical dimensions.

Gate:

- A practising mechanical/manufacturing engineer reviews the design.
- Measured deviations and assembly problems become tests or rule changes.

### Sprint 7, weeks 25-28: FEA, meshing, and physical correlation

Study:

- Weak form intuition, element types, interpolation, mesh quality, boundary conditions,
  contact, material models, convergence, modal analysis, heat transfer, and model-form
  error.

Work:

- Complete hand calculations for a bar, cantilever, plate-like approximation, and
  thermal-resistance network.
- Solve the same cases with CalculiX or Elmer.
- Run a mesh-convergence study.
- Perform one load-deflection or temperature experiment and compare measurement,
  hand calculation, and solver output.

Gate:

- Error sources are separated into geometry, material, boundary condition, discretization,
  and measurement components.
- No simulation result is labelled validated without correlation evidence.

Recommended resources:

- MIT Finite Element Analysis: <https://ocw.mit.edu/courses/2-092-finite-element-analysis-of-solids-and-fluids-i-fall-2009/>
- CalculiX: <https://www.calculix.de/>
- Elmer: <https://github.com/ElmerCSC/elmerfem>
- OpenRadioss: <https://openradioss.org/>

### Sprint 8, weeks 29-32: ML foundations and multimodal systems

Study:

- Probability, train/validation/test splits, leakage, calibration, gradient descent,
  representation learning, CNNs, transformers, embeddings, fine-tuning, overfitting,
  ablations, and error analysis.

Work:

- Train a small component or drawing classifier on Kaggle/Colab.
- Establish deterministic data splits and a data card.
- Compare a learned baseline with a non-ML heuristic.
- Run ablations and manually inspect every major error class.

Gate:

- A held-out test set remains untouched until the final evaluation.
- The model must beat a meaningful baseline and justify its inference cost.

Recommended resources:

- Hugging Face LLM course: <https://huggingface.co/learn/llm-course/chapter1/1>
- Practical Deep Learning: <https://course.fast.ai/>
- Full Stack Deep Learning: <https://fullstackdeeplearning.com/>

### Sprint 9, weeks 33-36: Agents, structured outputs, and evaluations

Study:

- Tool schemas, planning versus workflows, retrieval, provenance, prompt injection,
  structured outputs, trace evaluation, human escalation, provider abstraction, cost,
  latency, and model drift.

Work:

- Create at least 100 gold tasks from real mechanical workflows.
- Define exact, semantic, human, latency, and cost graders.
- Compare at least two hosted models and one open model on identical inputs.
- Record failure taxonomies and turn common failures into deterministic checks.

Gate:

- No provider is selected from anecdotes or a single demo.
- The system can abstain and ask for clarification.
- AI output cannot bypass BREP, manufacturing, solver, or approval gates.

Recommended resources:

- Hugging Face Agents course: <https://huggingface.co/learn/agents-course/en/unit0/introduction>
- OpenAI eval-driven design: <https://developers.openai.com/cookbook/examples/partners/eval_driven_system_design/receipt_inspection>
- Anthropic prompt/eval overview: <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview>
- Anthropic injection defenses: <https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks>

### Sprint 10, weeks 37-40: GPU, ROCm/CUDA, and performance engineering

Study:

- GPU execution model, memory hierarchy, occupancy, transfers, sparse kernels, profiling,
  mixed precision, reproducibility, CUDA, HIP, ROCm, and performance portability.

Work:

- Repair and verify the local NVIDIA driver before installing CUDA development tools.
- Benchmark the CPU reference solver.
- Run the CUDA implementation locally if supported.
- Port or validate a HIP implementation on AMD Developer Cloud MI300X.
- Publish throughput, latency, energy/cost assumptions, numerical error, and profiler
  evidence.

Gate:

- GPU work must demonstrate a measured benefit on a representative workload.
- CPU remains the correctness oracle.
- No benchmark excludes transfer/setup cost without explicitly labelling it.

Recommended resources:

- AMD AI Academy: <https://developer.amd.com/amd-ai-academy/>
- ROCm docs: <https://rocm.docs.amd.com/>
- NVIDIA DLI: <https://learn.nvidia.com/en-us/training/self-paced-courses>
- CUDA C++ guide: <https://docs.nvidia.com/cuda/cuda-c-programming-guide/>

### Sprint 11, weeks 41-44: Browser UX, collaboration, and cloud operations

Study:

- Three.js scene graphs and PBR, picking, sections, semantic selection, WebAssembly
  boundaries, queues, object storage, tenancy, authentication, backups, monitoring,
  cost controls, and incident response.

Work:

- Keep BREP computation authoritative on the trusted host/service.
- Make the browser a semantic editor/reviewer, not merely a GLB viewer.
- Add visible agent plans, diffs, undo, parameters, section/explode modes, measurement,
  and evidence links.
- Deploy a small staging environment with per-job cost and trace monitoring.

Gate:

- Five target users complete a fixed workflow without live coaching.
- A failed job is diagnosable from logs and retained evidence.
- Tenant data cannot be accessed across accounts in security tests.

### Sprint 12, weeks 45-48: Customer discovery, positioning, and sales

Study:

- Problem interviews, ICP selection, workflow mapping, willingness to pay, pricing,
  design partnerships, procurement, security questionnaires, and pilot contracts.

Work:

- Interview 30 people in one segment: small hardware teams, industrial-design firms,
  enclosure specialists, or electronics consultancies.
- Observe at least five complete current workflows.
- Quantify time, cost, rework, and failure risk.
- Offer a paid, narrow pilot with a clear deliverable and manual support behind the
  software where necessary.

Gate:

- At least three users ask to continue using the product.
- At least one customer pays, signs a pilot, or gives a concrete procurement path.
- If not, revise the customer/problem—not the rendering technology.

Recommended resource:

- YC Startup School: <https://www.startupschool.org/>

### Sprint 13, weeks 49-52: Integrated capstone, employment, and launch decision

Work:

- Run one camera-grip or enclosure project from intent through PCB integration, BREP,
  manufacturing checks, simulation, physical prototype, measurements, drawings, STEP,
  BOM, and release evidence.
- Give the same input to the current baseline and the final system; measure human time,
  errors, cost, and successful task completion.
- Publish four technical portfolio pieces and record concise demos.
- Complete 30 carefully targeted job applications, five mock interviews, and weekly
  networking with engineers in the target domain.
- Prepare an investor/design-partner data room only if customer gates are met.

Final gate:

- Clean reproducible release.
- Independent technical review.
- Physical correlation evidence.
- Three active design partners or one paid pilot.
- A credible job pipeline or offer that supports financial runway.

## 9. Judgment training

### Weekly decision memo

For every consequential choice, write one page:

1. Decision and deadline.
2. Customer problem affected.
3. Evidence currently available.
4. Assumptions and confidence.
5. At least three options, including “do nothing.”
6. Reversible versus irreversible consequences.
7. Build, buy, integrate, or partner analysis.
8. Success metric and kill criterion.
9. Actual result and what changed your mind.

### Technology replacement rule

Do not replace Qt, FreeCAD, OCCT, Three.js, a solver, cloud provider, or model until:

- the failure is reproduced;
- the responsible layer is identified;
- a benchmark represents the real workflow;
- at least two remedies are tested;
- migration cost and lost capabilities are documented;
- a user-facing metric improves materially.

### Claim ledger

Classify every public product claim as:

- Demonstrated in a reproducible test.
- Correlated with physical evidence.
- Reviewed by a qualified expert.
- Planned but unavailable.
- Hypothesis only.

Never market a hypothesis as a validated engineering capability.

## 10. Job-search system

Weekly minimum from week 5 onward:

- Three data-structure/algorithm exercises, with written complexity analysis.
- One C++ or ML systems design question.
- One explanation of a DesignStudio technical decision in five minutes.
- One public commit, issue, review, or technical note.
- Two conversations with engineers or hiring managers in target companies.

Monthly:

- One mock coding interview.
- One mock systems/domain interview.
- One resume revision based on actual response rates.
- One open-source contribution.

Use the startup as a portfolio only after isolating a clean, understandable slice. A
recruiter cannot review 155,000 mixed lines and thousands of untracked files.

## 11. Startup operating system

Maintain one active quarterly objective and no more than three measurable key results.

Suggested first objective:

> Prove that DesignStudio can reduce the time required to produce a manufacturable
> PCB enclosure without increasing first-pass physical failures.

Suggested key results:

1. Ten representative enclosure jobs with expert-reviewed ground truth.
2. At least 90% hard-constraint satisfaction and valid STEP export.
3. Three observed users complete the workflow and one agrees to a paid pilot.

Backlog policy:

- A feature requires a named user/problem, measurable outcome, owner, and test.
- Only one end-to-end vertical slice may be in progress.
- New rendering work is blocked when geometry or manufacturing correctness is failing.
- New solver types are blocked until the existing solver has setup and correlation
  evidence.
- Generated artifacts never enter source directories without an explicit fixture
  purpose and review.

## 12. Expert network required

Do not attempt to learn every profession deeply enough to replace its practitioners.
Build a review network:

- CAD/OCCT or geometry-kernel engineer.
- Injection-moulding/tooling engineer.
- Structural/thermal FEA engineer.
- Electronics/PCB engineer.
- Industrial designer or ergonomics specialist.
- B2B product/sales mentor.

Target one structured review per month. Pay experts when possible. Record decisions,
not confidential customer information, into rules and test cases.

Likely founding-team shape after validation:

- You: product, AI systems, integration, and technical direction.
- Cofounder or early lead: deep mechanical/manufacturing and customer credibility.
- Early engineer: C++ geometry/numerical systems.
- Early product/full-stack engineer: browser workflow and cloud operations.

## 13. Course scorecard

Score monthly from 0-2 for each item: 0 absent, 1 partial, 2 independently proven.

| Dimension | Evidence |
|---|---|
| Clean engineering | Clean clone builds, tests, and releases; small reviewable commits. |
| Independent mastery | No-AI explanation and debugging exercises pass. |
| CAD correctness | Valid BREP and round-trip benchmark. |
| Manufacturing correctness | Expert review and physical measurements. |
| Physics correctness | Convergence plus experiment correlation. |
| AI reliability | Fixed eval suite, error taxonomy, cost/latency measurements. |
| Product usability | Observed task completion without coaching. |
| Customer evidence | Interviews, repeated use, signed pilot, or revenue. |
| Career evidence | Portfolio, interviews, offers, open-source reviews. |
| Judgment | Decision memos predict outcomes and kill weak work early. |

Graduation requires at least 16/20, with no zero in clean engineering, independent
mastery, manufacturing correctness, customer evidence, or judgment.

## 14. Minimum founder-readiness gates

Do not call the company ready to scale until all are true:

- [ ] The repository is clean and releasable from a fresh clone.
- [ ] You can independently explain and debug every critical release path.
- [ ] One narrow product workflow is measurably better than the incumbent process.
- [ ] Generated geometry survives expert review and physical prototyping.
- [ ] Solver claims have convergence and correlation evidence.
- [ ] AI behavior has a fixed eval set and monitored production traces.
- [ ] At least three design partners use the product repeatedly.
- [ ] At least one customer has paid or completed procurement steps.
- [ ] Cloud unit economics are measured without promotional credits.
- [ ] Security, backups, data ownership, licensing, and incident response are documented.
- [ ] A second qualified person can operate the system and review releases.
- [ ] Employment/runway planning prevents desperate product or fundraising decisions.

## 15. Immediate 30/60/90-day plan

### Days 1-30

- Stop feature expansion.
- Inventory and clean the worktree without losing evidence.
- Create one reproducible baseline release.
- Fix or document the NVIDIA driver state; do not install CUDA until the driver works.
- Select one enclosure customer segment.
- Conduct five problem interviews.
- Begin Modern C++ and Git modules.

### Days 31-60

- Split one major monolith.
- Add sanitizers and a fuzz target.
- Define ten gold enclosure tasks and hard constraints.
- Complete another ten interviews.
- Publish the first technical portfolio article.
- Join AMD AI Developer Program and NVIDIA Developer Program; prepare but do not yet
  waste expiring credits.

### Days 61-90

- Run the first BREP round-trip benchmark.
- Manufacture one controlled prototype.
- Compare dimensions with the CAD model.
- Build the first 30-case agent eval.
- Apply to narrowly aligned jobs with the cleaned portfolio.
- Decide, from interview evidence, whether the wedge remains PCB enclosures or needs
  refinement.

## 16. Final perspective

A survivable startup is not the largest architecture you can imagine. It is a small,
reliable machine that repeatedly solves an expensive problem for people who pay, while
learning faster than competitors.

Your present advantage is ambition, persistence, and the ability to orchestrate AI
across disciplines. Your present threat is confusing breadth with mastery and activity
with validation. This course is designed to preserve the advantage while attacking the
threat directly.
