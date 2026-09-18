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

    function doOpenReviewWorkspace(jobId) {
        if (typeof documentViewerController !== "undefined" && documentViewerController) {
            documentViewerController.loadPage(jobId, 1);
        }
        if (typeof markdownViewerController !== "undefined" && markdownViewerController) {
            markdownViewerController.loadDocument(jobId);
        }
        if (typeof markdownEditorController !== "undefined" && markdownEditorController) {
            markdownEditorController.loadSource(jobId);
        }
        sidebar.currentTab = 6;
        stackLayout.currentIndex = 6;
    }

    function openReviewWorkspace(jobId) {
        if (typeof markdownEditorController !== "undefined" && markdownEditorController &&
            (markdownEditorController.isDirty || markdownEditorController.hasConflict || markdownEditorController.mergeSessionActive) &&
            markdownEditorController.activeJobId > 0 &&
            markdownEditorController.activeJobId !== jobId) {
            jobSwitchConfirmModal.pendingJobId = jobId;
            jobSwitchConfirmModal.open();
            return;
        }
        doOpenReviewWorkspace(jobId);
    }

    Connections {
        target: jobController
        function onError_occurred(msg) { window.globalError = msg; }
        function onOpen_review_requested(jid) { window.openReviewWorkspace(jid); }
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
                ReviewWorkspaceView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                }
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

    ModalDialog {
        id: jobSwitchConfirmModal
        objectName: "jobSwitchConfirmModal"
        title: "Unsaved Merge / Changes"
        width: 460
        height: 240
        property int pendingJobId: 0

        Column {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 14

            Text {
                text: "Unsaved Merge / Changes"
                color: "#F9FAFB"
                font.pixelSize: 16
                font.bold: true
            }

            Text {
                text: "You have uncommitted changes or an active conflict resolution session in the review editor. Switching jobs will discard these changes. Would you like to proceed?"
                color: "#D1D5DB"
                font.pixelSize: 13
                wrapMode: Text.Wrap
                width: parent.width
            }

            Row {
                spacing: 10
                anchors.horizontalCenter: parent.horizontalCenter

                Button {
                    id: jobSwitchCancelBtn
                    objectName: "jobSwitchCancelButton"
                    text: "Cancel"
                    onClicked: {
                        jobSwitchConfirmModal.pendingJobId = 0;
                        jobSwitchConfirmModal.close();
                    }
                }

                Button {
                    id: jobSwitchDiscardBtn
                    objectName: "jobSwitchDiscardButton"
                    text: "Discard & Switch"
                    highlighted: true
                    onClicked: {
                        var targetId = jobSwitchConfirmModal.pendingJobId;
                        jobSwitchConfirmModal.pendingJobId = 0;
                        jobSwitchConfirmModal.close();
                        if (typeof markdownEditorController !== "undefined" && markdownEditorController) {
                            markdownEditorController.clear();
                        }
                        window.doOpenReviewWorkspace(targetId);
                    }
                }
            }
        }
    }
}
