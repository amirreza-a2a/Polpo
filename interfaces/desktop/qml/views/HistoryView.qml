import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Item {
    id: root

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            Text {
                text: "Conversion History"
                color: "#F9FAFB"
                font.pixelSize: 20
                font.bold: true
            }

            Item { Layout.fillWidth: true }

            Text {
                text: "Total: " + (typeof jobHistoryModel !== "undefined" && jobHistoryModel ? jobHistoryModel.totalJobs : 0) + " jobs"
                color: "#9CA3AF"
                font.pixelSize: 13
            }
        }

        ListView {
            id: historyList
            objectName: "jobHistoryList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 8
            model: typeof jobHistoryModel !== "undefined" ? jobHistoryModel : null

            delegate: Card {
                width: ListView.view.width
                height: 68

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 16

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        RowLayout {
                            spacing: 10
                            Text {
                                text: model.fileName
                                color: "#F9FAFB"
                                font.pixelSize: 14
                                font.bold: true
                                elide: Text.ElideRight
                                Layout.maximumWidth: 260
                            }
                            StatusBadge {
                                status: model.status
                            }
                        }
                        Text {
                            text: model.createdAt + " • " + model.totalPages + " pages"
                            color: "#9CA3AF"
                            font.pixelSize: 11
                        }
                    }

                    RowLayout {
                        spacing: 8

                        Button {
                            text: "Open Markdown"
                            visible: model.status === "done"
                            onClicked: jobController.open_artifact_default(model.id)
                        }

                        Button {
                            text: "Reveal Folder"
                            onClicked: jobController.reveal_artifact_in_explorer(model.id)
                        }

                        Button {
                            text: "Retry"
                            visible: model.status === "failed"
                            onClicked: jobController.retry_job(model.id)
                        }
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                text: "No historical conversions found."
                color: "#6B7280"
                font.pixelSize: 14
                visible: historyList.count === 0
            }
        }

        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            spacing: 16

            Button {
                id: historyPrevBtn
                objectName: "historyPrevButton"
                text: "◀ Previous"
                enabled: typeof jobHistoryModel !== "undefined" && jobHistoryModel ? (jobHistoryModel.currentPage > 1) : false
                onClicked: if (typeof jobHistoryModel !== "undefined" && jobHistoryModel) jobHistoryModel.prev_page()
            }

            Text {
                text: "Page " + (typeof jobHistoryModel !== "undefined" && jobHistoryModel ? jobHistoryModel.currentPage : 1) + " of " + (typeof jobHistoryModel !== "undefined" && jobHistoryModel ? jobHistoryModel.totalPages : 1)
                color: "#D1D5DB"
                font.pixelSize: 13
            }

            Button {
                id: historyNextBtn
                objectName: "historyNextButton"
                text: "Next ▶"
                enabled: typeof jobHistoryModel !== "undefined" && jobHistoryModel ? (jobHistoryModel.currentPage < jobHistoryModel.totalPages) : false
                onClicked: if (typeof jobHistoryModel !== "undefined" && jobHistoryModel) jobHistoryModel.next_page()
            }
        }
    }
}
