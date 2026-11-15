# == DO NOT EDIT THE FOLLOWING LINES for the Raspberry Pi Pico VS Code Extension to work ==
if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.3.0)
set(toolchainVersion 15_2_Rel1)
set(picotoolVersion 2.3.0)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()
# ====================================================================================
cmake_minimum_required(VERSION 3.13)

# Forklift firmware for the Free-WiLi OG, built against the wiliOGbsp submodule.
# Mirrors the BSP's own top-level CMakeLists.txt (which we can't use directly
# because it builds the BSP's example apps). Build: see ../README.md.

set(FWOG_BSP_DIR ${CMAKE_CURRENT_LIST_DIR}/../wiliOGbsp)
if(NOT EXISTS ${FWOG_BSP_DIR}/bsp/CMakeLists.txt)
    message(FATAL_ERROR "wiliOGbsp submodule missing: run `git submodule update --init` from the repo root")
endif()

# Board header. Do NOT pass -DPICO_BOARD on the command line.
set(PICO_BOARD freewili_og CACHE STRING "Board type" FORCE)
list(APPEND PICO_BOARD_HEADER_DIRS "${FWOG_BSP_DIR}/bsp/boards")
# UART0 is the inter-CPU link on both CPUs and must never carry stdio.
set(PICO_STDIO_UART 0 CACHE BOOL "stdio over UART (forced off: UART0 is the link)" FORCE)

include(${FWOG_BSP_DIR}/pico_sdk_import.cmake)
project(forklift C CXX ASM)
set(CMAKE_C_STANDARD 11)
find_package(Python3 COMPONENTS Interpreter REQUIRED)   # genimage.py embeds the display image
pico_sdk_init()

add_subdirectory(${FWOG_BSP_DIR}/bsp ${CMAKE_BINARY_DIR}/fwog_bsp)

# Version + commit time for the UF2 info record, as the BSP's top level does.
execute_process(COMMAND git describe --always --dirty
    WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
    OUTPUT_VARIABLE FWOG_GIT_DESCRIBE OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET RESULT_VARIABLE _rc)
if(NOT _rc EQUAL 0 OR FWOG_GIT_DESCRIBE STREQUAL "")
    set(FWOG_GIT_DESCRIBE "dev")
endif()
execute_process(COMMAND git log -1 --format=%ct
    WORKING_DIRECTORY ${CMAKE_CURRENT_LIST_DIR}
    OUTPUT_VARIABLE FWOG_GIT_COMMIT_TS OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET RESULT_VARIABLE _rc)
if(NOT _rc EQUAL 0 OR NOT FWOG_GIT_COMMIT_TS MATCHES "^[0-9]+$")
    set(FWOG_GIT_COMMIT_TS 0)
endif()

# Display half first: the main UF2 carries it inside.
add_executable(forklift_display display/main.c)
target_link_libraries(forklift_display fwog_display_bsp)
fwog_display_app(forklift_display
    VERSION 001
    DESCRIPTION "Forklift: services breakout I/O for main, shows ID and state")

add_executable(forklift_main main/main.c main/motor_proto.c main/byte_ring.c main/wifi_link.c
    main/rf_proto.c main/rf_link.c)
target_link_libraries(forklift_main fwog_main_bsp hardware_pwm hardware_uart hardware_i2c pico_i2c_slave)
# A laptop that holds the port open but stops reading must not stall the motor
# loop for the SDK's default 500 ms.
target_compile_definitions(forklift_main PRIVATE PICO_STDIO_USB_STDOUT_TIMEOUT_US=20000)
fwog_embed_display_image(forklift_main)
fwog_main_app(forklift_main
    VERSION 001
    DESCRIPTION "Forklift: W/F/S lines over USB, WiFi (I2C) or 915 MHz radio drive 3 L298Ns; 500 ms watchdog")

# Radio base station: a second OG on the laptop's USB, relaying the same lines to the
# forklift over the CC1101 (main/rf_link.h). Same display app, built with a BASE title.
add_executable(flbase_display display/main.c)
target_link_libraries(flbase_display fwog_display_bsp)
target_compile_definitions(flbase_display PRIVATE BASE_STATION=1)
fwog_display_app(flbase_display
    VERSION 001
    DESCRIPTION "Forklift radio base: shows BASE and the forklift's state")

add_executable(flbase_main base/main.c main/motor_proto.c main/rf_proto.c main/rf_link.c)
target_link_libraries(flbase_main fwog_main_bsp hardware_uart)
target_compile_definitions(flbase_main PRIVATE PICO_STDIO_USB_STDOUT_TIMEOUT_US=20000)
fwog_embed_display_image(flbase_main)
fwog_main_app(flbase_main
    VERSION 001
    DESCRIPTION "Forklift radio base: laptop USB lines <-> 915 MHz CC1101 <-> forklift OG")

# Diagnostic only: run the radio link at 433.92 MHz on the 400 MHz antenna path (the
# configuration the BSP's bench loopback verified). Not for normal use in the US.
option(FORKLIFT_RF_TEST_433 "Radio diagnostic build at 433.92 MHz" OFF)
if(FORKLIFT_RF_TEST_433)
    foreach(t forklift_main forklift_display flbase_main flbase_display)
        target_compile_definitions(${t} PRIVATE RF_TEST_433=1)
    endforeach()
endif()
