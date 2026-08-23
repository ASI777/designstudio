from __future__ import annotations

import copy
import importlib.util
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "DesignStudio" / "mechanical_cad.py"
SPEC = importlib.util.spec_from_file_location("mechanical_cad", MODULE)
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(module)


def program():
    return {
        "schema": module.SCHEMA,
        "program_id": "bracket-v1",
        "units": "mm",
        "author": "sol-5.6",
        "commands": [
            {"id": "base", "op": "part.box",
             "params": {"length_mm": 80, "width_mm": 40, "height_mm": 4},
             "provenance": ["reference/front#base"]},
            {"id": "mount", "op": "part.cylinder",
             "params": {"radius_mm": 4, "height_mm": 4,
                        "base_mm": [10, 10, 0]},
             "provenance": ["reference/top#mount-hole"]},
            {"id": "joint", "op": "part.sphere",
             "params": {"radius_mm": 6, "center_mm": [20, 20, 4]},
             "provenance": ["requirements#joint-envelope"]},
            {"id": "collar", "op": "part.ellipsoid",
             "params": {"radial_radius_mm": 8, "axial_radius_mm": 3,
                        "center_mm": [20, 20, 4], "axis": [0, 1, 0]},
             "provenance": ["requirements#joint-collar"]},
            {"id": "taper", "op": "part.cone",
             "params": {"radius1_mm": 5, "radius2_mm": 3, "height_mm": 24,
                        "base_mm": [30, 20, 4], "direction": [1, 0, 0]},
             "provenance": ["requirements#tapered-link"]},
            {"id": "cut", "op": "feature.hole",
             "params": {"base": "base", "radius_mm": 2,
                        "center_mm": [10, 10, 0], "depth_mm": 4},
             "provenance": ["reference/top#hole-diameter"]},
            {"id": "ribs", "op": "pattern.linear",
             "params": {"source": "mount", "count": 3, "spacing_mm": 20,
                        "direction": [1, 0, 0]},
             "provenance": ["reference/top#mount-pattern"]},
        ],
        "checks": [
            {"kind": "valid_shape", "target": "cut"},
            {"kind": "volume", "target": "cut"},
        ],
    }


def test_valid_program_is_normalized_and_digest_is_stable():
    first = module.validate_program(program())
    second = module.validate_program(copy.deepcopy(program()))
    assert first == second
    assert len(module.program_digest(first)) == 64
    assert "part.sphere" in module.SUPPORTED_OPERATIONS
    assert "part.ellipsoid" in module.SUPPORTED_OPERATIONS
    assert "part.cone" in module.SUPPORTED_OPERATIONS
    assert "pattern.circular" in module.SUPPORTED_OPERATIONS
    assert module.SUPPORTED_OPERATIONS[-1] == "tolerance.stack.check"


def test_oriented_box_requires_a_right_handed_orthonormal_basis():
    candidate = program()
    candidate["commands"][0]["params"].update({
        "basis_x": [0, 1, 0],
        "basis_y": [0, 0, 1],
        "basis_z": [1, 0, 0],
    })
    normalized = module.validate_program(candidate)
    assert normalized["commands"][0]["params"]["basis_z"] == [1, 0, 0]

    for mutation in (
        {"basis_z": [0, 0, 2]},
        {"basis_y": [0, 1, 0]},
        {"basis_z": [-1, 0, 0]},
    ):
        invalid = copy.deepcopy(candidate)
        invalid["commands"][0]["params"].update(mutation)
        try:
            module.validate_program(invalid)
            raise AssertionError("invalid oriented-box basis was accepted")
        except module.MechanicalCadProgramError:
            pass


def test_equal_radius_cone_materializes_as_equivalent_cylinder():
    class FakeApp:
        @staticmethod
        def Vector(*values):
            return values

    class FakePart:
        @staticmethod
        def makeCylinder(radius, height, base, direction):
            return ("cylinder", radius, height, base, direction)

        @staticmethod
        def makeCone(*_args):
            raise AssertionError("equal-radius cone reached the OCCT cone primitive")

    original = module._app_part
    module._app_part = lambda: (FakeApp, FakePart)
    try:
        shape = module._build_shape(object(), "part.cone", {
            "radius1_mm": 8.0,
            "radius2_mm": 8.0,
            "height_mm": 20.0,
            "base_mm": [1.0, 2.0, 3.0],
            "direction": [0.0, 0.0, 1.0],
        })
    finally:
        module._app_part = original
    assert shape[0] == "cylinder"
    assert shape[1:3] == (8.0, 20.0)


def test_unknown_or_out_of_order_commands_fail_closed():
    unknown = program()
    unknown["commands"][0]["op"] = "feature.magic"
    try:
        module.validate_program(unknown)
        raise AssertionError("unknown mechanical feature was accepted")
    except module.MechanicalCadProgramError:
        pass

    stale = program()
    stale["commands"][5]["params"]["base"] = "future"
    try:
        module.validate_program(stale)
        raise AssertionError("forward feature reference was accepted")
    except module.MechanicalCadProgramError:
        pass


def test_units_dimensions_and_provenance_are_required():
    for field, value in (("units", "inch"), ("author", "")):
        candidate = program()
        candidate[field] = value
        try:
            module.validate_program(candidate)
            raise AssertionError(f"invalid {field} was accepted")
        except module.MechanicalCadProgramError:
            pass

    candidate = program()
    candidate["commands"][0]["provenance"] = []
    try:
        module.validate_program(candidate)
        raise AssertionError("missing provenance was accepted")
    except module.MechanicalCadProgramError:
        pass


def test_freecad_object_names_cannot_collide_after_sanitization():
    candidate = program()
    candidate["commands"][1]["id"] = "mount-alt"
    candidate["commands"][5]["params"]["base"] = "mount-alt"
    candidate["commands"][5]["id"] = "mount_alt"
    try:
        module.validate_program(candidate)
        raise AssertionError("sanitized FreeCAD object-name collision was accepted")
    except module.MechanicalCadProgramError:
        pass


def test_reference_profiles_and_revolve_parameters_are_supported():
    candidate = program()
    candidate["commands"] = [
        {"id": "profile", "op": "sketch.rectangle",
         "params": {"width_mm": 6, "height_mm": 10},
         "provenance": ["drawing/section#profile"]},
        {"id": "turned", "op": "feature.revolve",
         "params": {"profile": "profile", "axis_origin_mm": [0, 0, 0],
                    "axis_direction": [0, 1, 0], "angle_deg": 360},
         "provenance": ["drawing/section#axis"]},
        {"id": "edge", "op": "feature.chamfer",
         "params": {"base": "turned", "size_mm": 0.5},
         "provenance": ["drawing/detail#edge-break"]},
    ]
    candidate["checks"] = [{"kind": "valid_shape", "target": "edge"}]
    normalized = module.validate_program(candidate)
    assert [item["op"] for item in normalized["commands"]] == [
        "sketch.rectangle", "feature.revolve", "feature.chamfer"]


def test_native_sketch_and_assembly_commands_validate():
    candidate = {
        "schema": module.SCHEMA,
        "program_id": "native-assembly",
        "units": "mm",
        "author": "test",
        "commands": [
            {"id": "sketch", "op": "sketch.create", "params": {"plane": "XY"},
             "provenance": ["test#sketch"]},
            {"id": "line", "op": "sketch.line",
             "params": {"sketch": "sketch", "start_mm": [0, 0, 0],
                        "end_mm": [10, 0, 0]}, "provenance": ["test#line"]},
            {"id": "horizontal", "op": "sketch.constraint.add",
             "params": {"sketch": "sketch", "kind": "horizontal",
                        "references": [0]}, "provenance": ["test#horizontal"]},
            {"id": "part", "op": "part.box",
             "params": {"length_mm": 10, "width_mm": 10, "height_mm": 10},
             "provenance": ["test#part"]},
            {"id": "assembly", "op": "assembly.create", "params": {},
             "provenance": ["test#assembly"]},
            {"id": "component", "op": "assembly.component",
             "params": {"assembly": "assembly", "source": "part"},
             "provenance": ["test#component"]},
        ],
        "checks": [{"kind": "sketch_constraints", "target": "horizontal"}],
    }
    normalized = module.validate_program(candidate)
    assert [item["op"] for item in normalized["commands"]][-2:] == [
        "assembly.create", "assembly.component"]

    invalid = copy.deepcopy(candidate)
    invalid["commands"][-1]["params"]["source"] = "future"
    try:
        module.validate_program(invalid)
        raise AssertionError("assembly forward reference was accepted")
    except module.MechanicalCadProgramError:
        pass


def test_topology_mates_and_tolerance_stacks_validate():
    candidate = copy.deepcopy(program())
    candidate["program_id"] = "topology-stack"
    candidate["commands"] = [
        {"id": "first", "op": "part.box",
         "params": {"length_mm": 10, "width_mm": 10, "height_mm": 10},
         "provenance": ["test#first"]},
        {"id": "second", "op": "part.box",
         "params": {"length_mm": 10, "width_mm": 10, "height_mm": 10},
         "provenance": ["test#second"]},
        {"id": "assembly", "op": "assembly.create", "params": {},
         "provenance": ["test#assembly"]},
        {"id": "first-component", "op": "assembly.component",
         "params": {"assembly": "assembly", "source": "first"},
         "provenance": ["test#first-component"]},
        {"id": "second-component", "op": "assembly.component",
         "params": {"assembly": "assembly", "source": "second"},
         "provenance": ["test#second-component"]},
        {"id": "face-mate", "op": "assembly.mate",
         "params": {"assembly": "assembly", "first": "first-component",
                    "second": "second-component", "kind": "face_distance",
                    "first_reference": {"component": "first-component", "subelement": "Face6"},
                    "second_reference": {"component": "second-component", "subelement": "Face5"},
                    "distance_mm": 1},
         "provenance": ["test#face-mate"]},
        {"id": "stack", "op": "tolerance.stack.create",
         "params": {"assembly": "assembly"}, "provenance": ["test#stack"]},
        {"id": "dimension", "op": "tolerance.stack.item",
         "params": {"stack": "stack", "nominal_mm": 10,
                    "plus_mm": 0.1, "minus_mm": 0.1},
         "provenance": ["test#dimension"]},
        {"id": "stack-check", "op": "tolerance.stack.check",
         "params": {"stack": "stack", "lower_limit_mm": 9.8,
                    "upper_limit_mm": 10.2, "method": "rss"},
         "provenance": ["test#stack-check"]},
    ]
    candidate["checks"] = [{"kind": "tolerance_stack", "target": "stack-check"}]
    normalized = module.validate_program(candidate)
    assert normalized["commands"][5]["params"]["first_reference"]["subelement"] == "Face6"
    assert normalized["commands"][-1]["op"] == "tolerance.stack.check"

    invalid = copy.deepcopy(candidate)
    invalid["commands"][5]["params"]["first_reference"]["subelement"] = "Vertex1"
    try:
        module.validate_program(invalid)
        raise AssertionError("unsupported topology reference was accepted")
    except module.MechanicalCadProgramError:
        pass

    invalid = copy.deepcopy(candidate)
    invalid["commands"][-1]["params"]["lower_limit_mm"] = 11
    try:
        module.validate_program(invalid)
        raise AssertionError("inverted tolerance limits were accepted")
    except module.MechanicalCadProgramError:
        pass


if __name__ == "__main__":
    test_valid_program_is_normalized_and_digest_is_stable()
    test_oriented_box_requires_a_right_handed_orthonormal_basis()
    test_equal_radius_cone_materializes_as_equivalent_cylinder()
    test_unknown_or_out_of_order_commands_fail_closed()
    test_units_dimensions_and_provenance_are_required()
    test_freecad_object_names_cannot_collide_after_sanitization()
    test_reference_profiles_and_revolve_parameters_are_supported()
    test_native_sketch_and_assembly_commands_validate()
    test_topology_mates_and_tolerance_stacks_validate()
    print("MECHANICAL_CAD_PROGRAM_OK")
