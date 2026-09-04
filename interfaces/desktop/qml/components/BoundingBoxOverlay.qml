// ============================================================
//  interfaces/desktop/qml/components/BoundingBoxOverlay.qml
//  Read-Only Visual Bounding Box Overlay for Document Review
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15

Item {
    id: overlayRoot
    objectName: "boundingBoxOverlay"
    anchors.fill: parent

    property var controller: null
    property string fitMode: "preserve_aspect_fit"

    // Trigger re-computation on dimensions change or active regions change
    property var overlayItems: {
        if (!controller || width <= 0 || height <= 0) return [];
        return controller.getOverlayRects(width, height, fitMode);
    }

    Repeater {
        model: overlayRoot.overlayItems

        delegate: Rectangle {
            id: boxRect
            x: modelData.x
            y: modelData.y
            width: modelData.width
            height: modelData.height

            // Color coding based on origin and review status
            property color boxColor: {
                if (modelData.review_status === "accepted") return "#2a9d8f";
                if (modelData.is_modified || modelData.review_status === "modified") return "#f77f00";
                if (modelData.origin === "user_manual") return "#9d4edd";
                return "#00b4d8"; // AI detected default
            }

            color: Qt.rgba(boxColor.r, boxColor.g, boxColor.b, 0.15)
            border.color: boxColor
            border.width: 2
            radius: 2

            // Display Order badge
            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.margins: 2
                height: 16
                width: Math.max(16, badgeText.implicitWidth + 8)
                radius: 3
                color: boxRect.boxColor

                Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: "#" + modelData.display_order
                    color: "white"
                    font.pixelSize: 10
                    font.bold: true
                }
            }

            // Hover tooltip showing region identity & coordinates
            MouseArea {
                id: hoverArea
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.NoButton

                ToolTip.visible: hovered
                ToolTip.delay: 300
                ToolTip.text: "Region ID: " + modelData.region_id + "\n" +
                              "Origin: " + modelData.origin + "\n" +
                              "Status: " + modelData.review_status + "\n" +
                              "BBox: [" + modelData.effective_ymin + ", " + modelData.effective_xmin +
                              ", " + modelData.effective_ymax + ", " + modelData.effective_xmax + "]"
            }
        }
    }
}
