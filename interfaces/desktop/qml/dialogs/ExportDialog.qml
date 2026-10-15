import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ModalDialog {
    id: root
    objectName: "exportDialog"
    title: "Export Document"
    width: 440
    height: 300

    property int jobId: 0
    property int version: 0

    signal exportSelected(string exportType)

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 14

        Text {
            text: "Export Document"
            color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "white"
            font.pixelSize: 16
            font.bold: true
        }

        Text {
            text: "Select the desired export format:"
            color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "gray"
            font.pixelSize: 13
        }

        ButtonGroup {
            id: formatGroup
        }

        RadioButton {
            id: pdfRadio
            objectName: "exportPdfRadio"
            text: "Publication-Grade PDF (.pdf)"
            checked: true
            ButtonGroup.group: formatGroup
        }

        RadioButton {
            id: mdRadio
            objectName: "exportMarkdownRadio"
            text: "Standalone Markdown (.md)"
            ButtonGroup.group: formatGroup
        }

        RadioButton {
            id: pkgRadio
            objectName: "exportPackageRadio"
            text: "Document Package with Assets (.zip)"
            ButtonGroup.group: formatGroup
        }

        Item { Layout.fillHeight: true }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Button {
                text: "Cancel"
                onClicked: root.close()
            }

            Item { Layout.fillWidth: true }

            Button {
                id: confirmExportBtn
                objectName: "confirmExportButton"
                text: "Export"
                highlighted: true
                onClicked: {
                    var selectedType = "pdf";
                    if (mdRadio.checked) selectedType = "markdown";
                    else if (pkgRadio.checked) selectedType = "package";
                    root.exportSelected(selectedType);
                    if (typeof exportController !== "undefined" && exportController && root.jobId > 0) {
                        exportController.requestExport(root.jobId, selectedType, root.version);
                    }
                    root.close();
                }
            }
        }
    }
}
