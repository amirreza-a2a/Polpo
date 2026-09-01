import QtQuick
import QtQuick.Controls
import "../components"

Item {
    id: root
    property string extractedText: ""

    Column {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 16

        Text {
            text: "Quick Convert (Single-Image OCR)"
            color: "#F9FAFB"
            font.pixelSize: 20
            font.bold: true
        }

        Card {
            width: parent.width
            height: 120

            Column {
                anchors.centerIn: parent
                spacing: 8

                Text {
                    text: "Drag and drop an image (PNG, JPG, WebP) or click to browse"
                    color: "#9CA3AF"
                    font.pixelSize: 14
                    anchors.horizontalCenter: parent.horizontalCenter
                }

                Button {
                    text: "Select Image File"
                    anchors.horizontalCenter: parent.horizontalCenter
                    onClicked: {
                        // File picker invocation
                    }
                }
            }
        }

        Text {
            text: "Extracted Markdown Output"
            color: "#E5E7EB"
            font.pixelSize: 16
            font.bold: true
        }

        TextArea {
            id: resultArea
            width: parent.width
            height: parent.height - 240
            text: root.extractedText
            readOnly: true
            color: "#F3F4F6"
            background: Rectangle {
                color: "#111827"
                border.color: "#374151"
                radius: 6
            }
        }
    }
}
