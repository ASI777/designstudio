add_executable(kicad_symbol_library_tests
    KicadSymbolLibraryTests.cpp
    ../../KicadSymbolLibrary.cpp
    ../../KicadSymbolLibrary.h
)
target_include_directories(kicad_symbol_library_tests PRIVATE ../..)
target_link_libraries(kicad_symbol_library_tests PRIVATE Qt6::Core Qt6::Gui)

add_test(NAME qt_kicad_symbol_library COMMAND kicad_symbol_library_tests)
set_tests_properties(qt_kicad_symbol_library PROPERTIES
    TIMEOUT 30
    LABELS "qt;schematic;kicad;interchange"
)
