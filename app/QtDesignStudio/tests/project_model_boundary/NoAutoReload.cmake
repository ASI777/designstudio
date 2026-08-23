set(main_window_cpp "${CMAKE_CURRENT_LIST_DIR}/../../MainWindow.cpp")
set(main_window_h "${CMAKE_CURRENT_LIST_DIR}/../../MainWindow.h")
file(READ "${main_window_cpp}" implementation)
file(READ "${main_window_h}" interface)
set(combined "${implementation}\n${interface}")

foreach(forbidden IN ITEMS QFileSystemWatcher onProjectFileChanged watchProjectFile)
    string(FIND "${combined}" "${forbidden}" position)
    if(NOT position EQUAL -1)
        message(FATAL_ERROR
            "Unsafe automatic project reload boundary reintroduced: ${forbidden}")
    endif()
endforeach()

message(STATUS "MainWindow has no automatic external project-file reload path")
