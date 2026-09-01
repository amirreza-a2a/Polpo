import QtQuick
import QtQuick.Controls
import "../components"

Item {
    id: root

    Column {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        Row {
            width: parent.width
            spacing: 16

            Text {
                text: "Conversion History"
                color: "#F9FAFB"
                font.pixelSize: 20
                font.bold: true
                anchors.verticalCenter: parent.verticalCenter
            }

            Item { width: 1; height: 1; anchors.fill: parent }

            Text {
                text: "Total: " + (typeof jobHistoryModel !== "undefined" && jobHistoryModel ? jobHistoryModel.totalJobs : 0) + " jobs"
                color: "#9CA3AF"
                font.pixelSize: 13
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        ListView {
            id: historyList
            width: parent.width
            height: parent.height - 110
            clip: true
            spacing: 8
            model: jobHistoryModel

            delegate: Card {
                width: historyList.width
                height: 64

                Row {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 16

                    Column {
                        width: parent.width - 280
                        spacing: 4
                        anchors.verticalCenter: parent.verticalCenter

                        Row {
                            spacing: 10
                            Text {
                                text: model.fileName
                                color: "#F9FAFB"
                                font.pixelSize: 14
                                font.bold: true
                                elide: Text.ElideRight
                                width: 220
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

                    Row {
                        spacing: 8
                        anchors.verticalCenter: parent.verticalCenter

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

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 16

            Button {
                text: "◀ Previous"
                enabled: typeof jobHistoryModel !== "undefined" && jobHistoryModel ? (jobHistoryModel.currentPage > 1) : false
                onClicked: if (typeof jobHistoryModel !== "undefined" && jobHistoryModel) jobHistoryModel.prev_page()
            }

            Text {
                text: "Page " + (typeof jobHistoryModel !== "undefined" && jobHistoryModel ? jobHistoryModel.currentPage : 1) + " of " + (typeof jobHistoryModel !== "undefined" && jobHistoryModel ? jobHistoryModel.totalPages : 1)
                color: "#D1D5DB"
                font.pixelSize: 13
                anchors.verticalCenter: parent.verticalCenter
            }

            Button {
                text: "Next ▶"
                enabled: typeof jobHistoryModel !== "undefined" && jobHistoryModel ? (jobHistoryModel.currentPage < jobHistoryModel.totalPages) : false
                onClicked: if (typeof jobHistoryModel !== "undefined" && jobHistoryModel) jobHistoryModel.next_page()
            }
        }
    }
}
