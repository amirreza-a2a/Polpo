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
                text: "BYOK API Key Management"
                color: "#F9FAFB"
                font.pixelSize: 20
                font.bold: true
                anchors.verticalCenter: parent.verticalCenter
            }

            Item { width: 1; height: 1; anchors.fill: parent }

            Button {
                text: "+ Add API Key"
                onClicked: addKeyModal.open()
            }
        }

        ListView {
            id: slotList
            width: parent.width
            height: parent.height - 80
            clip: true
            spacing: 12
            model: apiSlotModel

            delegate: Card {
                width: slotList.width
                height: 70

                Row {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 16

                    Column {
                        width: parent.width - 200
                        spacing: 4
                        anchors.verticalCenter: parent.verticalCenter

                        Row {
                            spacing: 10
                            Text {
                                text: model.label
                                color: "#F9FAFB"
                                font.pixelSize: 15
                                font.bold: true
                            }
                            Text {
                                text: "(" + model.provider.toUpperCase() + ")"
                                color: "#9CA3AF"
                                font.pixelSize: 12
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                        Text {
                            text: model.selectedModel ? ("Model: " + model.selectedModel) : "Default Model"
                            color: "#6B7280"
                            font.pixelSize: 11
                        }
                    }

                    Row {
                        spacing: 8
                        anchors.verticalCenter: parent.verticalCenter

                        Button {
                            text: "Test"
                            onClicked: apiKeyController.test_key(model.id)
                        }

                        Button {
                            text: "Delete"
                            onClicked: apiKeyController.delete_key(model.id)
                        }
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                text: "No API keys registered. Add a provider key to enable OCR inference."
                color: "#6B7280"
                font.pixelSize: 14
                visible: slotList.count === 0
            }
        }
    }

    ModalDialog {
        id: addKeyModal
        title: "Register BYOK Key"

        Column {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                text: "Register Provider Key"
                color: "#F9FAFB"
                font.pixelSize: 16
                font.bold: true
            }

            TextField {
                id: providerInput
                width: parent.width
                placeholderText: "Provider (e.g. google, openai)"
            }

            TextField {
                id: labelInput
                width: parent.width
                placeholderText: "Slot Label (e.g. Work Gemini Key)"
            }

            TextField {
                id: keyInput
                width: parent.width
                placeholderText: "API Secret Key"
                echoMode: TextInput.Password
            }

            TextField {
                id: modelInput
                width: parent.width
                placeholderText: "Selected Model (optional)"
            }

            Row {
                spacing: 12
                anchors.horizontalCenter: parent.horizontalCenter

                Button {
                    text: "Cancel"
                    onClicked: addKeyModal.close()
                }

                Button {
                    text: "Save Key"
                    onClicked: {
                        apiKeyController.register_key(
                            providerInput.text,
                            labelInput.text,
                            keyInput.text,
                            modelInput.text,
                            ""
                        );
                        keyInput.text = "";
                        addKeyModal.close();
                    }
                }
            }
        }
    }
}
