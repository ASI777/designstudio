add_executable(project_model_boundary_tests
    ProjectModelBoundaryTests.cpp
    ../../ProjectModel.cpp
    ../../ProjectModel.h
    ../../ApplicationConcept.cpp
    ../../ApplicationConcept.h
)
target_include_directories(project_model_boundary_tests PRIVATE ../..)
target_link_libraries(project_model_boundary_tests PRIVATE Qt6::Core)

add_test(NAME qt_project_model_boundary COMMAND project_model_boundary_tests)
add_test(NAME qt_project_boundary_no_auto_reload
    COMMAND ${CMAKE_COMMAND} -P ${CMAKE_CURRENT_SOURCE_DIR}/NoAutoReload.cmake)

set_tests_properties(
    qt_project_model_boundary
    qt_project_boundary_no_auto_reload
    PROPERTIES
        TIMEOUT 30
        LABELS "qt;project;boundary"
)
