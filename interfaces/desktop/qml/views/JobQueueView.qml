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
                text: "Active Conversion Queue"
                color: "#F9FAFB"
                font.pixelSize: 20
                font.bold: true
                anchors.verticalCenter: parent.verticalCenter
            }

            Item { width: 1; height: 1; anchors.fill: parent } // Spacer

            Button {
                text: "+ Submit Document"
                onClicked: {
                    // Triggers file submission flow
                }
            }
        }

        ListView {
            id: queueList
            width: parent.width
            height: parent.height - 60
            clip: true
            spacing: 12
            model: jobQueueModel

            delegate: Card {
                width: queueList.width
                height: 90

                Row {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 16

                    Column {
                        width: parent.width - 240
                        spacing: 6
                        anchors.verticalCenter: parent.verticalCenter

                        Row {
                            spacing: 12
                            Text {
                                text: model.fileName
                                color: "#F9FAFB"
                                font.pixelSize: 15
                                font.bold: true
                                elide: Text.ElideRight
                                width: 250
                            }
                            StatusBadge {
                                status: model.status
                            }
                            Text {
                                text: model.activeApiLabel ? ("API: " + model.activeApiLabel) : ""
                                color: "#9CA3AF"
                                font.pixelSize: 12
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        ProgressBar {
                            width: parent.width
                            value: model.progressPercent
                        }

                        Text {
                            text: "Page " + model.processedPages + " of " + model.totalPages + " (" + Math.round(model.progressPercent) + "%)"
                            color: "#9CA3AF"
                            font.pixelSize: 12
                        }
                    }

                    Row {
                        spacing: 8
                        anchors.verticalCenter: parent.verticalCenter

                        Button {
                            text: "Cancel"
                            onClicked: jobController.cancel_job(model.id)
                        }

                        Button {
                            text: "Reschedule"
                            onClicked: {
                                // Reschedule slot
                            }
                        }
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                text: "No active jobs in queue. Submit a document to begin."
                color: "#6B7280"
                font.pixelSize: 14
                visible: queueList.count === 0
            }
        }
    }
}
