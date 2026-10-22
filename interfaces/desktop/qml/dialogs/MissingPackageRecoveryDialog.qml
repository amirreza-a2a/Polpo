import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ModalDialog {
    id: root
    objectName: "missingPackageRecoveryDialog"
    title: "Missing LaTeX Packages"
    width: 500
    height: 380

    property var missingPackages: []
    property bool canAutoRecover: false
    property string instructions: ""
    property int jobId: 0

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 14

        Text {
            text: "Missing LaTeX Packages"
            color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
            font.pixelSize: 16
            font.bold: true
        }

        Text {
            text: "PDF compilation requires the following LaTeX packages:"
            color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
            font.pixelSize: 13
        }

        // Missing package tags display
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 40
            color: (typeof theme !== "undefined" && theme) ? theme.surface : "transparent"
            border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
            border.width: 1
            radius: 4

            RowLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 8

                Text {
                    text: root.missingPackages.length > 0 ? root.missingPackages.join(", ") : "None specified"
                    color: (typeof theme !== "undefined" && theme) ? theme.accent : "transparent"
                    font.pixelSize: 13
                    font.bold: true
                    elide: Text.ElideRight
                }
            }
        }

        Text {
            text: root.canAutoRecover
                ? "Polpo can automatically install these packages into your local TinyTeX distribution."
                : "Automatic recovery is not available for your system TeX distribution. Please run the following command manually:"
            color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
            font.pixelSize: 13
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true

            TextArea {
                id: instructionsArea
                readOnly: true
                wrapMode: TextEdit.Wrap
                text: root.instructions
                selectByMouse: true
                color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                background: Rectangle {
                    color: (typeof theme !== "undefined" && theme) ? theme.surface : "transparent"
                    border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
                    border.width: 1
                    radius: 4
                }
            }
        }

        // Action Buttons
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Button {
                id: cancelBtn
                objectName: "recoveryCancelButton"
                text: "Cancel"
                onClicked: root.close()
            }

            Item { Layout.fillWidth: true }

            Button {
                id: installBtn
                objectName: "recoveryInstallButton"
                text: "Install Dependencies"
                highlighted: true
                visible: root.canAutoRecover
                enabled: !(typeof pdfSetupController !== "undefined" && pdfSetupController && pdfSetupController.isRecovering)
                onClicked: {
                    if (typeof pdfSetupController !== "undefined" && pdfSetupController) {
                        pdfSetupController.installMissingPackages(root.missingPackages);
                    }
                    root.close();
                }
            }
        }
    }
}
