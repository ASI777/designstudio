cmake_minimum_required(VERSION 3.20)
project(DesignCore LANGUAGES CXX VERSION 0.1.0)

include(GNUInstallDirs)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Core engine: compiled as a shared library consumed by the C# app via P/Invoke.
add_library(designcore SHARED
    src/geometry.cpp
    src/pcb_model.cpp
    src/obstacle_index.cpp
    src/physics.cpp
    src/linalg.cpp
    src/mechanical.cpp
    src/mechanical_cpu.cpp
    src/mechanical_formulations.cpp
    src/router.cpp
    src/interactive_router.cpp
    src/drc.cpp
    src/pour.cpp
    src/c_api.cpp
    src/c_api_h0_stubs.cpp
    src/balancing_sphere.cpp
    model/document.cpp
)

target_include_directories(designcore PUBLIC include)
target_include_directories(designcore PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})  # "model/..."
# U2 provides the real dc_doc_* impl; tell the H0 stubs to omit theirs.
target_compile_definitions(designcore PRIVATE DC_H0_HAVE_DOC)

# MSVC: export all C++ symbols from the DLL (not just the extern "C" API)
# so the test executable can link against the C++ classes directly.
set_target_properties(designcore PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)

install(TARGETS designcore
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR})
install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/include/designcore
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})

if (MSVC)
    target_compile_options(designcore PRIVATE /W4 /permissive-)
else()
    target_compile_options(designcore PRIVATE -Wall -Wextra)
endif()

# --- Open CASCADE (3D B-rep kernel) ---
# Distro OCCT packages frequently export optional VTK visualization targets
# even when VTK development files are absent. DesignCore only needs modeling,
# healing, meshing and STEP exchange, so resolve that focused link set directly.
option(USE_OCCT "Build with Open CASCADE 3D kernel" ON)
set(DESIGNCORE_OCCT_ENABLED OFF CACHE INTERNAL "DesignCore OCCT capability" FORCE)
if (USE_OCCT)
    find_path(OCCT_INCLUDE_DIR STEPControl_Reader.hxx PATH_SUFFIXES opencascade)
    set(OCCT_COMPONENTS
        TKDESTEP TKXSBase TKXCAF TKLCAF TKCAF TKCDF TKShHealing TKMesh TKOffset TKFillet TKBool TKBO
        TKPrim TKTopAlgo TKGeomAlgo TKGeomBase TKBRep TKG3d TKG2d TKMath TKernel
    )
    set(OCCT_LIBRARIES)
    set(OCCT_COMPLETE TRUE)
    foreach(component IN LISTS OCCT_COMPONENTS)
        find_library(${component}_LIBRARY NAMES ${component})
        if(NOT ${component}_LIBRARY)
            set(OCCT_COMPLETE FALSE)
        else()
            list(APPEND OCCT_LIBRARIES ${${component}_LIBRARY})
        endif()
    endforeach()
    if(OCCT_INCLUDE_DIR AND OCCT_COMPLETE)
        set(DESIGNCORE_OCCT_ENABLED ON CACHE INTERNAL "DesignCore OCCT capability" FORCE)
        message(STATUS "DesignCore: Open CASCADE B-Rep + STEP enabled")
        target_sources(designcore PRIVATE
            src/occt_kernel.cpp
            src/balancing_sphere_occt.cpp
        )
        target_include_directories(designcore PRIVATE ${OCCT_INCLUDE_DIR})
        target_link_libraries(designcore PRIVATE ${OCCT_LIBRARIES})
        target_compile_definitions(designcore PRIVATE
            HAS_OCCT=1
            DC_H0_HAVE_GEOMETRY=1
        )
        add_executable(designstudio-component-cad tools/component_cad_main.cpp)
        target_include_directories(designstudio-component-cad PRIVATE ${OCCT_INCLUDE_DIR})
        target_link_libraries(designstudio-component-cad PRIVATE ${OCCT_LIBRARIES})
        install(TARGETS designstudio-component-cad RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})

        add_executable(designstudio-board-step tools/board_step_main.cpp)
        target_include_directories(designstudio-board-step PRIVATE ${OCCT_INCLUDE_DIR})
        target_link_libraries(designstudio-board-step PRIVATE ${OCCT_LIBRARIES})
        install(TARGETS designstudio-board-step RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
    else()
        message(WARNING "DesignCore: OCCT not found; STEP/B-Rep generation disabled")
    endif()
endif()

# CUDA is deliberately optional.  The CPU solver remains the deterministic
# reference and is always compiled; when a CUDA toolkit is available the
# identical CSR contract can dispatch to the GPU (cuBLAS + a CUDA CSR kernel).
option(DESIGNCORE_ENABLE_CUDA
    "Build the optional CUDA mechanical solver backend" OFF)
if(DESIGNCORE_ENABLE_CUDA)
    include(CheckLanguage)
    check_language(CUDA)
    if(CMAKE_CUDA_COMPILER)
        enable_language(CUDA)
        find_package(CUDAToolkit QUIET)
        if(CUDAToolkit_FOUND)
            message(STATUS "DesignCore: optional CUDA mechanical solver enabled")
            target_sources(designcore PRIVATE src/mechanical_cuda.cu)
            target_compile_definitions(designcore PRIVATE DESIGNCORE_HAS_CUDA=1)
            target_link_libraries(designcore PRIVATE CUDA::cudart CUDA::cublas)
            set_target_properties(designcore PROPERTIES
                CUDA_STANDARD 17
                CUDA_STANDARD_REQUIRED ON
                CUDA_SEPARABLE_COMPILATION ON)
        else()
            message(WARNING "DesignCore: CUDA compiler found but CUDAToolkit was not found; using CPU solver")
        endif()
    else()
        message(WARNING "DesignCore: CUDA requested but no CUDA compiler was found; using CPU solver")
    endif()
endif()

if(BUILD_TESTING)
    enable_testing()
    add_subdirectory(tests)
endif()
