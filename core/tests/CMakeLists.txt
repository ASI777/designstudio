add_executable(core_tests test_core.cpp)
target_link_libraries(core_tests PRIVATE designcore)
add_test(NAME core_tests COMMAND core_tests)

add_executable(balancing_sphere_tests test_balancing_sphere.cpp)
target_link_libraries(balancing_sphere_tests PRIVATE designcore)
add_test(NAME balancing_sphere_tests
    COMMAND balancing_sphere_tests ${CMAKE_CURRENT_BINARY_DIR}/generated_balancing_sphere)
set_tests_properties(balancing_sphere_tests PROPERTIES
    TIMEOUT 120
    LABELS "core;occt;step;mechatronics;control"
)

add_executable(mechanical_engine_tests test_mechanical.cpp)
target_link_libraries(mechanical_engine_tests PRIVATE designcore)
add_test(NAME mechanical_engine_tests COMMAND mechanical_engine_tests)
set_tests_properties(mechanical_engine_tests PROPERTIES
    LABELS "core;mechanical;solver;deterministic"
    TIMEOUT 30)

# Windows: the test exe links designcore.dll but lives in a different output
# directory (build/tests/<cfg>/ vs build/<cfg>/), and Windows has no RPATH —
# copy the DLL next to the exe after every build so ctest can launch it.
if (WIN32)
    add_custom_command(TARGET core_tests POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                $<TARGET_FILE:designcore>
                $<TARGET_FILE_DIR:core_tests>)
    add_custom_command(TARGET balancing_sphere_tests POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                $<TARGET_FILE:designcore>
                $<TARGET_FILE_DIR:balancing_sphere_tests>)
    add_custom_command(TARGET mechanical_engine_tests POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                $<TARGET_FILE:designcore>
                $<TARGET_FILE_DIR:mechanical_engine_tests>)
endif()
