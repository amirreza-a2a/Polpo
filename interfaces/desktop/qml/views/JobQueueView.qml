import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"

Item {
    id: root
    property string selectedFilePath: ""
    property int targetJobId: 0

    FileDialog {
        id: pdfPicker
        title: "Select PDF Document for Conversion"
        nameFilters: ["PDF Files (*.pdf)", "All Files (*)"]
        onAccepted: {
            root.selectedFilePath = selectedFile.toString();
            submitModal.open();
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            Text {
                text: "Active Conversion Queue"
                color: "#F9FAFB"
                font.pixelSize: 20
                font.bold: true
            }

            Item { Layout.fillWidth: true }

            Button {
                id: submitDocBtn
                objectName: "submitDocumentButton"
                text: "+ Submit Document"
                highlighted: true
                onClicked: pdfPicker.open()
            }
        }

        ListView {
            id: queueList
            objectName: "jobQueueList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 12
            model: typeof jobQueueModel !== "undefined" ? jobQueueModel : null

            delegate: Card {
                width: ListView.view.width
                height: 96

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 16

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        RowLayout {
                            spacing: 12
                            Text {
                                text: model.fileName
                                color: "#F9FAFB"
                                font.pixelSize: 15
                                font.bold: true
                                elide: Text.ElideRight
                                Layout.maximumWidth: 260
                            }
                            StatusBadge {
                                status: model.status
                            }
                            Text {
                                text: model.activeApiLabel ? ("API: " + model.activeApiLabel) : ""
                                color: "#9CA3AF"
                                font.pixelSize: 12
                            }
                        }

                        ProgressBar {
                            Layout.fillWidth: true
                            value: model.progressPercent
                        }

                        RowLayout {
                            spacing: 16
                            Text {
                                text: "Page " + model.processedPages + " of " + model.totalPages + " (" + Math.round(model.progressPercent) + "%)"
                                color: "#9CA3AF"
                                font.pixelSize: 12
                            }
                            Text {
                                text: model.scheduledAt ? ("Scheduled: " + model.scheduledAt) : ""
                                color: "#F59E0B"
                                font.pixelSize: 11
                                visible: model.scheduledAt !== ""
                            }
                            Text {
                                text: model.errorMessage ? ("Error: " + model.errorMessage) : ""
                                color: "#EF4444"
                                font.pixelSize: 11
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                                visible: model.errorMessage !== ""
                            }
                        }
                    }

                    RowLayout {
                        spacing: 8

                        Button {
                            text: "Resume"
                            visible: model.status === "paused"
                            onClicked: jobController.resume_job(model.id)
                        }

                        Button {
                            text: "Run Now"
                            visible: model.status === "paused" || model.scheduledAt !== ""
                            onClicked: jobController.run_now(model.id)
                        }

                        Button {
                            text: "Retry"
                            visible: model.status === "failed"
                            onClicked: jobController.retry_job(model.id)
                        }

                        Button {
                            text: "Reschedule"
                            visible: model.status === "pending" || model.status === "paused"
                            onClicked: {
                                root.targetJobId = model.id;
                                rescheduleModal.open();
                            }
                        }

                        Button {
                            text: "Cancel"
                            onClicked: jobController.cancel_job(model.id)
                        }
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                text: "No active jobs in queue. Click '+ Submit Document' to convert a PDF."
                color: "#6B7280"
                font.pixelSize: 14
                visible: queueList.count === 0
            }
        }
    }

    ModalDialog {
        id: submitModal
        objectName: "submitJobModal"
        title: "Submit Document for Conversion"
        width: 480
        height: 380

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 14

            Text {
                text: "Submit Document"
                color: "#F9FAFB"
                font.pixelSize: 18
                font.bold: true
            }

            Text {
                text: "Selected: " + root.selectedFilePath
                color: "#9CA3AF"
                font.pixelSize: 12
                elide: Text.ElideMiddle
                Layout.fillWidth: true
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Prompt Template:"
                    color: "#D1D5DB"
                    font.pixelSize: 13
                    Layout.preferredWidth: 120
                }
                ComboBox {
                    id: promptCombo
                    Layout.fillWidth: true
                    model: typeof promptListModel !== "undefined" ? promptListModel : null
                    textRole: "name"
                    valueRole: "id"
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Scheduled Time:"
                    color: "#D1D5DB"
                    font.pixelSize: 13
                    Layout.preferredWidth: 120
                }
                TextField {
                    id: scheduleInput
                    Layout.fillWidth: true
                    placeholderText: "Immediate (or ISO UTC date)"
                }
            }

            CheckBox {
                id: p2Check
                text: "Enable Pipeline 2 Auto-Refinement"
                checked: typeof settingsController !== "undefined" && settingsController ? settingsController.autoPipeline2 : false
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 16

                Button {
                    text: "Cancel"
                    onClicked: submitModal.close()
                }

                Button {
                    text: "Start Conversion"
                    highlighted: true
                    onClicked: {
                        var pid = promptCombo.currentValue ? promptCombo.currentValue : 0;
                        jobController.submit_job(
                            root.selectedFilePath,
                            pid,
                            scheduleInput.text,
                            p2Check.checked
                        );
                        scheduleInput.text = "";
                        submitModal.close();
                    }
                }
            }
        }
    }

    ModalDialog {
        id: rescheduleModal
        objectName: "rescheduleJobModal"
        title: "Reschedule Job"
        width: 440
        height: 280

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 14

            Text {
                text: "Reschedule Conversion Job #" + root.targetJobId
                color: "#F9FAFB"
                font.pixelSize: 16
                font.bold: true
            }

            TextField {
                id: newSchedTimeInput
                Layout.fillWidth: true
                placeholderText: "New Scheduled ISO UTC (e.g. 2026-09-02T10:00:00Z)"
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Button {
                    text: "+1 Hour"
                    onClicked: {
                        var d = new Date(Date.now() + 3600 * 1000);
                        newSchedTimeInput.text = d.toISOString();
                    }
                }
                Button {
                    text: "+4 Hours"
                    onClicked: {
                        var d = new Date(Date.now() + 4 * 3600 * 1000);
                        newSchedTimeInput.text = d.toISOString();
                    }
                }
                Button {
                    text: "+1 Day"
                    onClicked: {
                        var d = new Date(Date.now() + 24 * 3600 * 1000);
                        newSchedTimeInput.text = d.toISOString();
                    }
                }
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 16

                Button {
                    text: "Cancel"
                    onClicked: rescheduleModal.close()
                }

                Button {
                    text: "Apply Schedule"
                    highlighted: true
                    onClicked: {
                        jobController.reschedule_job(root.targetJobId, newSchedTimeInput.text);
                        newSchedTimeInput.text = "";
                        rescheduleModal.close();
                    }
                }
            }
        }
    }
}
