import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Item {
    id: root
    property int editingSlotId: 0
    property int testingSlotId: 0
    property string statusNotice: ""

    Connections {
        target: apiKeyController
        function onSlots_changed() {
            root.statusNotice = "API slots updated successfully.";
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
                text: "BYOK API Key Management"
                color: "#F9FAFB"
                font.pixelSize: 20
                font.bold: true
            }

            Item { Layout.fillWidth: true }

            Text {
                text: root.statusNotice
                color: "#10B981"
                font.pixelSize: 12
                visible: root.statusNotice !== ""
            }

            Button {
                id: addApiKeyButton
                objectName: "addApiKeyButton"
                text: "+ Add API Key"
                highlighted: true
                onClicked: addKeyModal.open()
            }
        }

        ListView {
            id: slotList
            objectName: "apiKeySlotList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 12
            model: typeof apiSlotModel !== "undefined" ? apiSlotModel : null

            delegate: Card {
                width: ListView.view.width
                height: 80

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 16

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        RowLayout {
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
                                implicitWidth: provText.implicitWidth + 12
                                implicitHeight: 20
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
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        spacing: 8

                        Button {
                            text: root.testingSlotId === model.id ? "Testing…" : "Test"
                            enabled: root.testingSlotId === 0
                            onClicked: {
                                root.testingSlotId = model.id;
                                root.statusNotice = "Verifying provider connection for '" + model.label + "'...";
                                var ok = apiKeyController.test_key(model.id);
                                root.statusNotice = ok ? ("Key '" + model.label + "' verified OK.") : ("Verification failed for '" + model.label + "'.");
                                root.testingSlotId = 0;
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
                text: "No API keys configured. Click '+ Add API Key' to add an AI provider."
                color: "#6B7280"
                font.pixelSize: 14
                visible: slotList.count === 0
            }
        }
    }

    ModalDialog {
        id: addKeyModal
        objectName: "addKeyModal"
        title: "Register BYOK Key"
        width: 480
        height: 420

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                text: "Register Provider API Key"
                color: "#F9FAFB"
                font.pixelSize: 16
                font.bold: true
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Provider:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                ComboBox {
                    id: providerCombo
                    Layout.fillWidth: true
                    model: (typeof apiKeyController !== "undefined" && apiKeyController) ? apiKeyController.get_supported_providers() : ["google", "openai", "anthropic", "openrouter", "ollama"]
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Slot Label:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                TextField {
                    id: labelInput
                    Layout.fillWidth: true
                    placeholderText: "e.g. My OpenAI GPT-4o Key"
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "API Key:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                TextField {
                    id: keyInput
                    Layout.fillWidth: true
                    placeholderText: "Secret Key (stored in OS Keyring)"
                    echoMode: TextInput.Password
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Model Name:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                TextField {
                    id: modelInput
                    Layout.fillWidth: true
                    placeholderText: "Optional (e.g. gpt-4o, gemini-2.0-flash)"
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Base URL:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                TextField {
                    id: baseUrlInput
                    Layout.fillWidth: true
                    placeholderText: "Optional custom endpoint URL"
                }
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 16

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
        objectName: "editKeyModal"
        title: "Edit API Key Slot"
        width: 480
        height: 400

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                text: "Edit API Key Slot #" + root.editingSlotId
                color: "#F9FAFB"
                font.pixelSize: 16
                font.bold: true
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Slot Label:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                TextField {
                    id: editLabelInput
                    Layout.fillWidth: true
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "New Key:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                TextField {
                    id: editKeyInput
                    Layout.fillWidth: true
                    placeholderText: "Leave blank to keep existing secret"
                    echoMode: TextInput.Password
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Model Name:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                TextField {
                    id: editModelInput
                    Layout.fillWidth: true
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Base URL:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                TextField {
                    id: editBaseUrlInput
                    Layout.fillWidth: true
                }
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 16

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
