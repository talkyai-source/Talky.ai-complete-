cmake_minimum_required(VERSION 3.16)
project(voice_gateway_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS Debug Release RelWithDebInfo MinSizeRel ASan TSan)

# The gateway and its test suites all use std::thread, which needs pthread at
# link time. Link it PUBLIC on the library so every consumer inherits it.
set(THREADS_PREFER_PTHREAD_FLAG ON)
find_package(Threads REQUIRED)

option(VOICE_GATEWAY_ENABLE_ASAN "Enable AddressSanitizer + UBSan instrumentation" OFF)
option(VOICE_GATEWAY_ENABLE_TSAN "Enable ThreadSanitizer instrumentation" OFF)

if(CMAKE_BUILD_TYPE STREQUAL "ASan")
    set(VOICE_GATEWAY_ENABLE_ASAN ON)
endif()

if(CMAKE_BUILD_TYPE STREQUAL "TSan")
    set(VOICE_GATEWAY_ENABLE_TSAN ON)
endif()

if(VOICE_GATEWAY_ENABLE_ASAN AND VOICE_GATEWAY_ENABLE_TSAN)
    message(FATAL_ERROR "ASan and TSan cannot be enabled together")
endif()

function(voice_gateway_enable_sanitizers target_name)
    if(NOT CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        return()
    endif()

    if(VOICE_GATEWAY_ENABLE_ASAN)
        target_compile_options(${target_name} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
        target_link_options(${target_name} PRIVATE -fsanitize=address,undefined)
    endif()

    if(VOICE_GATEWAY_ENABLE_TSAN)
        target_compile_options(${target_name} PRIVATE -fsanitize=thread -fno-omit-frame-pointer)
        target_link_options(${target_name} PRIVATE -fsanitize=thread)
    endif()
endfunction()

add_library(voice_gateway_lib
    src/rtp_packet.cpp
    src/session.cpp
    src/session_registry.cpp
    src/http_server.cpp
)

target_include_directories(voice_gateway_lib PUBLIC include)
target_link_libraries(voice_gateway_lib PUBLIC Threads::Threads)

target_compile_options(voice_gateway_lib PRIVATE
    -Wall
    -Wextra
    -Wpedantic
)
voice_gateway_enable_sanitizers(voice_gateway_lib)

add_executable(voice_gateway src/main.cpp)
target_link_libraries(voice_gateway PRIVATE voice_gateway_lib)
voice_gateway_enable_sanitizers(voice_gateway)

enable_testing()

# Unit tests (RTP parse/serialize, registry validation).
add_executable(voice_gateway_tests tests/test_voice_gateway.cpp)
target_link_libraries(voice_gateway_tests PRIVATE voice_gateway_lib)
voice_gateway_enable_sanitizers(voice_gateway_tests)
add_test(NAME voice_gateway_tests COMMAND voice_gateway_tests)
set_tests_properties(voice_gateway_tests PROPERTIES TIMEOUT 120)

# Concurrency stress: teardown races, reaper, snapshot-under-churn, same-id/port
# restart race. Fixed ports -> RUN_SERIAL. A TIMEOUT lets CTest kill (and fail)
# a hung run rather than block forever if a deadlock regression returns.
add_executable(voice_gateway_concurrency_tests tests/test_concurrency.cpp)
target_link_libraries(voice_gateway_concurrency_tests PRIVATE voice_gateway_lib)
voice_gateway_enable_sanitizers(voice_gateway_concurrency_tests)
add_test(NAME voice_gateway_concurrency_tests COMMAND voice_gateway_concurrency_tests)
set_tests_properties(voice_gateway_concurrency_tests PROPERTIES TIMEOUT 300 RUN_SERIAL TRUE)

# Audit-fix regressions: VG-01 reorder, VG-02 flood-no-deadlock, VG-25 accounting,
# VG-18 end-to-end HTTP. Fixed ports -> RUN_SERIAL; TIMEOUT contains a hang.
add_executable(voice_gateway_fixes_tests tests/test_gateway_fixes.cpp)
target_link_libraries(voice_gateway_fixes_tests PRIVATE voice_gateway_lib)
voice_gateway_enable_sanitizers(voice_gateway_fixes_tests)
add_test(NAME voice_gateway_fixes_tests COMMAND voice_gateway_fixes_tests)
set_tests_properties(voice_gateway_fixes_tests PROPERTIES TIMEOUT 180 RUN_SERIAL TRUE)
