add_executable(step_mesh_reader_tests
    StepMeshReaderTests.cpp
    ../../StepMeshReader.cpp
    ../../StepMeshReader.h
)
target_include_directories(step_mesh_reader_tests PRIVATE
    ../..
    ${CMAKE_SOURCE_DIR}/core/include
)
target_link_libraries(step_mesh_reader_tests PRIVATE
    Qt6::Core
    Qt6::Gui
    designcore
    designstudio_glb_mesh
)

set(step_commands "${CMAKE_CURRENT_BINARY_DIR}/preview.commands")
set(step_fixture "${CMAKE_CURRENT_BINARY_DIR}/preview.step")
file(GENERATE OUTPUT "${step_commands}"
    CONTENT "box board 0 0 0 20 10 1.6 0 0 0\n")
add_custom_command(
    OUTPUT "${step_fixture}"
    COMMAND $<TARGET_FILE:designstudio-component-cad> "${step_commands}" "${step_fixture}"
    DEPENDS designstudio-component-cad "${step_commands}"
    VERBATIM
)
add_custom_target(step_mesh_fixture ALL DEPENDS "${step_fixture}")
add_dependencies(step_mesh_reader_tests step_mesh_fixture)

set(assembly_commands "${CMAKE_CURRENT_BINARY_DIR}/ap242-assembly.commands")
set(assembly_fixture "${CMAKE_CURRENT_BINARY_DIR}/ap242-assembly.step")
file(GENERATE OUTPUT "${assembly_commands}"
    CONTENT "box U1 -8 0 0 4 4 2 0 0 0\nbox J1 8 0 0 5 3 1.5 0 0 15\n")
add_custom_command(
    OUTPUT "${assembly_fixture}"
    COMMAND $<TARGET_FILE:designstudio-component-cad> "${assembly_commands}" "${assembly_fixture}"
    DEPENDS designstudio-component-cad "${assembly_commands}"
    VERBATIM
)
add_custom_target(semantic_assembly_fixture ALL DEPENDS "${assembly_fixture}")

add_test(NAME qt_step_mesh_reader COMMAND step_mesh_reader_tests "${step_fixture}")
set_tests_properties(qt_step_mesh_reader PROPERTIES
    TIMEOUT 30
    LABELS "qt;pcb;3d;step;occt"
)

add_executable(semantic_assembly_tests
    SemanticAssemblyTests.cpp
    ../../SemanticAssembly.cpp
    ../../SemanticAssembly.h
)
target_include_directories(semantic_assembly_tests PRIVATE ../..)
target_link_libraries(semantic_assembly_tests PRIVATE Qt6::Core Qt6::Gui)
add_test(NAME qt_semantic_assembly_contract COMMAND semantic_assembly_tests)
set_tests_properties(qt_semantic_assembly_contract PROPERTIES
    TIMEOUT 30 LABELS "qt;pcb;3d;step;ap242;semantic-assembly")

add_executable(semantic_assembly_runtime_tests
    SemanticAssemblyRuntimeTests.cpp
    ../../MeshProjectionView.cpp
    ../../MeshProjectionView.h
    ../../StepMeshReader.cpp
    ../../StepMeshReader.h
)
target_include_directories(semantic_assembly_runtime_tests PRIVATE
    ../..
    ${CMAKE_SOURCE_DIR}/core/include
)
target_link_libraries(semantic_assembly_runtime_tests PRIVATE
    Qt6::Core
    Qt6::Gui
    Qt6::Widgets
    Qt6::OpenGL
    Qt6::OpenGLWidgets
    designcore
    designstudio_glb_mesh
)
add_dependencies(semantic_assembly_runtime_tests semantic_assembly_fixture)
add_test(NAME qt_semantic_assembly_runtime
    COMMAND semantic_assembly_runtime_tests "${assembly_fixture}")
set_tests_properties(qt_semantic_assembly_runtime PROPERTIES
    TIMEOUT 60 ENVIRONMENT "QT_QPA_PLATFORM=offscreen"
    LABELS "qt;pcb;3d;step;ap242;semantic-assembly;cache")

add_executable(mesh_projection_controls_tests
    MeshProjectionControlsTests.cpp
    ../../MeshProjectionView.cpp
    ../../MeshProjectionView.h
    ../../StepMeshReader.cpp
    ../../StepMeshReader.h
)
target_include_directories(mesh_projection_controls_tests PRIVATE
    ../..
    ${CMAKE_SOURCE_DIR}/core/include
)
target_link_libraries(mesh_projection_controls_tests PRIVATE
    Qt6::Core
    Qt6::Gui
    Qt6::Widgets
    Qt6::OpenGL
    Qt6::OpenGLWidgets
    designcore
    designstudio_glb_mesh
)
add_test(NAME qt_mesh_projection_controls COMMAND mesh_projection_controls_tests)
set_tests_properties(qt_mesh_projection_controls PROPERTIES
    TIMEOUT 30
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen"
    LABELS "qt;pcb;3d;mouse;camera"
)
