add_executable(glb_mesh_reader_tests GlbMeshReaderTests.cpp)
target_link_libraries(glb_mesh_reader_tests PRIVATE
    Qt6::Core
    Qt6::Gui
    designstudio_glb_mesh
)

add_test(NAME glb_mesh_positive COMMAND glb_mesh_reader_tests positive)
add_test(NAME glb_mesh_transforms COMMAND glb_mesh_reader_tests transforms)
add_test(NAME glb_mesh_negative COMMAND glb_mesh_reader_tests negative)
set_tests_properties(glb_mesh_positive glb_mesh_transforms glb_mesh_negative PROPERTIES
    TIMEOUT 30
    LABELS "qt;glb;enclosure"
)

add_test(NAME qt_enclosure_glb_connection
    COMMAND $<TARGET_FILE:DesignStudioQt>
        --smoke-test
        --enclosure-glb ${CMAKE_CURRENT_BINARY_DIR}/triangle.glb)
set_tests_properties(qt_enclosure_glb_connection PROPERTIES
    TIMEOUT 30
    LABELS "qt;glb;enclosure;smoke"
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen;DS_AI_MOCK=1"
    PASS_REGULAR_EXPRESSION "ENCLOSURE_GLB_CONNECTED"
)

add_test(NAME qt_enclosure_evidence_export
    COMMAND $<TARGET_FILE:DesignStudioQt>
        --smoke-test
        --enclosure-glb ${CMAKE_CURRENT_BINARY_DIR}/triangle.glb
        --mesh-evidence-out ${CMAKE_CURRENT_BINARY_DIR}/triangle.mesh-evidence.json)
set_tests_properties(qt_enclosure_evidence_export PROPERTIES
    TIMEOUT 30
    LABELS "qt;glb;enclosure;contract"
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen;DS_AI_MOCK=1"
    PASS_REGULAR_EXPRESSION "MESH_EVIDENCE_EXPORTED"
)

# The parser test creates the same deterministic fixture when it runs; generate
# one at configure time for the application connection test without checking a
# binary artifact into source control.
add_custom_command(TARGET glb_mesh_reader_tests POST_BUILD
    COMMAND glb_mesh_reader_tests write-fixture ${CMAKE_CURRENT_BINARY_DIR}/triangle.glb
    BYPRODUCTS ${CMAKE_CURRENT_BINARY_DIR}/triangle.glb
    VERBATIM
)
add_dependencies(DesignStudioQt glb_mesh_reader_tests)
