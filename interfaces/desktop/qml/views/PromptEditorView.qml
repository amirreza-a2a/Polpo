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
                text: "System Prompts"
                color: "#F9FAFB"
                font.pixelSize: 20
                font.bold: true
                anchors.verticalCenter: parent.verticalCenter
            }

            Item { width: 1; height: 1; anchors.fill: parent }

            Button {
                text: "+ New Prompt"
                onClicked: {
                    // New prompt dialog
                }
            }
        }

        ListView {
            id: promptList
            width: parent.width
            height: parent.height - 80
            clip: true
            spacing: 10
            model: promptListModel

            delegate: Card {
                width: promptList.width
                height: 80

                Row {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 16

                    Column {
                        width: parent.width - 200
                        spacing: 4
                        anchors.verticalCenter: parent.verticalCenter

                        Row {
                            spacing: 8
                            Text {
                                text: model.name
                                color: "#F9FAFB"
                                font.pixelSize: 14
                                font.bold: true
                            }
                            Text {
                                text: model.isDefault ? "[DEFAULT]" : ""
                                color: "#34D399"
                                font.pixelSize: 11
                                font.bold: true
                            }
                        }
                        Text {
                            text: model.text
                            color: "#9CA3AF"
                            font.pixelSize: 12
                            elide: Text.ElideRight
                            width: parent.width
                        }
                    }

                    Row {
                        spacing: 8
                        anchors.verticalCenter: parent.verticalCenter

                        Button {
                            text: "Set Default"
                            visible: !model.isDefault
                            onClicked: promptController.set_default(model.id, model.promptType)
                        }

                        Button {
                            text: "Delete"
                            onClicked: promptController.delete_prompt(model.id)
                        }
                    }
                }
            }
        }
    }
}
