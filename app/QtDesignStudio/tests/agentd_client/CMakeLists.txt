add_executable(agentd_fake_daemon FakeAgentd.cpp)
target_link_libraries(agentd_fake_daemon PRIVATE Qt6::Core)

add_executable(agentd_client_tests AgentdClientTests.cpp)
target_link_libraries(agentd_client_tests PRIVATE designstudio_ui Qt6::Core)

add_test(NAME qt_agentd_startup_reconnect_recovery
    COMMAND agentd_client_tests $<TARGET_FILE:agentd_fake_daemon>)
set_tests_properties(qt_agentd_startup_reconnect_recovery PROPERTIES
    TIMEOUT 20
    LABELS "qt;rust;json-rpc;workspace;recovery")

add_executable(cold_start_workspace_tests ColdStartWorkspaceTests.cpp)
target_link_libraries(cold_start_workspace_tests PRIVATE
    designstudio_ui Qt6::Core Qt6::Widgets)

add_test(NAME qt_cold_start_workspace_creation
    COMMAND cold_start_workspace_tests
        ${DESIGNSTUDIO_AGENTD_TEST_EXECUTABLE}
        ${CMAKE_SOURCE_DIR}/testdata/work-package-fixture)
set_tests_properties(qt_cold_start_workspace_creation PROPERTIES
    ENVIRONMENT "QT_QPA_PLATFORM=${DESIGNSTUDIO_TEST_QPA_PLATFORM};DS_AI_MOCK=1"
    TIMEOUT 30
    LABELS "qt;freecad;rust;workspace;cold-start;rollback")
