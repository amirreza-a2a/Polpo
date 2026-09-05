// ============================================================
//  interfaces/desktop/qml/components/MarkdownImageCard.qml
//  Standalone Visual Region Image Card Component
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: imageCardRoot
    objectName: "markdownImageCard"

    property string imageUri: ""
    property string altText: ""
    property string regionId: ""
    property string occurrenceId: ""
    property int displayOrder: 0
    property bool isAssociated: false
    property real scaleFactor: 1.0
    property var controller: null

    readonly property bool isHighlighted: controller ? (
        controller.highlightedOccurrenceId !== "" ?
            (controller.highlightedOccurrenceId === occurrenceId) :
            (controller.highlightedRegionId !== "" && controller.highlightedRegionId === regionId)
    ) : false

    implicitWidth: Math.min(680, Math.max(300, 560 * scaleFactor))
    implicitHeight: cardLayout.implicitHeight + 20
    radius: 6
    color: "#1a1a22"
    border.width: isHighlighted ? 2 : 1
    border.color: isHighlighted ? "#3b82f6" : (cardMouseArea.containsMouse ? "#4b5563" : "#2a2a35")

    ColumnLayout {
        id: cardLayout
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        // Header with Region Order Badge and Title
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Rectangle {
                visible: imageCardRoot.isAssociated && imageCardRoot.displayOrder > 0
                height: 22
                width: Math.max(28, orderText.implicitWidth + 12)
                radius: 4
                color: isHighlighted ? "#2563eb" : "#1d4ed8"

                Text {
                    id: orderText
                    anchors.centerIn: parent
                    text: "#" + imageCardRoot.displayOrder
                    color: "#ffffff"
                    font.pixelSize: 11
                    font.bold: true
                }
            }

            Text {
                text: imageCardRoot.altText || (imageCardRoot.isAssociated ? "Visual Region Crop" : "Image")
                color: isHighlighted ? "#93c5fd" : "#d1d5db"
                font.pixelSize: 12
                font.bold: true
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }

        // Image Display Area
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(420 * scaleFactor, 500)
            color: "#121216"
            radius: 4
            clip: true

            Image {
                id: mainImage
                anchors.fill: parent
                anchors.margins: 6
                source: imageCardRoot.imageUri
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                smooth: true
                visible: status === Image.Ready
            }

            // Error Fallback
            ColumnLayout {
                anchors.centerIn: parent
                visible: mainImage.status === Image.Error || !imageCardRoot.imageUri
                spacing: 4

                Text {
                    text: "⚠️"
                    font.pixelSize: 24
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: "Image file could not be loaded"
                    color: "#ef4444"
                    font.pixelSize: 12
                    font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: imageCardRoot.imageUri || imageCardRoot.altText
                    color: "#6b7280"
                    font.pixelSize: 10
                    elide: Text.ElideMiddle
                    Layout.maximumWidth: parent.width - 20
                    Layout.alignment: Qt.AlignHCenter
                }
            }

            // Loading Indicator
            BusyIndicator {
                anchors.centerIn: parent
                running: mainImage.status === Image.Loading
                visible: running
            }
        }
    }

    MouseArea {
        id: cardMouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: imageCardRoot.isAssociated ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: {
            if (controller && imageCardRoot.regionId) {
                controller.selectRegion(imageCardRoot.regionId, imageCardRoot.occurrenceId)
            }
        }
    }
}
