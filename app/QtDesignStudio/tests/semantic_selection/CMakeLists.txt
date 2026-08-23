add_executable(semantic_selection_tests
    SemanticSelectionTests.cpp
    ../../SemanticSelectionModel.cpp
    ../../SemanticSelectionModel.h
)
target_include_directories(semantic_selection_tests PRIVATE ../..)
target_link_libraries(semantic_selection_tests PRIVATE Qt6::Core)
add_test(NAME qt_semantic_selection COMMAND semantic_selection_tests)
set_tests_properties(qt_semantic_selection PROPERTIES
    TIMEOUT 30 LABELS "qt;semantic-assembly;selection")
