cmake_minimum_required(VERSION 3.20)
project(step_to_stl LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(step_to_stl step_to_stl.cpp)

# Some distro OpenCASCADEConfig packages unconditionally import optional VTK
# targets. This utility needs only data exchange/modeling, so resolve those
# libraries directly and stay independent of the visualization stack.
find_path(OCCT_INCLUDE_DIR STEPControl_Reader.hxx PATH_SUFFIXES opencascade REQUIRED)
set(OCCT_COMPONENTS
    TKDESTEP TKXSBase TKDESTL TKShHealing TKMesh TKBRep TKTopAlgo TKGeomAlgo
    TKGeomBase TKG3d TKG2d TKMath TKernel
)
foreach(component IN LISTS OCCT_COMPONENTS)
    find_library(${component}_LIBRARY NAMES ${component} REQUIRED)
    list(APPEND OCCT_LIBRARIES ${${component}_LIBRARY})
endforeach()

target_include_directories(step_to_stl PRIVATE ${OCCT_INCLUDE_DIR})
target_link_libraries(step_to_stl PRIVATE ${OCCT_LIBRARIES})
