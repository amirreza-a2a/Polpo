import QtQuick
import QtQuick.Controls
import "../components"

Item {
    id: root
    property int editingSlotId: 0
    property string statusNotice: ""

    Connections {
        target: apiKeyController
        function onSlots_changed() {
            root.statusNotice = "API slots updated successfully.";
        }
    }

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

            Text {
                text: root.statusNotice
                color: "#10B981"
                font.pixelSize: 12
                anchors.verticalCenter: parent.verticalCenter
            }

            Button {
                text: "+ Add API Key"
                highlighted: true
                onClicked: addKeyModal.open()
            }
        }

        ListView {
            id: slotList
            width: parent.width
            height: parent.height - 80
            clip: true
            spacing: 12
            model: typeof apiSlotModel !== "undefined" ? apiSlotModel : null

            delegate: Card {
                width: slotList.width
                height: 80

                Row {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 16

                    Column {
                        width: parent.width - 240
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
                            Rectangle {
                                color: "#374151"
                                radius: 4
                                width: provText.implicitWidth + 8
                                height: 20
                                anchors.verticalCenter: parent.verticalCenter
                                Text {
                                    id: provText
                                    anchors.centerIn: parent
                                    text: model.provider.toUpperCase()
                                    color: "#60A5FA"
                                    font.pixelSize: 11
                                    font.bold: true
                                }
                            }
                        }
                        Text {
                            text: (model.selectedModel ? ("Model: " + model.selectedModel) : "Default Model") + (model.baseUrl ? (" • URL: " + model.baseUrl) : "")
                            color: "#9CA3AF"
                            font.pixelSize: 11
                        }
                    }

                    Row {
                        spacing: 8
                        anchors.verticalCenter: parent.verticalCenter

                        Button {
                            text: "Test"
                            onClicked: {
                                var ok = apiKeyController.test_key(model.id);
                                root.statusNotice = ok ? ("Key '" + model.label + "' verified OK.") : ("Verification failed for '" + model.label + "'.");
                            }
                        }

                        Button {
                            text: "Edit"
                            onClicked: {
                                root.editingSlotId = model.id;
                                editLabelInput.text = model.label;
                                editModelInput.text = model.selectedModel;
                                editBaseUrlInput.text = model.baseUrl;
                                editKeyInput.text = "";
                                editKeyModal.open();
                            }
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
                text: "No API keys configured. Add an API key to enable OCR inference."
                color: "#6B7280"
                font.pixelSize: 14
                visible: slotList.count === 0
            }
        }
    }

    ModalDialog {
        id: addKeyModal
        title: "Register BYOK Key"
        width: 480
        height: 420

        Column {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                text: "Register Provider API Key"
                color: "#F9FAFB"
                font.pixelSize: 16
                font.bold: true
            }

            Row {
                spacing: 12
                Text {
                    text: "Provider:"
                    color: "#D1D5DB"
                    width: 100
                    anchors.verticalCenter: parent.verticalCenter
                }
                ComboBox {
                    id: providerCombo
                    width: 320
                    model: (typeof apiKeyController !== "undefined" && apiKeyController) ? apiKeyController.get_supported_providers() : ["google", "openai", "anthropic", "openrouter", "ollama"]
                }
            }

            Row {
                spacing: 12
                Text {
                    text: "Slot Label:"
                    color: "#D1D5DB"
                    width: 100
                    anchors.verticalCenter: parent.verticalCenter
                }
                TextField {
                    id: labelInput
                    width: 320
                    placeholderText: "e.g. My OpenAI GPT-4o Key"
                }
            }

            Row {
                spacing: 12
                Text {
                    text: "API Key:"
                    color: "#D1D5DB"
                    width: 100
                    anchors.verticalCenter: parent.verticalCenter
                }
                TextField {
                    id: keyInput
                    width: 320
                    placeholderText: "Secret Key (stored in OS Keyring)"
                    echoMode: TextInput.Password
                }
            }

            Row {
                spacing: 12
                Text {
                    text: "Model Name:"
                    color: "#D1D5DB"
                    width: 100
                    anchors.verticalCenter: parent.verticalCenter
                }
                TextField {
                    id: modelInput
                    width: 320
                    placeholderText: "Optional (e.g. gpt-4o, gemini-2.0-flash)"
                }
            }

            Row {
                spacing: 12
                Text {
                    text: "Base URL:"
                    color: "#D1D5DB"
                    width: 100
                    anchors.verticalCenter: parent.verticalCenter
                }
                TextField {
                    id: baseUrlInput
                    width: 320
                    placeholderText: "Optional custom endpoint URL"
                }
            }

            Row {
                spacing: 12
                anchors.horizontalCenter: parent.horizontalCenter

                Button {
                    text: "Cancel"
                    onClicked: {
                        keyInput.text = "";
                        addKeyModal.close();
                    }
                }

                Button {
                    text: "Save Key"
                    highlighted: true
                    onClicked: {
                        apiKeyController.register_key(
                            providerCombo.currentText,
                            labelInput.text,
                            keyInput.text,
                            modelInput.text,
                            baseUrlInput.text
                        );
                        keyInput.text = "";
                        labelInput.text = "";
                        modelInput.text = "";
                        baseUrlInput.text = "";
                        addKeyModal.close();
                    }
                }
            }
        }
    }

    ModalDialog {
        id: editKeyModal
        title: "Edit API Key Slot"
        width: 480
        height: 400

        Column {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                text: "Edit API Key Slot #" + root.editingSlotId
                color: "#F9FAFB"
                font.pixelSize: 16
                font.bold: true
            }

            Row {
                spacing: 12
                Text {
                    text: "Slot Label:"
                    color: "#D1D5DB"
                    width: 100
                    anchors.verticalCenter: parent.verticalCenter
                }
                TextField {
                    id: editLabelInput
                    width: 320
                }
            }

            Row {
                spacing: 12
                Text {
                    text: "New Key:"
                    color: "#D1D5DB"
                    width: 100
                    anchors.verticalCenter: parent.verticalCenter
                }
                TextField {
                    id: editKeyInput
                    width: 320
                    placeholderText: "Leave blank to keep existing secret"
                    echoMode: TextInput.Password
                }
            }

            Row {
                spacing: 12
                Text {
                    text: "Model Name:"
                    color: "#D1D5DB"
                    width: 100
                    anchors.verticalCenter: parent.verticalCenter
                }
                TextField {
                    id: editModelInput
                    width: 320
                }
            }

            Row {
                spacing: 12
                Text {
                    text: "Base URL:"
                    color: "#D1D5DB"
                    width: 100
                    anchors.verticalCenter: parent.verticalCenter
                }
                TextField {
                    id: editBaseUrlInput
                    width: 320
                }
            }

            Row {
                spacing: 12
                anchors.horizontalCenter: parent.horizontalCenter

                Button {
                    text: "Cancel"
                    onClicked: {
                        editKeyInput.text = "";
                        editKeyModal.close();
                    }
                }

                Button {
                    text: "Update Slot"
                    highlighted: true
                    onClicked: {
                        apiKeyController.update_key(
                            root.editingSlotId,
                            editLabelInput.text,
                            editKeyInput.text,
                            editModelInput.text,
                            editBaseUrlInput.text
                        );
                        editKeyInput.text = "";
                        editKeyModal.close();
                    }
                }
            }
        }
    }
}
