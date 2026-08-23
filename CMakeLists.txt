cmake_minimum_required(VERSION 3.20)
project(DesignStudio LANGUAGES CXX VERSION 1.0.0)

include(CTest)

if(BUILD_TESTING)
    find_package(Python3 COMPONENTS Interpreter)
    if(Python3_Interpreter_FOUND)
        # A developer pyenv may precede the distro interpreter while lacking
        # the dependencies exercised by the integrated Desktop tests. Prefer
        # a verified system interpreter when it supplies the declared stack.
        find_program(DESIGNSTUDIO_SYSTEM_PYTHON NAMES python3 PATHS /usr/bin NO_DEFAULT_PATH)
        if(DESIGNSTUDIO_SYSTEM_PYTHON)
            execute_process(
                COMMAND ${DESIGNSTUDIO_SYSTEM_PYTHON} -c
                    "import PIL, cryptography, httpx, jsonschema"
                RESULT_VARIABLE DESIGNSTUDIO_SYSTEM_PYTHON_READY
                OUTPUT_QUIET ERROR_QUIET)
            if(DESIGNSTUDIO_SYSTEM_PYTHON_READY EQUAL 0)
                set(Python3_EXECUTABLE ${DESIGNSTUDIO_SYSTEM_PYTHON})
            endif()
        endif()
add_test(NAME reference_image_set_contract
         COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_reference_image_set.py)
add_test(NAME camera_model_eval_contract
         COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_camera_model_eval.py)
add_test(NAME camera_review_contract
         COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_camera_review.py)
add_test(NAME native_tool_worker_contract
         COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_native_tool_worker.py)
add_test(NAME native_smoke_harness_contract
         COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_native_smoke_harness.py)
        set_tests_properties(reference_image_set_contract PROPERTIES
            LABELS "reference;vision;provenance;security" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        set_tests_properties(native_tool_worker_contract PROPERTIES
            LABELS "freecad;worker;cancellation;transaction;security" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        set_tests_properties(native_smoke_harness_contract PROPERTIES
            LABELS "freecad;smoke;native;regression" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME placement_constraints
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_placement_constraints.py)
        set_tests_properties(placement_constraints PROPERTIES
            LABELS "pcb;placement;agent" TIMEOUT 30)
        add_test(NAME procurement_evidence
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_procurement_evidence.py)
        set_tests_properties(procurement_evidence PROPERTIES
            LABELS "pcb;procurement;datasheet;security" TIMEOUT 30)
        add_test(NAME datasheet_harness_persistence
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_datasheet_harness_persistence.py)
        set_tests_properties(datasheet_harness_persistence PROPERTIES
            LABELS "pcb;datasheet;footprint;integration" TIMEOUT 30)
        add_test(NAME datasheet_auto_footprint_extraction
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_codex_datasheet_extractor.py)
        set_tests_properties(datasheet_auto_footprint_extraction PROPERTIES
            LABELS "pcb;datasheet;footprint;integration" TIMEOUT 60
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME datasheet_sol_visual_inspection
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_codex_datasheet_inspector.py)
        set_tests_properties(datasheet_sol_visual_inspection PROPERTIES
            LABELS "component;datasheet;vision;sol;cost"
            TIMEOUT 60 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME pcb_auto_sized_placement_modes
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_subsystem_placement_modes.py)
        set_tests_properties(pcb_auto_sized_placement_modes PROPERTIES
            LABELS "pcb;placement;routing;integration" TIMEOUT 30)
        add_test(NAME freecad_datasheet_board_bridge
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_freecad_bridge.py)
        set_tests_properties(freecad_datasheet_board_bridge PROPERTIES
            LABELS "pcb;datasheet;footprint;freecad;mechanical;integration" TIMEOUT 30)
        add_test(NAME product_subgraph_propagation
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_product_propagation.py)
        set_tests_properties(product_subgraph_propagation PROPERTIES
            LABELS "product-graph;analysis;mechanical;electrical;thermal;control" TIMEOUT 30)
        add_test(NAME signed_update_installer
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_signed_update.py)
        set_tests_properties(signed_update_installer PROPERTIES
            LABELS "release;security;installer;update" TIMEOUT 30)
        add_test(NAME datasheet_component_3d_assets
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_component_3d_assets.py)
        set_tests_properties(datasheet_component_3d_assets PROPERTIES
            LABELS "pcb;datasheet;3d;freecad;hunyuan;rocm" TIMEOUT 60
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME component_sqlite_library
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_component_library.py)
        set_tests_properties(component_sqlite_library PROPERTIES
            LABELS "component;datasheet;sqlite;deduplication" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME manufacturer_reference_circuit
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_reference_circuit.py)
        set_tests_properties(manufacturer_reference_circuit PROPERTIES
            LABELS "component;datasheet;schematic;electrical;approval" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME distributed_six_axis_robot
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_distributed_robot.py)
        set_tests_properties(distributed_six_axis_robot PROPERTIES
            LABELS "product-graph;robot;electrical;thermal;safety;spice" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME authoritative_dimensions_contract
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_authoritative_dimensions.py)
        set_tests_properties(authoritative_dimensions_contract PROPERTIES
            LABELS "authority;dimensions;units;provenance;cad;manufacturing" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME authoritative_cross_domain_product
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_authoritative_product.py)
        set_tests_properties(authoritative_cross_domain_product PROPERTIES
            LABELS "product;robot;mechanical;structural;materials;electrical;electronics;thermal;safety;manufacturing;atomic"
            TIMEOUT 30 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME engineering_workflow_graph_runtime
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_engineering_graph.py)
        set_tests_properties(engineering_workflow_graph_runtime PROPERTIES
            LABELS "workflow;graph;ai;engineering;repair;atomic"
            TIMEOUT 30 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME joint_electronics_net_contract
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_joint_electronics.py)
        set_tests_properties(joint_electronics_net_contract PROPERTIES
            LABELS "electronics;schematic;datasheet;pinmux;motor-control;can-fd"
            TIMEOUT 30 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME component_sourcing_review_workflow
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_component_workflow.py)
        set_tests_properties(component_sourcing_review_workflow PROPERTIES
            LABELS "component;procurement;digikey;mouser;approval;deduplication" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME physical_product_codesign_contract
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_physical_codesign.py)
        set_tests_properties(physical_product_codesign_contract PROPERTIES
            LABELS "component;datasheet;pcb;topology;mechanical;codesign;approval" TIMEOUT 60
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME physical_product_codesign_live_tools
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_physical_codesign_live_tools.py)
        set_tests_properties(physical_product_codesign_live_tools PROPERTIES
            LABELS "freecad;pcb;topology;mechanical;codesign;approval" TIMEOUT 60
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME hunyuan_omni_rocm_service
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/cloud/hunyuan-omni-amd/test_service.py)
        set_tests_properties(hunyuan_omni_rocm_service PROPERTIES
            LABELS "3d;hunyuan;rocm;cloud;security" TIMEOUT 60
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}/cloud/hunyuan-omni-amd)
        add_test(NAME reference_provenance_gate
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tools/check_reference_provenance.py)
        set_tests_properties(reference_provenance_gate PROPERTIES
            LABELS "reference;provenance;license" TIMEOUT 60
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME interaction_structure_generation
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_interaction_structure.py)
        set_tests_properties(interaction_structure_generation PROPERTIES
            LABELS "mechanical;freecad;interaction;rib;deterministic" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME manufacturing_rules_contract
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_manufacturing_rules.py)
        set_tests_properties(manufacturing_rules_contract PROPERTIES
            LABELS "mechanical;manufacturing;dfm;deterministic" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME rib_network_contract
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_rib_network.py)
        set_tests_properties(rib_network_contract PROPERTIES
            LABELS "mechanical;rib;manufacturing;graph;deterministic" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME mechanical_view_modes_contract
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_mechanical_view_modes.py)
        set_tests_properties(mechanical_view_modes_contract PROPERTIES
            LABELS "mechanical;view;presentation;deterministic" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME mechanical_solver_worker_contract
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/services/ai-gateway/test_mechanical_engine_contract.py)
        set_tests_properties(mechanical_solver_worker_contract PROPERTIES
            LABELS "mechanical;solver;ai;contract;approval"
            TIMEOUT 30 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}/services/ai-gateway
            ENVIRONMENT "DESIGNSTUDIO_CORE_LIBRARY=$<TARGET_FILE:designcore>")
        add_test(NAME reference_form_workflow
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_reference_form.py)
        set_tests_properties(reference_form_workflow PROPERTIES
            LABELS "mechanical;freecad;reference;revision;asset;deterministic"
            TIMEOUT 30 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME physical_design_workflow
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_physical_design.py)
        set_tests_properties(physical_design_workflow PROPERTIES
            LABELS "mechanical;freecad;physical-design;redesign;scope;deterministic"
            TIMEOUT 30 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME mechanical_cad_program_v1
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_mechanical_cad.py)
        set_tests_properties(mechanical_cad_program_v1 PROPERTIES
            LABELS "mechanical;freecad;cad;compatibility" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        find_program(DESIGNSTUDIO_SCHEMA_PYTHON NAMES python3
            PATHS /usr/bin /usr/local/bin NO_DEFAULT_PATH)
        if(NOT DESIGNSTUDIO_SCHEMA_PYTHON)
            set(DESIGNSTUDIO_SCHEMA_PYTHON ${Python3_EXECUTABLE})
        endif()
        add_test(NAME vector_native_cad_contract
            COMMAND ${DESIGNSTUDIO_SCHEMA_PYTHON}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_vector_native_cad.py)
        set_tests_properties(vector_native_cad_contract PROPERTIES
            LABELS "mechanical;freecad;cad;vector-native;schema" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        find_program(DESIGNSTUDIO_FREECAD_LAUNCHER_DEFAULT
            NAMES DesignStudioLauncher
            HINTS "$ENV{DESIGNSTUDIO_PREFIX}/bin")
        set(DESIGNSTUDIO_FREECAD_LAUNCHER
            "${DESIGNSTUDIO_FREECAD_LAUNCHER_DEFAULT}"
            CACHE FILEPATH "FreeCAD-compatible launcher for kernel runtime tests")
        find_program(DESIGNSTUDIO_FREECAD_COMMAND_DEFAULT
            NAMES freecadcmd FreeCADCmd
            HINTS "$ENV{FREECAD_ROOT}/bin")
        set(DESIGNSTUDIO_FREECAD_COMMAND
            "${DESIGNSTUDIO_FREECAD_COMMAND_DEFAULT}"
            CACHE FILEPATH "Headless FreeCAD command for supervised worker tests")
        option(DESIGNSTUDIO_REQUIRE_FREECAD_RUNTIME
            "Fail configuration when the local FreeCAD runtime is unavailable" OFF)
        function(designstudio_register_missing_runtime_test test_name dependency_name)
            if(DESIGNSTUDIO_REQUIRE_FREECAD_RUNTIME)
                message(FATAL_ERROR
                    "${dependency_name} is required for ${test_name}; set the corresponding FreeCAD path")
            endif()
            add_test(NAME ${test_name} COMMAND ${CMAKE_COMMAND} -E false)
            set_tests_properties(${test_name} PROPERTIES SKIP_RETURN_CODE 1)
        endfunction()
        if(EXISTS "${DESIGNSTUDIO_FREECAD_COMMAND}")
            add_test(NAME native_tool_worker_runtime
                COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_SOURCE_DIR}/tests/run_native_tool_worker_runtime.py
                    --freecadcmd ${DESIGNSTUDIO_FREECAD_COMMAND}
                    --worker ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/DesignStudio/native_tool_worker.py)
        else()
            designstudio_register_missing_runtime_test(
                native_tool_worker_runtime "FreeCADCmd/freecadcmd")
        endif()
        set_tests_properties(native_tool_worker_runtime PROPERTIES
            LABELS "freecad;worker;cancellation;transaction;occt" TIMEOUT 120
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        if(EXISTS "${DESIGNSTUDIO_FREECAD_LAUNCHER}")
            add_test(NAME vector_native_cad_runtime
                COMMAND "${DESIGNSTUDIO_FREECAD_LAUNCHER}" -c
                    "exec(open('${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/run_vector_native_cad_runtime.py').read())")
        else()
            designstudio_register_missing_runtime_test(
                vector_native_cad_runtime "DesignStudioLauncher")
        endif()
        set_tests_properties(vector_native_cad_runtime PROPERTIES
            LABELS "mechanical;freecad;cad;vector-native;occt" TIMEOUT 120
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        if(EXISTS "${DESIGNSTUDIO_FREECAD_COMMAND}")
            add_test(NAME molded_rib_runtime
                COMMAND ${DESIGNSTUDIO_FREECAD_COMMAND}
                    -c "p='${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/molded_rib_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})")
        else()
            designstudio_register_missing_runtime_test(
                molded_rib_runtime "FreeCADCmd/freecadcmd")
        endif()
        set_tests_properties(molded_rib_runtime PROPERTIES
            LABELS "mechanical;freecad;manufacturing;rib;occt"
            TIMEOUT 120 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME local_redesign_v2_contract
            COMMAND ${DESIGNSTUDIO_SCHEMA_PYTHON}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_local_redesign_v2.py)
        set_tests_properties(local_redesign_v2_contract PROPERTIES
            LABELS "mechanical;freecad;redesign;topology;schema" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        if(EXISTS "${DESIGNSTUDIO_FREECAD_LAUNCHER}")
            add_test(NAME local_redesign_v2_runtime
                COMMAND "${DESIGNSTUDIO_FREECAD_LAUNCHER}" -c
                    "exec(open('${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/run_local_redesign_v2_runtime.py').read())")
        else()
            designstudio_register_missing_runtime_test(
                local_redesign_v2_runtime "DesignStudioLauncher")
        endif()
        set_tests_properties(local_redesign_v2_runtime PROPERTIES
            LABELS "mechanical;freecad;redesign;topology;occt" TIMEOUT 180
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME authoritative_drawings_contract
            COMMAND ${DESIGNSTUDIO_SCHEMA_PYTHON}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_authoritative_drawings.py)
        set_tests_properties(authoritative_drawings_contract PROPERTIES
            LABELS "mechanical;freecad;drawings;hlr;schema" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        if(EXISTS "${DESIGNSTUDIO_FREECAD_LAUNCHER}")
            add_test(NAME authoritative_drawings_runtime
                COMMAND "${DESIGNSTUDIO_FREECAD_LAUNCHER}" -c
                    "exec(open('${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/run_authoritative_drawings_runtime.py').read())")
        else()
            designstudio_register_missing_runtime_test(
                authoritative_drawings_runtime "DesignStudioLauncher")
        endif()
        set_tests_properties(authoritative_drawings_runtime PROPERTIES
            LABELS "mechanical;freecad;drawings;hlr;occt" TIMEOUT 180
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME constraint_candidates_contract
            COMMAND ${DESIGNSTUDIO_SCHEMA_PYTHON}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_constraint_candidates.py)
        set_tests_properties(constraint_candidates_contract PROPERTIES
            LABELS "mechanical;freecad;physical-design;candidates;schema" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME camera_benchmark_contract
            COMMAND ${DESIGNSTUDIO_SCHEMA_PYTHON}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_camera_benchmark.py)
        set_tests_properties(camera_benchmark_contract PROPERTIES
            LABELS "mechanical;freecad;camera;reference;schema" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        if(EXISTS "${DESIGNSTUDIO_FREECAD_LAUNCHER}")
            add_test(NAME camera_program_runtime
                COMMAND "${DESIGNSTUDIO_FREECAD_LAUNCHER}" -c
                    "p='${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/camera_program_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})")
        else()
            designstudio_register_missing_runtime_test(
                camera_program_runtime "DesignStudioLauncher")
        endif()
        set_tests_properties(camera_program_runtime PROPERTIES
            LABELS "mechanical;freecad;camera;cad-program;occt" TIMEOUT 180
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME physical_design_flow_matrix
            COMMAND ${DESIGNSTUDIO_SCHEMA_PYTHON}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_physical_design_flow_matrix.py)
        set_tests_properties(physical_design_flow_matrix PROPERTIES
            LABELS "mechanical;freecad;physical-design;release;flow-matrix;schema" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME physical_validation_contract
            COMMAND ${DESIGNSTUDIO_SCHEMA_PYTHON}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_physical_validation.py)
        set_tests_properties(physical_validation_contract PROPERTIES
            LABELS "mechanical;freecad;physical-design;physical-validation;schema" TIMEOUT 60
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        if(EXISTS "${DESIGNSTUDIO_FREECAD_LAUNCHER}")
            add_test(NAME physical_validation_runtime
                COMMAND "${DESIGNSTUDIO_FREECAD_LAUNCHER}" -c
                    "p='${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/physical_validation_runtime.py'; exec(compile(open(p).read(), p, 'exec'), {'__file__':p, '__name__':'__main__'})")
        else()
            designstudio_register_missing_runtime_test(
                physical_validation_runtime "DesignStudioLauncher")
        endif()
        set_tests_properties(physical_validation_runtime PROPERTIES
            LABELS "mechanical;freecad;physical-design;physical-validation;occt" TIMEOUT 240
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        if(EXISTS "${DESIGNSTUDIO_FREECAD_LAUNCHER}")
            add_test(NAME constraint_candidates_runtime
                COMMAND "${DESIGNSTUDIO_FREECAD_LAUNCHER}" -c
                    "exec(open('${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/run_constraint_candidates_runtime.py').read())")
        else()
            designstudio_register_missing_runtime_test(
                constraint_candidates_runtime "DesignStudioLauncher")
        endif()
        set_tests_properties(constraint_candidates_runtime PROPERTIES
            LABELS "mechanical;freecad;physical-design;candidates;occt" TIMEOUT 180
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME semantic_assembly_contract
            COMMAND ${DESIGNSTUDIO_SCHEMA_PYTHON}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_semantic_assembly.py)
        set_tests_properties(semantic_assembly_contract PROPERTIES
            LABELS "mechanical;freecad;semantic-assembly;ap242;schema" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        if(EXISTS "${DESIGNSTUDIO_FREECAD_LAUNCHER}")
            add_test(NAME semantic_assembly_runtime
                COMMAND "${DESIGNSTUDIO_FREECAD_LAUNCHER}" -c
                    "exec(open('${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/run_semantic_assembly_runtime.py').read())")
        else()
            designstudio_register_missing_runtime_test(
                semantic_assembly_runtime "DesignStudioLauncher")
        endif()
        set_tests_properties(semantic_assembly_runtime PROPERTIES
            LABELS "mechanical;freecad;semantic-assembly;ap242;occt" TIMEOUT 120
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME car_body_study_workflow
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/tests/test_car_body_study.py)
        set_tests_properties(car_body_study_workflow PROPERTIES
            LABELS "mechanical;freecad;car;surface;reference;revision;deterministic"
            TIMEOUT 30 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
        add_test(NAME rocm_support_optimizer_contract
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/cloud/support-optimizer-amd/test_service.py)
        set_tests_properties(rocm_support_optimizer_contract PROPERTIES
            LABELS "mechanical;rocm;mi300x;support;cloud;contract" TIMEOUT 30
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})

        # Production launcher diagnostics are intentionally fail-closed when
        # the user's profile is not writable.  Give all FreeCAD runtime
        # fixtures isolated writable profile roots so a read-only CI/home
        # mount cannot turn an otherwise valid CAD test into a startup error.
        set(DESIGNSTUDIO_RUNTIME_PROFILE_ENV
            "XDG_STATE_HOME=/tmp/designstudio-ctest-state;XDG_CONFIG_HOME=/tmp/designstudio-ctest-config;XDG_CACHE_HOME=/tmp/designstudio-ctest-cache")
        foreach(runtime_test IN ITEMS
                vector_native_cad_runtime
                local_redesign_v2_runtime
                authoritative_drawings_runtime
                constraint_candidates_runtime
                physical_validation_runtime
                semantic_assembly_runtime
                molded_rib_runtime
                camera_program_runtime)
            if(TEST "${runtime_test}")
                set_tests_properties("${runtime_test}" PROPERTIES
                    ENVIRONMENT "${DESIGNSTUDIO_RUNTIME_PROFILE_ENV}")
            endif()
        endforeach()
    endif()
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Build the C++ engine first (defines its own project DesignCore)
add_subdirectory(core)

if(BUILD_TESTING AND Python3_Interpreter_FOUND AND DESIGNCORE_OCCT_ENABLED)
    add_test(NAME datasheet_component_cad_program
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_component_cad_program.py)
    set_tests_properties(datasheet_component_cad_program PROPERTIES
        LABELS "component;datasheet;smt;tht;step;occt;approval"
        TIMEOUT 120 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        ENVIRONMENT "DESIGNSTUDIO_COMPONENT_CAD=$<TARGET_FILE:designstudio-component-cad>")

    add_test(NAME manufacturer_production_package
        COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_production_cam.py
            $<TARGET_FILE:designstudio-board-step>)
    set_tests_properties(manufacturer_production_package PROPERTIES
        LABELS "pcb;production;gerber;excellon;bom;pnp;ipc;step;fabricator"
        TIMEOUT 180 WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})
endif()

# Qt moc/uic/rcc — set after core so it doesn't interfere with core targets
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)

option(DESIGNSTUDIO_BUILD_FREECAD_MODULE
    "Build the native DesignStudio GUI module against a source-built FreeCAD" OFF)
option(DESIGNSTUDIO_DEVELOPER_TOOLS
    "Build and expose the separate DesignStudio diagnostics environment" OFF)

# Build the Qt application
add_subdirectory(app/QtDesignStudio)

if(DESIGNSTUDIO_BUILD_FREECAD_MODULE)
    add_subdirectory(modules/DesignStudioGui)
endif()
