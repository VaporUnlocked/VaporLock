cmake_minimum_required(VERSION 3.24)

project(VaporLock
    VERSION 0.2.1
    DESCRIPTION "ONNX-based user-controlled aim assistance"
    LANGUAGES CXX)

include(CheckLanguage)
include(CTest)

# CMake may cache CUDA as NOTFOUND before it considers the standard CUDA_PATH
# environment variable. Respect an explicit compiler first, then recover the
# normal NVIDIA Toolkit location when it is available.
if(NOT CMAKE_CUDA_COMPILER AND DEFINED ENV{CUDA_PATH} AND EXISTS "$ENV{CUDA_PATH}/bin/nvcc.exe")
    set(CMAKE_CUDA_COMPILER "$ENV{CUDA_PATH}/bin/nvcc.exe" CACHE FILEPATH "NVIDIA CUDA compiler" FORCE)
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/product_version.txt" VAPORLOCK_RELEASE_VERSION)
list(LENGTH VAPORLOCK_RELEASE_VERSION VAPORLOCK_VERSION_LINES)
if(NOT VAPORLOCK_VERSION_LINES EQUAL 1 OR
   NOT VAPORLOCK_RELEASE_VERSION MATCHES "^([0-9]+\\.[0-9]+\\.[0-9]+)(-[0-9A-Za-z]+(\\.[0-9A-Za-z]+)*)?$")
    message(FATAL_ERROR "Invalid product_version.txt release label")
endif()
if(NOT CMAKE_MATCH_1 STREQUAL PROJECT_VERSION)
    message(FATAL_ERROR "Release label must match the numeric CMake project version")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "product_version.txt")
add_compile_definitions(VAPORLOCK_PRODUCT_VERSION="${VAPORLOCK_RELEASE_VERSION}")

if(BUILD_TESTING)
    if(WIN32)
        add_executable(CloudPipeFixture tests/cloud_pipe_fixture.cpp)
        target_include_directories(CloudPipeFixture PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
        add_executable(CloudPipeFixtureFree tests/cloud_pipe_fixture.cpp)
        target_include_directories(CloudPipeFixtureFree PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

        add_executable(CloudOwnedPipeFixture tests/cloud_owned_pipe_fixture.cpp)
        target_include_directories(CloudOwnedPipeFixture PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
        add_executable(CloudOwnedPipeFixtureFree tests/cloud_owned_pipe_fixture.cpp)
        target_include_directories(CloudOwnedPipeFixtureFree PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

    endif()
    add_executable(BgraTensorResizeTests tests/bgra_tensor_resize_tests.cpp)
    target_include_directories(BgraTensorResizeTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    add_test(NAME BgraTensorResize COMMAND BgraTensorResizeTests)
    set_tests_properties(BgraTensorResize PROPERTIES TIMEOUT 30)
    add_executable(OnnxCacheEligibilityTests tests/onnx_cache_eligibility_tests.cpp)
    target_include_directories(OnnxCacheEligibilityTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    file(GLOB VAPORLOCK_ONNX_TEST_FIXTURES "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/onnx-cache/*.onnx")
    list(LENGTH VAPORLOCK_ONNX_TEST_FIXTURES VAPORLOCK_ONNX_TEST_FIXTURE_COUNT)
    if(VAPORLOCK_ONNX_TEST_FIXTURE_COUNT EQUAL 12)
        add_test(NAME OnnxCacheEligibility COMMAND OnnxCacheEligibilityTests "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/onnx-cache")
        set_tests_properties(OnnxCacheEligibility PROPERTIES TIMEOUT 20)
    else()
        message(STATUS "OnnxCacheEligibility test not registered: twelve synthetic ONNX fixtures are not supplied")
    endif()
    add_executable(TensorRtCacheTests tests/tensorrt_cache_tests.cpp)
    target_include_directories(TensorRtCacheTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_compile_definitions(TensorRtCacheTests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
    target_link_libraries(TensorRtCacheTests PRIVATE bcrypt)
    add_test(NAME TensorRtCache COMMAND TensorRtCacheTests)
    set_tests_properties(TensorRtCache PROPERTIES TIMEOUT 20)
    add_executable(InferenceCadenceTests tests/inference_cadence_tests.cpp)
    target_include_directories(InferenceCadenceTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    add_test(NAME InferenceCadence COMMAND InferenceCadenceTests)
    set_tests_properties(InferenceCadence PROPERTIES TIMEOUT 20)
    add_executable(CaptureQualificationTests tests/capture_qualification_tests.cpp)
    target_include_directories(CaptureQualificationTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    add_test(NAME CaptureQualification COMMAND CaptureQualificationTests)
    set_tests_properties(CaptureQualification PROPERTIES TIMEOUT 20)
    foreach(edition IN ITEMS premium free)
        add_executable(TuningTests-${edition} tests/native_tuning_tests.cpp)
        target_include_directories(TuningTests-${edition} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
        target_compile_definitions(TuningTests-${edition} PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN VAPORLOCK_HAS_ONNXRUNTIME=0)
        target_link_libraries(TuningTests-${edition} PRIVATE bcrypt advapi32)
        target_sources(TuningTests-${edition} PRIVATE app/HardwareOutput.cpp)
        target_link_libraries(TuningTests-${edition} PRIVATE user32 gdi32 comdlg32 comctl32 shcore shell32 ole32 windowscodecs d3d11 dxgi ws2_32)
        add_test(NAME Tuning-${edition} COMMAND TuningTests-${edition})
    endforeach()

    add_executable(PublicAccessTests tests/native_public_access_tests.cpp app/HardwareOutput.cpp)
    target_include_directories(PublicAccessTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_compile_definitions(PublicAccessTests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN VAPORLOCK_HAS_ONNXRUNTIME=0)
    target_link_libraries(PublicAccessTests PRIVATE user32 gdi32 comdlg32 comctl32 shcore shell32 ole32 windowscodecs d3d11 dxgi ws2_32 bcrypt)
    add_test(NAME PublicAccess COMMAND PublicAccessTests)
    set_tests_properties(PublicAccess PROPERTIES TIMEOUT 20)

    add_executable(HardwareOutputTests tests/hardware_output_tests.cpp)
    target_include_directories(HardwareOutputTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_compile_definitions(HardwareOutputTests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
    target_link_libraries(HardwareOutputTests PRIVATE ws2_32)
    add_test(NAME HardwareOutput COMMAND HardwareOutputTests)
    set_tests_properties(HardwareOutput PROPERTIES TIMEOUT 20)

    add_executable(FrameRenderingTests tests/frame_rendering_tests.cpp app/HardwareOutput.cpp)
    target_compile_definitions(FrameRenderingTests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN VAPORLOCK_HAS_ONNXRUNTIME=0)
    target_include_directories(FrameRenderingTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

    target_link_libraries(FrameRenderingTests PRIVATE user32 gdi32 comdlg32 comctl32 shcore shell32 ole32 windowscodecs d3d11 dxgi ws2_32)
    target_link_libraries(FrameRenderingTests PRIVATE bcrypt advapi32)
    add_test(NAME FrameRendering COMMAND FrameRenderingTests)
    set_tests_properties(FrameRendering PROPERTIES TIMEOUT 20)

    add_executable(AsyncTelemetryTests tests/async_telemetry_tests.cpp)
    target_include_directories(AsyncTelemetryTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_compile_definitions(AsyncTelemetryTests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
    add_test(NAME AsyncTelemetry COMMAND AsyncTelemetryTests)
    set_tests_properties(AsyncTelemetry PROPERTIES TIMEOUT 20)

    add_executable(DatasetWriterTests tests/dataset_writer_tests.cpp app/HardwareOutput.cpp)
    target_compile_definitions(DatasetWriterTests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN VAPORLOCK_HAS_ONNXRUNTIME=0)
    target_include_directories(DatasetWriterTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

    target_link_libraries(DatasetWriterTests PRIVATE user32 gdi32 comdlg32 comctl32 shcore shell32 ole32 windowscodecs d3d11 dxgi ws2_32)
    target_link_libraries(DatasetWriterTests PRIVATE bcrypt advapi32)
    add_test(NAME DatasetWriter COMMAND DatasetWriterTests)
    set_tests_properties(DatasetWriter PROPERTIES TIMEOUT 20)
    add_executable(DatasetWriterPremiumTests tests/dataset_writer_tests.cpp app/HardwareOutput.cpp)
    target_include_directories(DatasetWriterPremiumTests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_compile_definitions(DatasetWriterPremiumTests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN VAPORLOCK_HAS_ONNXRUNTIME=0)
    target_link_libraries(DatasetWriterPremiumTests PRIVATE user32 gdi32 comdlg32 comctl32 shcore shell32 ole32 windowscodecs d3d11 dxgi ws2_32)
    target_link_libraries(DatasetWriterPremiumTests PRIVATE bcrypt advapi32)
    add_test(NAME DatasetWriterPremium COMMAND DatasetWriterPremiumTests)
    set_tests_properties(DatasetWriterPremium PROPERTIES TIMEOUT 20)
endif()

# Legacy edition arguments remain accepted for existing build commands. They all
# produce the same unrestricted Community client; no edition macro is emitted.
set(VAPORLOCK_EDITION "community" CACHE STRING "Compatibility label; all builds are unrestricted")
set_property(CACHE VAPORLOCK_EDITION PROPERTY STRINGS community premium free)
string(TOLOWER "${VAPORLOCK_EDITION}" VAPORLOCK_EDITION)
if(NOT VAPORLOCK_EDITION MATCHES "^(community|premium|free)$")
    message(FATAL_ERROR "VAPORLOCK_EDITION must be community, premium or free (equivalent aliases)")
endif()

set(ONNXRUNTIME_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/third_party/onnxruntime-gpu" CACHE PATH "Path to an extracted ONNX Runtime Windows SDK")
set(ONNXRUNTIME_DML_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/third_party/onnxruntime-directml-1.24.4" CACHE PATH "Path to an extracted ONNX Runtime DirectML package")
set(VAPORLOCK_CUDA_RUNTIME_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/third_party/cuda-runtime" CACHE PATH "Directory containing CUDA/cuDNN runtime DLLs")
set(VAPORLOCK_TENSORRT_RTX_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/third_party/tensorrt-rtx-1.6.1.120/TensorRT-RTX-1.6.1.120" CACHE PATH "Path to an extracted TensorRT RTX Windows SDK")
option(VAPORLOCK_BUILD_CUDA_PREPROCESS "Build the optional custom CUDA preprocessing module" OFF)
option(VAPORLOCK_BUILD_TENSORRT_PREPROCESS "Build GPU preprocessing for the TensorRT RTX engine" ON)

function(vaporlock_harden_windows_binary target)
    if(MSVC)
        # Control Flow Guard and the standard Windows image mitigations make a
        # tampered native process materially harder to redirect at runtime.
        target_compile_options(${target} PRIVATE /guard:cf)
        target_link_options(${target} PRIVATE /guard:cf /DYNAMICBASE /NXCOMPAT /HIGHENTROPYVA)
    endif()
endfunction()

set(VAPORLOCK_SOURCES
    app/Win32VaporLock.cpp
    app/HardwareOutput.cpp
    app/VaporLock.rc
)

# TensorRT RTX is deliberately isolated from the ONNX Runtime engines. Its
# native runtime is consumer-RTX specific and must prove it can create both a
# builder and ONNX parser before it is offered by the control center.
if(EXISTS "${VAPORLOCK_TENSORRT_RTX_ROOT}/include/NvInfer.h")
    find_package(CUDAToolkit REQUIRED)
    # NVIDIA's Windows 1.6 SDK CMake export currently references a capture
    # player binary that the archive does not include. Import only the two
    # runtime libraries this project uses instead of making the build depend
    # on that unrelated absent tool.
    add_library(VaporLockTensorRtRtx SHARED IMPORTED)
    set_target_properties(VaporLockTensorRtRtx PROPERTIES
        IMPORTED_IMPLIB "${VAPORLOCK_TENSORRT_RTX_ROOT}/lib/tensorrt_rtx_1_6.lib"
        IMPORTED_LOCATION "${VAPORLOCK_TENSORRT_RTX_ROOT}/bin/tensorrt_rtx_1_6.dll"
        INTERFACE_INCLUDE_DIRECTORIES "${VAPORLOCK_TENSORRT_RTX_ROOT}/include")
    add_library(VaporLockTensorRtRtxOnnxParser SHARED IMPORTED)
    set_target_properties(VaporLockTensorRtRtxOnnxParser PROPERTIES
        IMPORTED_IMPLIB "${VAPORLOCK_TENSORRT_RTX_ROOT}/lib/tensorrt_onnxparser_rtx_1_6.lib"
        IMPORTED_LOCATION "${VAPORLOCK_TENSORRT_RTX_ROOT}/bin/tensorrt_onnxparser_rtx_1_6.dll"
        INTERFACE_INCLUDE_DIRECTORIES "${VAPORLOCK_TENSORRT_RTX_ROOT}/include")
    if(BUILD_TESTING)
        add_executable(TensorRtRtxSmoke tests/tensorrt_rtx_smoke.cpp)
        target_link_libraries(TensorRtRtxSmoke PRIVATE VaporLockTensorRtRtx VaporLockTensorRtRtxOnnxParser CUDA::cudart_static)
        set_property(TARGET TensorRtRtxSmoke PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
        target_include_directories(TensorRtRtxSmoke PRIVATE "${VAPORLOCK_TENSORRT_RTX_ROOT}/include")
        vaporlock_harden_windows_binary(TensorRtRtxSmoke)
        add_custom_command(TARGET TensorRtRtxSmoke POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${VAPORLOCK_TENSORRT_RTX_ROOT}/bin/tensorrt_rtx_1_6.dll" "$<TARGET_FILE_DIR:TensorRtRtxSmoke>/tensorrt_rtx_1_6.dll"
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${VAPORLOCK_TENSORRT_RTX_ROOT}/bin/tensorrt_onnxparser_rtx_1_6.dll" "$<TARGET_FILE_DIR:TensorRtRtxSmoke>/tensorrt_onnxparser_rtx_1_6.dll"
        )
        add_test(NAME TensorRtRtxSmoke COMMAND TensorRtRtxSmoke)
    endif()
endif()

configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/app/ProductVersion.h.in"
    "${CMAKE_CURRENT_BINARY_DIR}/generated/ProductVersion.h"
    @ONLY)

add_executable(VaporLockLauncher WIN32 app/PortableLauncher.cpp app/VaporLock.rc)
target_compile_definitions(VaporLockLauncher PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_include_directories(VaporLockLauncher PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/generated")
target_link_libraries(VaporLockLauncher PRIVATE user32)
if(MSVC)
    target_compile_options(VaporLockLauncher PRIVATE /W4 /permissive-)
endif()
vaporlock_harden_windows_binary(VaporLockLauncher)

function(vaporlock_common_target target)
    add_executable(${target} WIN32 ${VAPORLOCK_SOURCES})
    set_target_properties(${target} PROPERTIES COMPILE_PDB_NAME "${target}_compile")
    target_include_directories(${target} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_compile_definitions(${target} PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
    target_link_libraries(${target} PRIVATE user32 gdi32 comdlg32 comctl32 shcore shell32 ole32 windowscodecs d3d11 dxgi ws2_32)
    target_link_libraries(${target} PRIVATE bcrypt advapi32)
    if(MSVC)
        target_compile_options(${target} PRIVATE
            $<$<COMPILE_LANGUAGE:CXX>:/W4>
            $<$<COMPILE_LANGUAGE:CXX>:/permissive->)
    else()
        target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
    endif()
    vaporlock_harden_windows_binary(${target})
    add_custom_command(TARGET ${target} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory $<TARGET_FILE_DIR:${target}>
        COMMAND ${CMAKE_COMMAND} -E copy_directory ${CMAKE_CURRENT_SOURCE_DIR}/assets $<TARGET_FILE_DIR:${target}>/assets
    )
endfunction()

# CPU and CUDA share the CUDA-enabled ONNX Runtime distribution. The UI chooses
# CPU explicitly; CUDA is only activated when the user selects it.
vaporlock_common_target(VaporLock)
if(ONNXRUNTIME_ROOT AND EXISTS "${ONNXRUNTIME_ROOT}/include/onnxruntime_cxx_api.h")
    if(BUILD_TESTING)
        add_executable(CpuPlacementTests tests/cpu_placement_tests.cpp)
        target_include_directories(CpuPlacementTests PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${ONNXRUNTIME_ROOT}/include")
        target_compile_definitions(CpuPlacementTests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
        target_link_libraries(CpuPlacementTests PRIVATE "${ONNXRUNTIME_ROOT}/lib/onnxruntime.lib")
    endif()
    if(BUILD_TESTING)
        add_executable(InferenceMetadataTests tests/inference_metadata_tests.cpp)
        target_include_directories(InferenceMetadataTests PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${ONNXRUNTIME_ROOT}/include")
        target_link_libraries(InferenceMetadataTests PRIVATE "${ONNXRUNTIME_ROOT}/lib/onnxruntime.lib")
        add_custom_command(TARGET InferenceMetadataTests POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${ONNXRUNTIME_ROOT}/lib/onnxruntime.dll" "$<TARGET_FILE_DIR:InferenceMetadataTests>/onnxruntime.dll")
        if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/tensorrt-identity.onnx")
            add_test(NAME InferenceMetadata COMMAND InferenceMetadataTests "${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures/tensorrt-identity.onnx")
            set_tests_properties(InferenceMetadata PROPERTIES TIMEOUT 20)
        else()
            message(STATUS "InferenceMetadata test not registered: synthetic ONNX fixture is not supplied")
        endif()
    endif()
    target_compile_definitions(VaporLock PRIVATE VAPORLOCK_HAS_ONNXRUNTIME=1 VAPORLOCK_HAS_CUDA=1)
    target_include_directories(VaporLock PRIVATE "${ONNXRUNTIME_ROOT}/include")
    # The CPU base engine requires only ONNX Runtime itself. CUDA provider and
    # NVIDIA runtime files are fetched on demand by the acceleration manager.
    foreach(runtime_dll IN ITEMS onnxruntime.dll onnxruntime_providers_shared.dll)
        if(EXISTS "${ONNXRUNTIME_ROOT}/lib/${runtime_dll}")
            add_custom_command(TARGET VaporLock POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different "${ONNXRUNTIME_ROOT}/lib/${runtime_dll}" "$<TARGET_FILE_DIR:VaporLock>/${runtime_dll}"
            )
        endif()
    endforeach()
    if(VAPORLOCK_BUILD_CUDA_PREPROCESS AND EXISTS "${VAPORLOCK_CUDA_RUNTIME_ROOT}")
        file(GLOB_RECURSE VAPORLOCK_CUDA_RUNTIME_DLLS CONFIGURE_DEPENDS "${VAPORLOCK_CUDA_RUNTIME_ROOT}/*.dll")
        foreach(runtime_dll IN LISTS VAPORLOCK_CUDA_RUNTIME_DLLS)
            get_filename_component(runtime_name "${runtime_dll}" NAME)
            add_custom_command(TARGET VaporLock POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different "${runtime_dll}" "$<TARGET_FILE_DIR:VaporLock>/${runtime_name}"
            )
        endforeach()
    endif()
    # Keep CUDA preprocessing optional: the standard ONNX Runtime CUDA path
    # remains usable on build machines that have only the redistributable.
    if(VAPORLOCK_BUILD_CUDA_PREPROCESS)
        check_language(CUDA)
    endif()
    if(VAPORLOCK_BUILD_CUDA_PREPROCESS AND CMAKE_CUDA_COMPILER)
        enable_language(CUDA)
        find_package(CUDAToolkit REQUIRED)
        target_sources(VaporLock PRIVATE detection/CudaPreprocess.cu)
        target_link_libraries(VaporLock PRIVATE CUDA::cudart)
        target_compile_definitions(VaporLock PRIVATE VAPORLOCK_HAS_CUDA_PREPROCESS=1)
        set_target_properties(VaporLock PROPERTIES CUDA_STANDARD 17 CUDA_STANDARD_REQUIRED ON CUDA_ARCHITECTURES native)
    endif()
endif()

# DirectML ships with a different ONNX Runtime binary, so it is a deliberately
# separate executable. This prevents incompatible provider DLLs from being
# loaded into one process.
set(VAPORLOCK_DML_NATIVE "${ONNXRUNTIME_DML_ROOT}/runtimes/win-x64/native")
if(EXISTS "${ONNXRUNTIME_DML_ROOT}/build/native/include/onnxruntime_cxx_api.h" AND EXISTS "${VAPORLOCK_DML_NATIVE}/onnxruntime.lib")
    vaporlock_common_target(VaporLockDml)
    set_target_properties(VaporLockDml PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/$<CONFIG>/dml")
    target_compile_definitions(VaporLockDml PRIVATE VAPORLOCK_HAS_ONNXRUNTIME=1 VAPORLOCK_HAS_DML=1 VAPORLOCK_FORCE_DML=1)
    target_include_directories(VaporLockDml PRIVATE "${ONNXRUNTIME_DML_ROOT}/build/native/include")
    target_link_libraries(VaporLockDml PRIVATE d3d12)
    foreach(runtime_dll IN ITEMS onnxruntime.dll onnxruntime_providers_shared.dll)
        add_custom_command(TARGET VaporLockDml POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${VAPORLOCK_DML_NATIVE}/${runtime_dll}" "$<TARGET_FILE_DIR:VaporLockDml>/${runtime_dll}"
        )
    endforeach()
endif()

# TensorRT RTX is a dedicated NVIDIA consumer-GPU engine. It shares the
# ordinary ONNX Runtime binary only for model metadata and output decoding;
# model execution, engine building, and caching use NVIDIA's direct runtime.
if(EXISTS "${VAPORLOCK_TENSORRT_RTX_ROOT}/include/NvInfer.h" AND ONNXRUNTIME_ROOT AND EXISTS "${ONNXRUNTIME_ROOT}/include/onnxruntime_cxx_api.h")
    find_package(CUDAToolkit REQUIRED)
    vaporlock_common_target(VaporLockTensorRt)
    set_target_properties(VaporLockTensorRt PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/$<CONFIG>/trt")
    target_compile_definitions(VaporLockTensorRt PRIVATE VAPORLOCK_HAS_ONNXRUNTIME=1 VAPORLOCK_FORCE_TENSORRT_RTX=1)
    target_include_directories(VaporLockTensorRt PRIVATE "${ONNXRUNTIME_ROOT}/include" "${VAPORLOCK_TENSORRT_RTX_ROOT}/include")
    target_link_libraries(VaporLockTensorRt PRIVATE VaporLockTensorRtRtx VaporLockTensorRtRtxOnnxParser CUDA::cudart_static)
    # The static CUDA runtime uses the static CRT. Match every object in this
    # executable instead of suppressing conflicting CRT defaults at link time.
    set_property(TARGET VaporLockTensorRt PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    if(VAPORLOCK_BUILD_TENSORRT_PREPROCESS)
        if(NOT EXISTS "${CUDAToolkit_NVCC_EXECUTABLE}")
            message(FATAL_ERROR "TensorRT GPU preprocessing requires the CUDA compiler; disable VAPORLOCK_BUILD_TENSORRT_PREPROCESS explicitly for a CPU-preprocess build")
        endif()
        get_filename_component(VAPORLOCK_CUDA_TOOLKIT_DIRECTORY "${CUDAToolkit_BIN_DIR}" DIRECTORY)
        if(WIN32 AND NOT EXISTS "${VAPORLOCK_CUDA_TOOLKIT_DIRECTORY}/nvvm/bin/cicc.exe")
            message(FATAL_ERROR "TensorRT GPU preprocessing needs the CUDA libnvvm compiler component (nvvm/bin/cicc.exe)")
        endif()
        # Compile a single CUDA object directly. The redistributable toolkit
        # has nvcc but no machine-wide Visual Studio CUDA integration installed.
        # PTX for Turing supports newer RTX architectures; include native Blackwell.
        get_filename_component(VAPORLOCK_HOST_COMPILER_DIR "${CMAKE_CXX_COMPILER}" DIRECTORY)
        set(VAPORLOCK_TRT_CUDA_OBJECT "${CMAKE_CURRENT_BINARY_DIR}/trt-preprocess/$<CONFIG>/CudaPreprocess.obj")
        add_custom_command(OUTPUT "${VAPORLOCK_TRT_CUDA_OBJECT}"
            COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_CURRENT_BINARY_DIR}/trt-preprocess/$<CONFIG>"
            COMMAND "${CUDAToolkit_NVCC_EXECUTABLE}" --std=c++17 --machine 64
                --compiler-bindir "${VAPORLOCK_HOST_COMPILER_DIR}"
                -Xcompiler "$<IF:$<CONFIG:Debug>,/MTd,/MT>,/EHsc,/guard:cf"
                --generate-code=arch=compute_75,code=compute_75
                --generate-code=arch=compute_120,code=sm_120
                -I "${CMAKE_CURRENT_SOURCE_DIR}"
                -c "${CMAKE_CURRENT_SOURCE_DIR}/detection/CudaPreprocess.cu"
                -o "${VAPORLOCK_TRT_CUDA_OBJECT}"
            DEPENDS detection/CudaPreprocess.cu detection/CudaPreprocess.h VERBATIM)
        set_source_files_properties("${VAPORLOCK_TRT_CUDA_OBJECT}" PROPERTIES EXTERNAL_OBJECT TRUE GENERATED TRUE)
        target_sources(VaporLockTensorRt PRIVATE "${VAPORLOCK_TRT_CUDA_OBJECT}")
        target_compile_definitions(VaporLockTensorRt PRIVATE VAPORLOCK_HAS_CUDA_PREPROCESS=1)
    endif()
    if(MSVC)
        target_link_options(VaporLockTensorRt PRIVATE $<$<CONFIG:Debug>:/MAP>)
    endif()
    foreach(runtime_dll IN ITEMS onnxruntime.dll onnxruntime_providers_shared.dll)
        if(EXISTS "${ONNXRUNTIME_ROOT}/lib/${runtime_dll}")
            add_custom_command(TARGET VaporLockTensorRt POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different "${ONNXRUNTIME_ROOT}/lib/${runtime_dll}" "$<TARGET_FILE_DIR:VaporLockTensorRt>/${runtime_dll}")
        endif()
    endforeach()
    foreach(runtime_dll IN ITEMS tensorrt_rtx_1_6.dll tensorrt_onnxparser_rtx_1_6.dll)
        add_custom_command(TARGET VaporLockTensorRt POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${VAPORLOCK_TENSORRT_RTX_ROOT}/bin/${runtime_dll}" "$<TARGET_FILE_DIR:VaporLockTensorRt>/${runtime_dll}")
    endforeach()
endif()
