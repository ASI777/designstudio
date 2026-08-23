cmake_minimum_required(VERSION 3.20)

set(DESIGNSTUDIO_FREECAD_SOURCE_DIR "" CACHE PATH "FreeCAD source root")
set(DESIGNSTUDIO_FREECAD_BUILD_DIR "" CACHE PATH "FreeCAD build root")
set(DESIGNSTUDIO_FREECAD_INSTALL_DIR "" CACHE PATH "FreeCAD install prefix")

foreach(required_path
        DESIGNSTUDIO_FREECAD_SOURCE_DIR
        DESIGNSTUDIO_FREECAD_BUILD_DIR
        DESIGNSTUDIO_FREECAD_INSTALL_DIR)
    if(NOT IS_DIRECTORY "${${required_path}}")
        message(FATAL_ERROR "${required_path} must identify the pinned FreeCAD build")
    endif()
endforeach()

find_package(Python3 REQUIRED COMPONENTS Development)
find_package(Qt6 REQUIRED COMPONENTS Widgets)
find_library(DESIGNSTUDIO_GUI_LIBRARY DesignStudioGuiCore REQUIRED
    HINTS "${DESIGNSTUDIO_FREECAD_BUILD_DIR}/lib"
          "${DESIGNSTUDIO_FREECAD_INSTALL_DIR}/lib")
find_library(DESIGNSTUDIO_BASE_LIBRARY DesignStudioBase REQUIRED
    HINTS "${DESIGNSTUDIO_FREECAD_BUILD_DIR}/lib"
          "${DESIGNSTUDIO_FREECAD_INSTALL_DIR}/lib")

add_library(DesignStudioGui MODULE AppDesignStudioGui.cpp)
set_target_properties(DesignStudioGui PROPERTIES
    PREFIX ""
    OUTPUT_NAME "DesignStudioGui"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/Mod/DesignStudio"
    # Keep build and installed artifacts byte-identical.  The branded host
    # supplies its own FreeCAD/dependency search path; the module only needs
    # its sibling libraries in the final prefix.
    BUILD_RPATH "$ORIGIN"
    BUILD_WITH_INSTALL_RPATH TRUE
    INSTALL_RPATH "$ORIGIN"
)
target_include_directories(DesignStudioGui PRIVATE
    "${DESIGNSTUDIO_FREECAD_SOURCE_DIR}/src"
    "${DESIGNSTUDIO_FREECAD_SOURCE_DIR}/src/3rdParty/PyCXX"
    "${DESIGNSTUDIO_FREECAD_BUILD_DIR}"
    "${DESIGNSTUDIO_FREECAD_BUILD_DIR}/src"
    "${DESIGNSTUDIO_FREECAD_BUILD_DIR}/_deps/fmt-src/include"
)
target_link_libraries(DesignStudioGui PRIVATE
    designstudio_ui
    "${DESIGNSTUDIO_GUI_LIBRARY}"
    "${DESIGNSTUDIO_BASE_LIBRARY}"
    Python3::Python
    Qt6::Widgets
)

install(TARGETS DesignStudioGui LIBRARY DESTINATION lib)
install(FILES
    "${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/Init.py"
    "${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/InitGui.py"
    DESTINATION Mod/DesignStudio)
install(DIRECTORY
    "${CMAKE_SOURCE_DIR}/freecad/DesignStudioWorkbench/DesignStudio"
    DESTINATION Mod/DesignStudio
    PATTERN "__pycache__" EXCLUDE)
install(FILES
    "${CMAKE_SOURCE_DIR}/third_party/freecad/branding.xml"
    DESTINATION bin)
install(PROGRAMS
    "${CMAKE_SOURCE_DIR}/scripts/designstudio-launcher"
    DESTINATION bin
    RENAME DesignStudioLauncher)
install(PROGRAMS
    "${CMAKE_SOURCE_DIR}/scripts/designstudio-live-session"
    DESTINATION bin)
install(FILES
    "${CMAKE_SOURCE_DIR}/app/QtDesignStudio/resources/designstudio.svg"
    DESTINATION share/icons/hicolor/scalable/apps)
set(DESIGNSTUDIO_EXECUTABLE "${CMAKE_INSTALL_PREFIX}/bin/DesignStudioLauncher")
set(DESIGNSTUDIO_ICON
    "${CMAKE_INSTALL_PREFIX}/share/icons/hicolor/scalable/apps/designstudio.svg")
configure_file(
    "${CMAKE_SOURCE_DIR}/scripts/DesignStudio.desktop"
    "${CMAKE_CURRENT_BINARY_DIR}/DesignStudio.desktop"
    @ONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/DesignStudio.desktop"
    DESTINATION share/applications
    RENAME designstudio.desktop)
install(PROGRAMS "${CMAKE_SOURCE_DIR}/scripts/install-desktop-shortcut.sh"
    DESTINATION bin)

if(BUILD_TESTING)
    # These are production-host tests, not the transitional Qt harness.  Keep
    # them runnable after an existing local install while still registering a
    # deterministic failure when the requested branded prefix is absent.
    set(DESIGNSTUDIO_NATIVE_SMOKE_SCRIPT
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/run-native-smoke.sh")
    set(DESIGNSTUDIO_WAYLAND_SMOKE_SCRIPT
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/run-wayland-startup.sh")
    set(DESIGNSTUDIO_DESKTOP_ENTRY_SCRIPT
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/run-desktop-entry-test.sh")
    set(DESIGNSTUDIO_PROFILE_STARTUP_SCRIPT
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/run-profile-startup-test.sh")
    set(DESIGNSTUDIO_ACTIVATION_CYCLE_SCRIPT
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/run-activation-cycle.sh")
    if(EXISTS "${DESIGNSTUDIO_FREECAD_INSTALL_DIR}/bin/DesignStudioLauncher")
        add_test(NAME designstudio_native_desktop_startup
            COMMAND bash "${DESIGNSTUDIO_NATIVE_SMOKE_SCRIPT}"
                "${DESIGNSTUDIO_FREECAD_INSTALL_DIR}")
        add_test(NAME designstudio_desktop_entry
            COMMAND bash "${DESIGNSTUDIO_DESKTOP_ENTRY_SCRIPT}"
                "${DESIGNSTUDIO_FREECAD_INSTALL_DIR}")
        add_test(NAME designstudio_profile_startup
            COMMAND bash "${DESIGNSTUDIO_PROFILE_STARTUP_SCRIPT}"
                "${DESIGNSTUDIO_FREECAD_INSTALL_DIR}")
        add_test(NAME designstudio_wayland_startup
            COMMAND bash "${DESIGNSTUDIO_WAYLAND_SMOKE_SCRIPT}"
                "${DESIGNSTUDIO_FREECAD_INSTALL_DIR}")
        add_test(NAME designstudio_activation_cycle
            COMMAND bash "${DESIGNSTUDIO_ACTIVATION_CYCLE_SCRIPT}"
                "${DESIGNSTUDIO_FREECAD_INSTALL_DIR}")
    else()
        foreach(startup_test IN ITEMS
                designstudio_native_desktop_startup
                designstudio_desktop_entry
                designstudio_profile_startup
                designstudio_wayland_startup
                designstudio_activation_cycle)
            add_test(NAME "${startup_test}" COMMAND ${CMAKE_COMMAND} -E false)
        endforeach()
    endif()
    set_tests_properties(
        designstudio_native_desktop_startup
        designstudio_desktop_entry
        designstudio_profile_startup
        designstudio_wayland_startup
        designstudio_activation_cycle
        PROPERTIES LABELS "desktop;launcher;freecad;startup" TIMEOUT 120)
    set_tests_properties(designstudio_native_desktop_startup
        PROPERTIES ENVIRONMENT
        "DESIGNSTUDIO_ALLOW_OFFSCREEN_FALLBACK=1;QT_QPA_PLATFORM=offscreen;DESIGNSTUDIO_XVFB_ACTIVE=1")
    set_tests_properties(designstudio_desktop_entry PROPERTIES
        ENVIRONMENT "DESIGNSTUDIO_ALLOW_OFFSCREEN_FALLBACK=1;QT_QPA_PLATFORM=offscreen")
    set_tests_properties(designstudio_activation_cycle PROPERTIES
        ENVIRONMENT "DESIGNSTUDIO_ALLOW_OFFSCREEN_FALLBACK=1;QT_QPA_PLATFORM=offscreen")
endif()
