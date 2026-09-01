import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import "views"

ApplicationWindow {
    id: window
    visible: true
    width: 1040
    height: 700
    minimumWidth: 800
    minimumHeight: 600
    title: "PolpoT — Desktop Document Intelligence"
    color: "#0F172A"

    property string globalError: ""
    property string missedScheduleBanner: ""

    Connections {
        target: jobController
        function onError_occurred(msg) { window.globalError = msg; }
    }
    Connections {
        target: apiKeyController
        function onError_occurred(msg) { window.globalError = msg; }
    }
    Connections {
        target: promptController
        function onError_occurred(msg) { window.globalError = msg; }
    }
    Connections {
        target: settingsController
        function onError_occurred(msg) { window.globalError = msg; }
    }
    Connections {
        target: quickConvertController
        function onError_occurred(msg) { window.globalError = msg; }
    }
    Connections {
        target: eventBridge
        function onMissed_schedule_received(jid, fn, sched, pol) {
            if (pol === "prompt") {
                window.missedScheduleBanner = "Missed scheduled job for '" + fn + "' while application was closed.";
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        NotificationBanner {
            Layout.fillWidth: true
            message: window.globalError
            bannerType: "error"
        }

        NotificationBanner {
            Layout.fillWidth: true
            message: window.missedScheduleBanner
            bannerType: "warning"
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            Sidebar {
                id: sidebar
                Layout.fillHeight: true
                currentTab: 0
                onTabSelected: function(idx) {
                    stackLayout.currentIndex = idx;
                }
            }

            StackLayout {
                id: stackLayout
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: 0

                JobQueueView {}
                HistoryView {}
                QuickConvertView {}
                ApiKeyView {}
                PromptEditorView {}
                SettingsView {}
            }
        }
    }
}
