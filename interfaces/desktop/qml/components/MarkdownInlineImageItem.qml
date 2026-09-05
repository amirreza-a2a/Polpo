// ============================================================
//  interfaces/desktop/qml/components/MarkdownInlineImageItem.qml
//  Interactive Inline Visual Region Image Thumbnail for QML Flow
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: inlineImageRoot
    objectName: "markdownInlineImageItem"

    property var imageRef: null
    property string occurrenceId: imageRef ? (imageRef.occurrenceId || "") : ""
    property string regionId: imageRef ? (imageRef.regionId || "") : ""
    property string imageUri: imageRef ? (imageRef.imageUri || "") : ""
    property string altText: imageRef ? (imageRef.altText || "") : ""
    property int displayOrder: imageRef ? (imageRef.displayOrder || 0) : 0
    property bool isAssociated: imageRef ? (imageRef.isAssociated || false) : false
    property real scaleFactor: 1.0
    property var controller: null

    readonly property bool isHighlighted: controller ? (
        (controller.highlightedOccurrenceId !== "" && controller.highlightedOccurrenceId === occurrenceId) ||
        (controller.highlightedRegionId !== "" && controller.highlightedRegionId === regionId)
    ) : false

    width: Math.max(80, Math.min(240, 160 * scaleFactor))
    height: Math.max(60, Math.min(180, 110 * scaleFactor))
    radius: 4
    color: "#18181f"
    border.width: isHighlighted ? 2 : 1
    border.color: isHighlighted ? "#3b82f6" : (mouseArea.containsMouse ? "#60a5fa" : "#3a3a48")

    clip: true

    Image {
        id: thumbnailImage
        anchors.fill: parent
        anchors.margins: 4
        source: inlineImageRoot.imageUri
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        smooth: true
        visible: status === Image.Ready
    }

    // Fallback indicator when image cannot be loaded
    Rectangle {
        anchors.fill: parent
        anchors.margins: 4
        color: "#24242e"
        visible: thumbnailImage.status === Image.Error || !inlineImageRoot.imageUri

        ColumnLayout {
            anchors.centerIn: parent
            spacing: 2

            Text {
                text: "🖼"
                font.pixelSize: 16
                Layout.alignment: Qt.AlignHCenter
            }

            Text {
                text: inlineImageRoot.altText || "Missing Image"
                color: "#9ca3af"
                font.pixelSize: 10
                elide: Text.ElideRight
                Layout.maximumWidth: inlineImageRoot.width - 16
                Layout.alignment: Qt.AlignHCenter
            }
        }
    }

    // Display order badge (#1, #2, ...) for associated regions
    Rectangle {
        visible: inlineImageRoot.isAssociated && inlineImageRoot.displayOrder > 0
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: 4
        height: 18
        width: Math.max(22, badgeText.implicitWidth + 8)
        radius: 3
        color: isHighlighted ? "#2563eb" : "#1d4ed8"

        Text {
            id: badgeText
            anchors.centerIn: parent
            text: "#" + inlineImageRoot.displayOrder
            color: "#ffffff"
            font.pixelSize: 10
            font.bold: true
        }
    }

    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            if (controller && inlineImageRoot.regionId) {
                controller.selectRegion(inlineImageRoot.regionId, inlineImageRoot.occurrenceId)
            }
        }

        ToolTip.visible: mouseArea.containsMouse && (inlineImageRoot.altText !== "" || inlineImageRoot.isAssociated)
        ToolTip.delay: 300
        ToolTip.text: (inlineImageRoot.isAssociated ? "Region #" + inlineImageRoot.displayOrder + ": " : "") + (inlineImageRoot.altText || "Visual Crop")
    }
}
