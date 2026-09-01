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
    property int missedJobId: 0
    property string missedFileName: ""

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
                window.missedJobId = jid;
                window.missedFileName = fn;
                window.missedScheduleBanner = "Missed scheduled conversion for '" + fn + "'.";
                missedScheduleModal.open();
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
                objectName: "mainSidebar"
                Layout.fillHeight: true
                currentTab: 0
                onTabSelected: function(idx) {
                    stackLayout.currentIndex = idx;
                }
            }

            StackLayout {
                id: stackLayout
                objectName: "mainStackLayout"
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

    ModalDialog {
        id: missedScheduleModal
        title: "Missed Scheduled Document"
        width: 460
        height: 260

        Column {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 14

            Text {
                text: "Missed Scheduled Document"
                color: "#F9FAFB"
                font.pixelSize: 16
                font.bold: true
            }

            Text {
                text: "The scheduled time for '" + window.missedFileName + "' passed while the desktop application was closed. How would you like to proceed?"
                color: "#D1D5DB"
                font.pixelSize: 13
                wrapMode: Text.Wrap
                width: parent.width
            }

            Row {
                spacing: 10
                anchors.horizontalCenter: parent.horizontalCenter

                Button {
                    text: "Run Now"
                    highlighted: true
                    onClicked: {
                        jobController.acknowledge_missed_schedule(window.missedJobId, "run_now", "");
                        window.missedScheduleBanner = "";
                        missedScheduleModal.close();
                    }
                }

                Button {
                    text: "Mark Paused"
                    onClicked: {
                        jobController.acknowledge_missed_schedule(window.missedJobId, "mark_paused", "");
                        window.missedScheduleBanner = "";
                        missedScheduleModal.close();
                    }
                }

                Button {
                    text: "Cancel Job"
                    onClicked: {
                        jobController.acknowledge_missed_schedule(window.missedJobId, "cancel", "");
                        window.missedScheduleBanner = "";
                        missedScheduleModal.close();
                    }
                }
            }
        }
    }
}
