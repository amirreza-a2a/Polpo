import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ModalDialog {
    id: root
    objectName: "pdfSetupAssistantDialog"
    title: "PDF Export Setup & Diagnostics"
    width: 520
    height: 440

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        Text {
            text: "PDF Export Setup & Environment"
            color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
            font.pixelSize: 16
            font.bold: true
        }

        // Status overview card
        Card {
            Layout.fillWidth: true
            Layout.preferredHeight: 120

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 6

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "Quarto CLI:"
                        color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                        font.pixelSize: 13
                        Layout.preferredWidth: 140
                    }
                    Text {
                        text: (typeof pdfSetupController !== "undefined" && pdfSetupController && pdfSetupController.quartoAvailable)
                            ? ("Available (" + pdfSetupController.quartoVersion + ")")
                            : "Not Detected"
                        color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                        font.pixelSize: 13
                        font.bold: true
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "XeLaTeX Engine:"
                        color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                        font.pixelSize: 13
                        Layout.preferredWidth: 140
                    }
                    Text {
                        text: (typeof pdfSetupController !== "undefined" && pdfSetupController && pdfSetupController.xelatexAvailable)
                            ? ("Available (" + (pdfSetupController.texDistribution || "System TeX") + ")")
                            : "Not Detected"
                        color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                        font.pixelSize: 13
                        font.bold: true
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "Overall Readiness:"
                        color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                        font.pixelSize: 13
                        Layout.preferredWidth: 140
                    }
                    Text {
                        text: (typeof pdfSetupController !== "undefined" && pdfSetupController && pdfSetupController.isExportReady)
                            ? "Ready for Publication PDF Export"
                            : "Configuration / Installation Required"
                        color: (typeof pdfSetupController !== "undefined" && pdfSetupController && pdfSetupController.isExportReady)
                            ? ((typeof theme !== "undefined" && theme) ? theme.successText : "transparent")
                            : ((typeof theme !== "undefined" && theme) ? theme.errorText : "transparent")
                        font.pixelSize: 13
                        font.bold: true
                    }
                }
            }
        }

        // Custom Quarto path row
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: "Custom Quarto Path:"
                color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                font.pixelSize: 13
            }

            TextField {
                id: customPathInput
                Layout.fillWidth: true
                placeholderText: "e.g. /usr/local/bin/quarto or C:\\quarto\\bin\\quarto.exe"
                text: typeof pdfSetupController !== "undefined" && pdfSetupController ? pdfSetupController.customQuartoPath : ""
                onAccepted: {
                    if (typeof pdfSetupController !== "undefined" && pdfSetupController) {
                        pdfSetupController.setCustomQuartoPath(text);
                    }
                }
            }

            Button {
                text: "Apply"
                onClicked: {
                    if (typeof pdfSetupController !== "undefined" && pdfSetupController) {
                        pdfSetupController.setCustomQuartoPath(customPathInput.text);
                    }
                }
            }
        }

        // Instructions text area
        Text {
            text: "Setup & Recovery Instructions:"
            color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
            font.pixelSize: 12
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true

            TextArea {
                id: instructionsArea
                readOnly: true
                wrapMode: TextEdit.Wrap
                text: (typeof pdfSetupController !== "undefined" && pdfSetupController)
                    ? pdfSetupController.setupInstructions
                    : "No instructions available."
                selectByMouse: true
                color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                background: Rectangle {
                    color: (typeof theme !== "undefined" && theme) ? theme.surface : "transparent"
                    border.color: (typeof theme !== "undefined" && theme) ? (instructionsArea.activeFocus ? theme.focusRing : theme.border) : "transparent"
                    border.width: 1
                    radius: 4
                }
            }
        }

        // Actions
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Button {
                text: "Re-check Environment"
                onClicked: {
                    if (typeof pdfSetupController !== "undefined" && pdfSetupController) {
                        pdfSetupController.probeEnvironment();
                    }
                }
            }

            Item { Layout.fillWidth: true }

            Button {
                text: "Close"
                onClicked: root.close()
            }
        }
    }
}
