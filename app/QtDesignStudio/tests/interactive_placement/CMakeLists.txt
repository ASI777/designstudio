add_executable(interactive_placement_tests
    InteractivePlacementTests.cpp
    ../../InteractivePlacementModel.cpp
)
target_include_directories(interactive_placement_tests PRIVATE ../..)
target_link_libraries(interactive_placement_tests PRIVATE Qt6::Core Qt6::Gui)

add_test(NAME qt_interactive_placement_contract COMMAND interactive_placement_tests)
set_tests_properties(qt_interactive_placement_contract PROPERTIES
    TIMEOUT 30
    LABELS "qt;3d;step;placement;interaction;contract"
)
