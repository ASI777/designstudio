add_executable(cad_assist_contract_tests CadAssistContractTests.cpp)
target_link_libraries(cad_assist_contract_tests PRIVATE
    Qt6::Core
    designstudio_cad_assist
)

add_executable(cad_assist_application_tests
    CadAssistApplicationTests.cpp
    ../../CadAssistProjectBinding.cpp
    ../../CadAssistProjectBinding.h
    ../../ProjectModel.cpp
    ../../ProjectModel.h
)
target_include_directories(cad_assist_application_tests PRIVATE ../..)
target_link_libraries(cad_assist_application_tests PRIVATE
    Qt6::Core
    designstudio_cad_assist
)

set(CAD_ASSIST_FIXTURE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/fixtures)

add_test(NAME cad_assist_contract_positive
    COMMAND cad_assist_contract_tests positive ${CAD_ASSIST_FIXTURE_DIR})
add_test(NAME cad_assist_contract_negative
    COMMAND cad_assist_contract_tests negative ${CAD_ASSIST_FIXTURE_DIR})
add_test(NAME cad_assist_contract_limits
    COMMAND cad_assist_contract_tests limits ${CAD_ASSIST_FIXTURE_DIR})
add_test(NAME cad_assist_application_binding
    COMMAND cad_assist_application_tests ${CAD_ASSIST_FIXTURE_DIR})

# Build a contract whose base digest matches the exact project fixture bytes,
# then exercise the same MainWindow connection used by the Tools menu.
set(CAD_ASSIST_APP_PROJECT ${CAD_ASSIST_FIXTURE_DIR}/matching-project.dsproj)
file(SHA256 ${CAD_ASSIST_APP_PROJECT} CAD_ASSIST_APP_PROJECT_SHA256)
file(READ ${CAD_ASSIST_FIXTURE_DIR}/valid-session.json CAD_ASSIST_APP_CONTRACT)
string(REPLACE
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    "${CAD_ASSIST_APP_PROJECT_SHA256}"
    CAD_ASSIST_APP_CONTRACT
    "${CAD_ASSIST_APP_CONTRACT}")
set(CAD_ASSIST_APP_CONTRACT_PATH ${CMAKE_CURRENT_BINARY_DIR}/app-bound-session.json)
file(WRITE ${CAD_ASSIST_APP_CONTRACT_PATH} "${CAD_ASSIST_APP_CONTRACT}")
if(DESIGNSTUDIO_DEVELOPER_TOOLS)
    add_test(NAME qt_cad_assist_app_connection
        COMMAND $<TARGET_FILE:DesignStudioQt>
            --smoke-test
            --cad-assist ${CAD_ASSIST_APP_CONTRACT_PATH}
            ${CAD_ASSIST_APP_PROJECT})
endif()

set_tests_properties(
    cad_assist_contract_positive
    cad_assist_contract_negative
    cad_assist_contract_limits
    cad_assist_application_binding
    PROPERTIES
        TIMEOUT 30
        LABELS "qt;cad-assist;contract"
)

if(DESIGNSTUDIO_DEVELOPER_TOOLS)
    set_tests_properties(qt_cad_assist_app_connection PROPERTIES
        TIMEOUT 30 LABELS "qt;cad-assist;contract;diagnostics"
        ENVIRONMENT "QT_QPA_PLATFORM=${DESIGNSTUDIO_TEST_QPA_PLATFORM};DS_AI_MOCK=1"
        PASS_REGULAR_EXPRESSION "CAD_ASSIST_CONNECTED")
endif()
