cmake_minimum_required(VERSION 3.16)
project(voice_gateway_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(voice_gateway_lib
    src/rtp_packet.cpp
    src/session.cpp
    src/session_registry.cpp
    src/http_server.cpp
)

target_include_directories(voice_gateway_lib PUBLIC include)

target_compile_options(voice_gateway_lib PRIVATE
    -Wall
    -Wextra
    -Wpedantic
)

add_executable(voice_gateway src/main.cpp)
target_link_libraries(voice_gateway PRIVATE voice_gateway_lib)

enable_testing()

add_executable(voice_gateway_tests tests/test_voice_gateway.cpp)
target_link_libraries(voice_gateway_tests PRIVATE voice_gateway_lib)

add_test(NAME voice_gateway_tests COMMAND voice_gateway_tests)
