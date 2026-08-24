cmake_minimum_required(VERSION 3.20)

include(GNUInstallDirs)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets OpenGLWidgets Concurrent Network)
find_package(Qt6 QUIET OPTIONAL_COMPONENTS Svg SvgWidgets)   # icons + vector SVG import when present

# Qt's offscreen plugin cannot provide a QOpenGLWidget context on every
# supported Qt release. Keep it as the developer default, while allowing CI
# to exercise the real renderer through xcb on an Xvfb virtual display.
set(DESIGNSTUDIO_TEST_QPA_PLATFORM "offscreen" CACHE STRING
    "Qt platform plugin used by DesignStudio GUI tests")

# Trust-boundary reader for CAD-assist evidence.  It intentionally has no
# ProjectModel dependency: validation produces an immutable session snapshot
# and cannot mutate a .dsproj document.
add_library(designstudio_cad_assist STATIC
    CadAssistContract.cpp
    CadAssistContract.h
)
set_target_properties(designstudio_cad_assist PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(designstudio_cad_assist PUBLIC Qt6::Core)
target_include_directories(designstudio_cad_assist PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
if(MSVC)
    target_compile_options(designstudio_cad_assist PRIVATE /W4 /permissive-)
else()
    target_compile_options(designstudio_cad_assist PRIVATE -Wall -Wextra -Wpedantic)
endif()

# Defensive, dependency-light GLB reader shared by the enclosure workspace and
# parser tests. It flattens the selected glTF scene into immutable millimetre
# geometry; no renderer or project mutation is involved at this boundary.
add_library(designstudio_glb_mesh STATIC
    GlbMeshReader.cpp
    GlbMeshReader.h
    SemanticAssembly.cpp
    SemanticAssembly.h
)
set_target_properties(designstudio_glb_mesh PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(designstudio_glb_mesh PUBLIC Qt6::Core Qt6::Gui)
target_include_directories(designstudio_glb_mesh PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
if(MSVC)
    target_compile_options(designstudio_glb_mesh PRIVATE /W4 /permissive-)
else()
    target_compile_options(designstudio_glb_mesh PRIVATE -Wall -Wextra -Wpedantic)
endif()

set(DESIGNSTUDIO_UI_SOURCES
    MainWindow.cpp
    MainWindow.h
    ApplicationConcept.cpp
    ApplicationConcept.h
    ProductHomePage.cpp
    ProductHomePage.h
    DesignFlowPanel.cpp
    DesignFlowPanel.h
    BuildMapPanel.cpp
    BuildMapPanel.h
    AgentdClient.cpp
    AgentdClient.h
    CodexAppServerClient.cpp
    CodexAppServerClient.h
    ProductConfiguratorPanel.cpp
    ProductConfiguratorPanel.h
    PhysicalDesignPanel.cpp
    PhysicalDesignPanel.h
    PropagationPanel.cpp
    PropagationPanel.h
    CadAssistPanel.cpp
    CadAssistPanel.h
    CadAssistProjectBinding.cpp
    CadAssistProjectBinding.h
    EnclosureConceptTab.cpp
    EnclosureConceptTab.h
    InteractivePlacementModel.cpp
    InteractivePlacementModel.h
    SupportJobClient.cpp
    SupportJobClient.h
    MeshProjectionView.cpp
    MeshProjectionView.h
    SemanticSelectionModel.cpp
    SemanticSelectionModel.h
    SemanticAssemblyPanel.cpp
    SemanticAssemblyPanel.h
    StepMeshReader.cpp
    StepMeshReader.h
    Pcb3DView.cpp
    Pcb3DView.h
    PcbCanvas.cpp
    PcbCanvas.h
    SchematicView.cpp
    SchematicView.h
    KicadSymbolLibrary.cpp
    KicadSymbolLibrary.h
    ProjectModel.cpp
    ProjectModel.h
    DimensionSolver.cpp
    DimensionSolver.h
    DimSolverCore.h
    DatasheetExtractor.cpp
    DatasheetExtractor.h
    CoreBridge.cpp
    CoreBridge.h
    DrcPanel.cpp
    DrcPanel.h
    LayerPanel.cpp
    LayerPanel.h
    ComponentPanel.cpp
    ComponentPanel.h
    FootprintLibTab.cpp
    FootprintLibTab.h
    InkscapeHost.cpp
    InkscapeHost.h
    resources/footprintlib.qrc
    DesignChatPanel.cpp
    DesignChatPanel.h
    AgentActivityPanel.cpp
    AgentActivityPanel.h
    CredentialsDialog.cpp
    CredentialsDialog.h
)

add_library(designstudio_ui SHARED ${DESIGNSTUDIO_UI_SOURCES})
set_target_properties(designstudio_ui PROPERTIES
    POSITION_INDEPENDENT_CODE ON
    OUTPUT_NAME designstudio_ui
    INSTALL_RPATH "$ORIGIN"
)

target_link_libraries(designstudio_ui PUBLIC
    Qt6::Core
    Qt6::Gui
    Qt6::Widgets
    Qt6::OpenGLWidgets
    Qt6::Concurrent
    Qt6::Network
    designcore
    designstudio_cad_assist
    designstudio_glb_mesh
)

target_include_directories(designstudio_ui PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_include_directories(designstudio_ui PRIVATE
    ${CMAKE_SOURCE_DIR}/core/include
)

# Qt SVG (optional): SVG tool icons + footprint SVG import.
#   • Qt6::SvgWidgets → vector import via QGraphicsSvgItem (crisp at any zoom)
#   • else            → raster import via QImageReader + the qsvg image plugin
if(TARGET Qt6::SvgWidgets)
    message(STATUS "DesignStudioQt: Qt6::SvgWidgets found — VECTOR SVG import enabled")
    target_link_libraries(designstudio_ui PRIVATE Qt6::Svg Qt6::SvgWidgets)
    target_compile_definitions(designstudio_ui PRIVATE HAVE_QTSVGWIDGETS)
elseif(TARGET Qt6::Svg)
    message(STATUS "DesignStudioQt: Qt6::Svg found (no SvgWidgets) — raster SVG import")
    target_link_libraries(designstudio_ui PRIVATE Qt6::Svg)
else()
    message(STATUS "DesignStudioQt: Qt6::Svg dev absent — raster SVG import via qsvg plugin")
endif()

# Optional lib2geom (LGPL) — Inkscape's geometry kernel. When present, the
# Footprint Lib tab uses it for boolean ops / path offsetting; otherwise it
# falls back to Qt-native QPainterPath. Build stays green either way.
find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(LIB2GEOM QUIET 2geom)
endif()
if(LIB2GEOM_FOUND)
    message(STATUS "DesignStudioQt: lib2geom found — enabling HAVE_LIB2GEOM")
    target_compile_definitions(designstudio_ui PRIVATE HAVE_LIB2GEOM)
    target_include_directories(designstudio_ui PRIVATE ${LIB2GEOM_INCLUDE_DIRS})
    target_link_libraries(designstudio_ui PRIVATE ${LIB2GEOM_LIBRARIES})
else()
    message(STATUS "DesignStudioQt: lib2geom not found — using Qt-native geometry")
endif()

# Qt 6.10 changed addAction() overload order — suppress that deprecation noise
target_compile_options(designstudio_ui PRIVATE -Wno-deprecated-declarations)
if(DESIGNSTUDIO_DEVELOPER_TOOLS)
    target_compile_definitions(designstudio_ui PRIVATE DESIGNSTUDIO_DEVELOPER_TOOLS=1)
endif()

qt_add_executable(DesignStudioQt main.cpp)
target_link_libraries(DesignStudioQt PRIVATE designstudio_ui)

set_target_properties(DesignStudioQt PROPERTIES
    WIN32_EXECUTABLE TRUE
    MACOSX_BUNDLE TRUE
    # This executable is a transitional Qt integration harness.  The
    # production `bin/DesignStudio` name is reserved for the launcher that
    # hosts the product inside FreeCAD.
    OUTPUT_NAME "designstudio-qt-harness"
    INSTALL_RPATH "$ORIGIN/../${CMAKE_INSTALL_LIBDIR}"
)
if(DESIGNSTUDIO_DEVELOPER_TOOLS)
    set_target_properties(DesignStudioQt PROPERTIES
        OUTPUT_NAME "designstudio-diagnostics")
endif()

# Copy stylesheet to build dir
configure_file(resources/dark.qss ${CMAKE_CURRENT_BINARY_DIR}/dark.qss COPYONLY)

install(TARGETS designstudio_ui LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR})
# Keep the transitional client available for diagnostics and migration, but it
# must never masquerade as the supported FreeCAD-hosted product.
install(TARGETS DesignStudioQt
    BUNDLE DESTINATION .
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS ${CMAKE_SOURCE_DIR}/scripts/designstudio-launcher
    DESTINATION ${CMAKE_INSTALL_BINDIR}
    RENAME DesignStudioLauncher)

install(FILES ${CMAKE_SOURCE_DIR}/docs/schemas/cad-assist-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/mesh-evidence-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/reference-image-set-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/visual-evidence-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/pcb-rules-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/pcb-placement-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/pcb-routing-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/verification-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/datasheet-evidence-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/procurement-quote-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/component-selection-plan-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/component-candidate-set-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/component-decision-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/pcb-topology-study-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/pcb-island-plan-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/mechanical-component-requirements-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/release-manifest-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/release-gate-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/mechanical-contract-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/mechanical-collision-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/bound-component-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/incremental-analysis-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/workspace-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/product-graph-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/configuration-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/evidence-record-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/generation-job-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/generation-artifacts-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/product-intent-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/work-package-graph-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/workflow-run-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/interaction-surface-map-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/propagation-run-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/propagation-run-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/proposal-branch-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/integration-sandbox-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/agent-task-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/update-manifest-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/datasheet-inspection-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/component-cad-program-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/interactive-component-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/component-placement-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/placement-evidence-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/support-generation-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/support-result-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/reference-form-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/reference-form-result-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/physical-design-session-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/physical-design-session-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/physical-test-plan-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/physical-test-observation-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/physical-candidate-decision-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/semantic-assembly-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/local-redesign-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/local-redesign-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/local-redesign-discard-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/authoritative-drawing-package-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/mechanical-cad-program-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/surface-design-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/propagation-run-v2.schema.json
    DESTINATION ${CMAKE_INSTALL_DATADIR}/DesignStudio/schemas
)
install(DIRECTORY ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench
    DESTINATION ${CMAKE_INSTALL_DATADIR}/DesignStudio/freecad
    PATTERN "__pycache__" EXCLUDE)
# FreeCAD resolves workbenches from <prefix>/Mod at runtime.  Keep the active
# DesignStudio workbench synchronized with the audited share copy above so a
# local install cannot silently continue loading stale Python commands.
install(DIRECTORY ${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/
    DESTINATION Mod/DesignStudio
    PATTERN "__pycache__" EXCLUDE)
install(DIRECTORY ${CMAKE_SOURCE_DIR}/swarm/release ${CMAKE_SOURCE_DIR}/swarm/vendors
    DESTINATION ${CMAKE_INSTALL_DATADIR}/DesignStudio/python/swarm
    PATTERN "__pycache__" EXCLUDE)
install(DIRECTORY ${CMAKE_SOURCE_DIR}/swarm/memory
    DESTINATION ${CMAKE_INSTALL_DATADIR}/DesignStudio/python/swarm
    PATTERN "__pycache__" EXCLUDE)
install(DIRECTORY ${CMAKE_SOURCE_DIR}/swarm/agents
    DESTINATION ${CMAKE_INSTALL_DATADIR}/DesignStudio/python/swarm
    PATTERN "__pycache__" EXCLUDE)
install(DIRECTORY ${CMAKE_SOURCE_DIR}/docs/datasheet-extractor
    DESTINATION ${CMAKE_INSTALL_DATADIR}/DesignStudio/python/docs
    PATTERN "__pycache__" EXCLUDE)
install(FILES ${CMAKE_SOURCE_DIR}/docs/schemas/datasheet-inspection-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/reference-image-set-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/visual-evidence-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/component-cad-program-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/component-selection-plan-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/component-candidate-set-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/component-decision-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/pcb-topology-study-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/pcb-island-plan-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/mechanical-component-requirements-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/interactive-component-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/component-placement-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/placement-evidence-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/support-generation-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/support-result-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/reference-form-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/reference-form-result-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/physical-design-session-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/physical-design-session-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/physical-test-plan-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/physical-test-observation-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/physical-candidate-decision-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/semantic-assembly-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/local-redesign-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/local-redesign-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/authoritative-drawing-package-v1.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/mechanical-cad-program-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/surface-design-v2.schema.json
              ${CMAKE_SOURCE_DIR}/docs/schemas/propagation-run-v2.schema.json
    DESTINATION ${CMAKE_INSTALL_DATADIR}/DesignStudio/python/docs/schemas)
install(DIRECTORY ${CMAKE_SOURCE_DIR}/tools/layout_extractor
    DESTINATION ${CMAKE_INSTALL_DATADIR}/DesignStudio/python/tools
    PATTERN "__pycache__" EXCLUDE)
install(PROGRAMS ${CMAKE_SOURCE_DIR}/tools/extract_component_datasheet.py
                 ${CMAKE_SOURCE_DIR}/tools/publish_component_assets.py
                 ${CMAKE_SOURCE_DIR}/tools/reference_image_set.py
    DESTINATION ${CMAKE_INSTALL_DATADIR}/DesignStudio/python/tools)
install(FILES ${CMAKE_SOURCE_DIR}/swarm/runtime_paths.py
    DESTINATION ${CMAKE_INSTALL_DATADIR}/DesignStudio/python/swarm)
install(PROGRAMS ${CMAKE_SOURCE_DIR}/swarm/release/designstudio-release-gate
    DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS ${CMAKE_SOURCE_DIR}/swarm/release/sign_release.py
    DESTINATION ${CMAKE_INSTALL_BINDIR}
    RENAME designstudio-sign-release)
install(PROGRAMS ${CMAKE_SOURCE_DIR}/swarm/release/update_installer.py
    DESTINATION ${CMAKE_INSTALL_BINDIR}
    RENAME designstudio-update)
install(PROGRAMS ${CMAKE_SOURCE_DIR}/tools/bind_component.py
    DESTINATION ${CMAKE_INSTALL_BINDIR}
    RENAME designstudio-bind-component)
install(PROGRAMS ${CMAKE_SOURCE_DIR}/tools/publish_component_assets.py
    DESTINATION ${CMAKE_INSTALL_BINDIR}
    RENAME designstudio-publish-component)

if(BUILD_TESTING)
    set(DESIGNSTUDIO_AGENTD_TEST_EXECUTABLE
        "${CMAKE_SOURCE_DIR}/services/designstudio-agentd/target/release/designstudio-agentd"
        CACHE FILEPATH "designstudio-agentd executable used by Qt integration tests")
    add_test(NAME qt_app_smoke COMMAND DesignStudioQt --smoke-test)
    set_tests_properties(qt_app_smoke PROPERTIES
        ENVIRONMENT "QT_QPA_PLATFORM=${DESIGNSTUDIO_TEST_QPA_PLATFORM};DS_AI_MOCK=1"
        TIMEOUT 30
        LABELS "qt;smoke"
    )
    add_test(NAME qt_product_ui_contract COMMAND DesignStudioQt --ui-contract-test)
    set_tests_properties(qt_product_ui_contract PROPERTIES
        ENVIRONMENT "QT_QPA_PLATFORM=${DESIGNSTUDIO_TEST_QPA_PLATFORM};DS_AI_MOCK=1"
        TIMEOUT 30 LABELS "qt;ui;product;workspace"
        PASS_REGULAR_EXPRESSION "UI_CONTRACT_OK")
    add_test(NAME qt_physical_design_workflow COMMAND DesignStudioQt --physical-design-workflow-test)
    set_tests_properties(qt_physical_design_workflow PROPERTIES
        ENVIRONMENT "QT_QPA_PLATFORM=${DESIGNSTUDIO_TEST_QPA_PLATFORM};DS_AI_MOCK=1"
        TIMEOUT 30 LABELS "qt;ui;physical-design;physical-validation"
        PASS_REGULAR_EXPRESSION "PHYSICAL_DESIGN_WORKFLOW_OK")
    add_test(NAME qt_industrial_design_flow COMMAND DesignStudioQt --industrial-design-flow-test)
    set_tests_properties(qt_industrial_design_flow PROPERTIES
        ENVIRONMENT "QT_QPA_PLATFORM=${DESIGNSTUDIO_TEST_QPA_PLATFORM};DS_AI_MOCK=1"
        TIMEOUT 30 LABELS "qt;ui;industrial-design;workflow;approval"
        PASS_REGULAR_EXPRESSION "INDUSTRIAL_DESIGN_FLOW_OK")
    add_test(NAME qt_freecad_electronics_smoke
        COMMAND DesignStudioQt --electronics-only --smoke-test)
    set_tests_properties(qt_freecad_electronics_smoke PROPERTIES
        ENVIRONMENT "QT_QPA_PLATFORM=${DESIGNSTUDIO_TEST_QPA_PLATFORM};DS_AI_MOCK=1"
        TIMEOUT 30
        LABELS "qt;freecad;electronics;smoke"
        PASS_REGULAR_EXPRESSION "FREECAD_ELECTRONICS_PROFILE"
    )
    if(Python3_Interpreter_FOUND)
        add_test(NAME product_propagation_control_plane
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/services/designstudio-agentd/test_propagation_protocol.py
                ${DESIGNSTUDIO_AGENTD_TEST_EXECUTABLE})
        set_tests_properties(product_propagation_control_plane PROPERTIES
            TIMEOUT 30 LABELS "agentd;product-graph;analysis;propagation"
            PASS_REGULAR_EXPRESSION "DESIGNSTUDIO_PROPAGATION_PROTOCOL_OK")
        add_test(NAME bound_component_contract
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_component_binding.py)
        set_tests_properties(bound_component_contract PROPERTIES
            TIMEOUT 30 LABELS "component;datasheet;step;freecad;contract")
        add_test(NAME incremental_electrical_analysis
            COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/swarm/tests/test_incremental_design.py)
        set_tests_properties(incremental_electrical_analysis PROPERTIES
            TIMEOUT 30 LABELS "schematic;erc;dc;power;stability;spice;incremental")
        add_test(NAME qt_unified_verification_report
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/verification/VerificationReportTest.py
                $<TARGET_FILE:DesignStudioQt>
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/verification/verification-pass.dsproj)
        set_tests_properties(qt_unified_verification_report PROPERTIES
            TIMEOUT 30 LABELS "qt;pcb;verification;release")
        add_test(NAME industrial_release_gate
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_SOURCE_DIR}/swarm/tests/test_release_gate.py
                $<TARGET_FILE:DesignStudioQt>
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/verification/verification-pass.dsproj)
        set_tests_properties(industrial_release_gate PROPERTIES
            TIMEOUT 30 LABELS "pcb;verification;release;security;handoff")
        add_test(NAME installed_layout_native_engine
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/verification/InstalledLayoutTest.py
                $<TARGET_FILE:DesignStudioQt>
                $<TARGET_FILE:designcore>
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/verification/verification-pass.dsproj)
        set_tests_properties(installed_layout_native_engine PROPERTIES
            TIMEOUT 30 LABELS "install;qt;core;verification;release")
    endif()

    if(DESIGNCORE_OCCT_ENABLED)
        add_test(NAME qt_balancing_sphere_generation
            COMMAND DesignStudioQt --smoke-test
                --generate-balancing-sphere
                ${CMAKE_CURRENT_BINARY_DIR}/generated_balancing_sphere)
        set_tests_properties(qt_balancing_sphere_generation PROPERTIES
            ENVIRONMENT "QT_QPA_PLATFORM=${DESIGNSTUDIO_TEST_QPA_PLATFORM};DS_AI_MOCK=1"
            TIMEOUT 120
            LABELS "qt;occt;step;mechatronics;control;smoke"
            PASS_REGULAR_EXPRESSION "BALANCING_SPHERE_GENERATED"
        )
    endif()

    add_subdirectory(tests/cad_assist_contract)
    add_subdirectory(tests/project_model_boundary)
    add_subdirectory(tests/glb_mesh)
    add_subdirectory(tests/kicad_symbol_library)
    add_subdirectory(tests/semantic_selection)
    if(DESIGNCORE_OCCT_ENABLED)
        add_subdirectory(tests/step_mesh)
    endif()
    add_subdirectory(tests/agentd_client)
    add_subdirectory(tests/interactive_placement)
endif()
