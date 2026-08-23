# Self-balancing sphere demonstrator

This work package proves that DesignStudio can generate and validate a cross-domain product package rather than only trace or display geometry. The reference product is a hollow spherical robot that moves a dense ballast in two axes when an IMU reports surface roll, surface pitch, roll rate or pitch rate.

## Generated package

The **Enclosure → Generate self-balancing sphere STEP** action writes:

- `self_balancing_sphere_assembly.step`: complete AP-style STEP assembly;
- `self_balancing_sphere_cutaway.step`: upper shell omitted for internal inspection;
- `parts/*.step`: one round-trip-validated B-Rep file for every part;
- `self_balancing_sphere.dsproj`: schema-v3 mechatronic project, material data, assembly joints, cross-domain constraints, electrical architecture and rationale;
- `controller_response.csv`: deterministic two-axis actuator response.

The default 160 mm diameter demonstrator contains 24 solid parts across four domains:

- mechanical: split 3 mm shell, equatorial reinforcement, four frame struts, XY guide rails, carriage and tungsten ballast;
- electromechanical: two geared motors with encoder intent;
- electronic: four-layer controller PCB, six-axis IMU, MCU, two H-bridges and buck power conversion;
- electrical: 3S battery, fuse, service disconnect and power/ground harnesses.

## Balance law and physical envelope

For each surface axis, the static ballast request is

`ballast offset = (total mass / ballast mass) × sphere radius × tan(surface angle)`.

Angular-rate damping adds a signed displacement request before the command is clamped to rail travel. The actuator simulation then enforces travel, speed and acceleration limits. With the default 0.35 kg ballast, 1.24 kg total mass, 80 mm radius and ±35 mm travel, the calculated static balance envelope is approximately ±7.04°. A steeper request is reported as saturated; the software does not pretend the mechanism can hold it.

This is a control and packaging demonstrator, not a certified dynamic model. A production design still needs contact/friction modelling, coupled sphere/ballast dynamics, motor torque and thermal sizing, battery protection review, tolerance-stack analysis, impact load cases, modal analysis and FEA. The shell and internal frame are valid B-Rep solids, but `targetSafetyFactor` remains a requirement until those load cases are solved.

Controller masses are explicit lumped engineering inputs. They are not inferred from the simple component-envelope solids; selected production parts and as-built shell/frame volumes must replace them before dynamic or motor-sizing results are accepted.

## Validation gates

Automated tests verify:

1. parameter range and internal-fit rejection;
2. angle and angular-rate contributions to ballast movement;
3. command saturation and actuator travel/speed limits;
4. controller convergence on both axes;
5. B-Rep validity for every generated part;
6. STEP write/read round trips;
7. full-assembly STEP tessellation through the public DesignCore geometry API;
8. 160 mm assembly bounding box;
9. schema-v3 project reload through the document API;
10. headless generation through the actual Qt application command path.

For automated use:

```sh
DesignStudio --smoke-test --generate-balancing-sphere /path/to/package
```
