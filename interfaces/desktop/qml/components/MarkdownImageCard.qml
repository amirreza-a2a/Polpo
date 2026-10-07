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
    color: (typeof theme !== "undefined" && theme) ? theme.surfaceElevated : "black"
    border.width: isHighlighted ? 2 : 1
    border.color: (typeof theme !== "undefined" && theme) ? (isHighlighted ? theme.accent : (cardMouseArea.containsMouse ? theme.borderStrong : theme.border)) : "gray"

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
                color: (typeof theme !== "undefined" && theme) ? (isHighlighted ? theme.accentActive : theme.accent) : "blue"

                Text {
                    id: orderText
                    anchors.centerIn: parent
                    text: "#" + imageCardRoot.displayOrder
                    color: (typeof theme !== "undefined" && theme) ? theme.accentText : "white"
                    font.pixelSize: 11
                    font.bold: true
                }
            }

            Text {
                text: imageCardRoot.altText || (imageCardRoot.isAssociated ? "Visual Region Crop" : "Image")
                color: (typeof theme !== "undefined" && theme) ? (isHighlighted ? theme.accentHover : theme.textPrimary) : "white"
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
            color: (typeof theme !== "undefined" && theme) ? theme.surfaceSunken : "black"
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
                    color: (typeof theme !== "undefined" && theme) ? theme.error : "red"
                    font.pixelSize: 12
                    font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: imageCardRoot.imageUri || imageCardRoot.altText
                    color: (typeof theme !== "undefined" && theme) ? theme.textMuted : "gray"
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
