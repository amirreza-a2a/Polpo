import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

Item {
    id: root
    property int editingPromptId: 0
    property string activeFilter: ""

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            Text {
                text: "System Prompts"
                color: "#F9FAFB"
                font.pixelSize: 20
                font.bold: true
            }

            RowLayout {
                spacing: 8

                Button {
                    text: "All"
                    highlighted: root.activeFilter === ""
                    onClicked: {
                        root.activeFilter = "";
                        promptListModel.reload_prompts("");
                    }
                }
                Button {
                    text: "Pipeline 1 (OCR)"
                    highlighted: root.activeFilter === "pipeline1"
                    onClicked: {
                        root.activeFilter = "pipeline1";
                        promptListModel.reload_prompts("pipeline1");
                    }
                }
                Button {
                    text: "Pipeline 2 (Refinement)"
                    highlighted: root.activeFilter === "pipeline2"
                    onClicked: {
                        root.activeFilter = "pipeline2";
                        promptListModel.reload_prompts("pipeline2");
                    }
                }
            }

            Item { Layout.fillWidth: true }

            Button {
                id: newPromptBtn
                objectName: "newPromptButton"
                text: "+ New Prompt"
                highlighted: true
                onClicked: createPromptModal.open()
            }
        }

        ListView {
            id: promptList
            objectName: "promptList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 10
            model: typeof promptListModel !== "undefined" ? promptListModel : null

            delegate: Card {
                width: ListView.view.width
                height: 84

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 16

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        RowLayout {
                            spacing: 8
                            Text {
                                text: model.name
                                color: "#F9FAFB"
                                font.pixelSize: 15
                                font.bold: true
                            }
                            Rectangle {
                                color: "#1E3A8A"
                                radius: 4
                                implicitWidth: typeText.implicitWidth + 12
                                implicitHeight: 18
                                Text {
                                    id: typeText
                                    anchors.centerIn: parent
                                    text: model.promptType.toUpperCase()
                                    color: "#93C5FD"
                                    font.pixelSize: 10
                                    font.bold: true
                                }
                            }
                            Text {
                                text: model.isDefault ? "★ DEFAULT" : ""
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
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        spacing: 8

                        Button {
                            text: "Edit"
                            onClicked: {
                                root.editingPromptId = model.id;
                                editNameInput.text = model.name;
                                editTextInput.text = model.text;
                                editDefaultCheck.checked = model.isDefault;
                                editPromptModal.open();
                            }
                        }

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

    ModalDialog {
        id: createPromptModal
        objectName: "createPromptModal"
        title: "Create System Prompt"
        width: 520
        height: 440

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                text: "Create New Prompt Template"
                color: "#F9FAFB"
                font.pixelSize: 16
                font.bold: true
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Prompt Name:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                TextField {
                    id: createNameInput
                    Layout.fillWidth: true
                    placeholderText: "e.g. Persian Technical OCR"
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Pipeline Type:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                ComboBox {
                    id: createTypeCombo
                    Layout.fillWidth: true
                    model: ["pipeline1", "pipeline2"]
                }
            }

            Text {
                text: "Prompt Text:"
                color: "#D1D5DB"
                font.pixelSize: 13
            }

            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 120
                clip: true

                TextArea {
                    id: createTextInput
                    width: parent.width
                    wrapMode: TextArea.Wrap
                    placeholderText: "Enter AI system instructions..."
                    color: "#F3F4F6"
                    background: Rectangle {
                        color: "#111827"
                        border.color: "#374151"
                        radius: 6
                    }
                }
            }

            CheckBox {
                id: createDefaultCheck
                text: "Set as default for this pipeline"
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 16

                Button {
                    text: "Cancel"
                    onClicked: createPromptModal.close()
                }

                Button {
                    text: "Create Prompt"
                    highlighted: true
                    onClicked: {
                        promptController.create_prompt(
                            createNameInput.text,
                            createTextInput.text,
                            createTypeCombo.currentText,
                            createDefaultCheck.checked
                        );
                        createNameInput.text = "";
                        createTextInput.text = "";
                        createPromptModal.close();
                    }
                }
            }
        }
    }

    ModalDialog {
        id: editPromptModal
        objectName: "editPromptModal"
        title: "Edit Prompt"
        width: 520
        height: 400

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                text: "Edit Prompt Template #" + root.editingPromptId
                color: "#F9FAFB"
                font.pixelSize: 16
                font.bold: true
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    text: "Prompt Name:"
                    color: "#D1D5DB"
                    Layout.preferredWidth: 100
                }
                TextField {
                    id: editNameInput
                    Layout.fillWidth: true
                }
            }

            Text {
                text: "Prompt Text:"
                color: "#D1D5DB"
                font.pixelSize: 13
            }

            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: 120
                clip: true

                TextArea {
                    id: editTextInput
                    width: parent.width
                    wrapMode: TextArea.Wrap
                    color: "#F3F4F6"
                    background: Rectangle {
                        color: "#111827"
                        border.color: "#374151"
                        radius: 6
                    }
                }
            }

            CheckBox {
                id: editDefaultCheck
                text: "Set as default"
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 16

                Button {
                    text: "Cancel"
                    onClicked: editPromptModal.close()
                }

                Button {
                    text: "Update Prompt"
                    highlighted: true
                    onClicked: {
                        promptController.update_prompt(
                            root.editingPromptId,
                            editNameInput.text,
                            editTextInput.text,
                            editDefaultCheck.checked
                        );
                        editPromptModal.close();
                    }
                }
            }
        }
    }
}
