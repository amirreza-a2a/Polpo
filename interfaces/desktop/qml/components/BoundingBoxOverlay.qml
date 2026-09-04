// ============================================================
//  interfaces/desktop/qml/components/BoundingBoxOverlay.qml
//  Interactive Visual Bounding Box Overlay for Document Review
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15

Item {
    id: overlayRoot
    objectName: "boundingBoxOverlay"
    anchors.fill: parent

    property var controller: null
    property var viewportArea: null
    property string fitMode: "preserve_aspect_fit"

    // Converts local coordinates within an item to viewport space
    function mapToViewport(localX, localY, item) {
        if (overlayRoot.viewportArea) {
            return item.mapToItem(overlayRoot.viewportArea, localX, localY);
        }
        var itemPt = item.mapToItem(overlayRoot, localX, localY);
        var zoom = controller ? controller.zoom : 1.0;
        var panX = controller ? controller.panX : 0.0;
        var panY = controller ? controller.panY : 0.0;
        return { x: (itemPt.x * zoom) - panX, y: (itemPt.y * zoom) - panY };
    }

    // Trigger re-computation on dimensions change, active regions change, or transient editing
    property var overlayItems: {
        if (!controller || width <= 0 || height <= 0) return [];
        var _active = controller.activeRegions;
        var _sel = controller.selectedRegionId;
        var _trans = controller.transientBox;
        return controller.getOverlayRects(width, height, fitMode);
    }

    // Calculate displayed image bounds to distinguish real document surface from letterbox/pillarbox margins
    property var displayedRect: {
        if (!controller || width <= 0 || height <= 0) {
            return { x: 0, y: 0, width: width, height: height };
        }
        var _rw = controller.rasterWidth;
        var _rh = controller.rasterHeight;
        return controller.getDisplayedRect(width, height, fitMode);
    }

    // =========================================================================
    // Empty Page Area Interaction (Creation, Deselection, Pan/Zoom)
    // =========================================================================
    MouseArea {
        id: emptyArea
        objectName: "emptyArea"
        x: overlayRoot.displayedRect ? (overlayRoot.displayedRect.x || 0) : 0
        y: overlayRoot.displayedRect ? (overlayRoot.displayedRect.y || 0) : 0
        width: overlayRoot.displayedRect ? (overlayRoot.displayedRect.width || 0) : 0
        height: overlayRoot.displayedRect ? (overlayRoot.displayedRect.height || 0) : 0
        z: 0
        hoverEnabled: true
        cursorShape: (controller && controller.editorState === "creating") ? Qt.CrossCursor : Qt.ArrowCursor
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton

        property real lastPanX: 0
        property real lastPanY: 0
        property bool isPanning: false

        onPressed: function(mouse) {
            if (mouse.button === Qt.RightButton || mouse.button === Qt.MiddleButton) {
                isPanning = true;
                lastPanX = mouse.x;
                lastPanY = mouse.y;
            } else if (mouse.button === Qt.LeftButton && controller) {
                isPanning = false;
                var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, emptyArea);
                controller.startCreateManual(vpPt.x, vpPt.y);
            }
        }

        onPositionChanged: function(mouse) {
            if (isPanning && controller) {
                var dx = mouse.x - lastPanX;
                var dy = mouse.y - lastPanY;
                controller.panBy(-dx, -dy);
                lastPanX = mouse.x;
                lastPanY = mouse.y;
            } else if (pressed && controller && controller.editorState === "creating") {
                var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, emptyArea);
                controller.updateCreateManual(vpPt.x, vpPt.y);
            }
        }

        onReleased: function(mouse) {
            if (isPanning) {
                isPanning = false;
            } else if (mouse.button === Qt.LeftButton && controller && controller.editorState === "creating") {
                controller.commitCreateManual();
            }
        }

        onCanceled: function() {
            if (isPanning) {
                isPanning = false;
            } else if (controller && controller.editorState === "creating") {
                controller.cancelCreateManual();
            }
        }

        onWheel: function(wheel) {
            if (controller) {
                var factor = wheel.angleDelta.y > 0 ? 1.15 : (1.0 / 1.15);
                var vpPt = overlayRoot.mapToViewport(wheel.x, wheel.y, emptyArea);
                controller.zoomAt(controller.zoom * factor, vpPt.x, vpPt.y);
            }
        }
    }

    // =========================================================================
    // Active Visual Regions
    // =========================================================================
    Repeater {
        id: regionRepeater
        model: overlayRoot.overlayItems

        delegate: Rectangle {
            id: boxRect
            objectName: "boxRect_" + modelData.region_id
            x: modelData.x
            y: modelData.y
            width: modelData.width
            height: modelData.height
            z: modelData.is_selected ? 5 : 1

            property string regionId: modelData.region_id
            property bool isSelected: controller ? controller.selectedRegionId === regionId : (modelData.is_selected || false)

            // Color coding based on origin and review status
            property color boxColor: {
                if (isSelected) return "#00f0ff";
                if (modelData.review_status === "accepted") return "#2a9d8f";
                if (modelData.is_modified || modelData.review_status === "modified") return "#f77f00";
                if (modelData.origin === "user_manual") return "#9d4edd";
                return "#00b4d8"; // AI detected default
            }

            color: Qt.rgba(boxColor.r, boxColor.g, boxColor.b, isSelected ? 0.25 : 0.15)
            border.color: boxColor
            border.width: isSelected ? 2 : 1
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
                z: 2

                Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: "#" + modelData.display_order
                    color: boxRect.isSelected ? "#000000" : "#ffffff"
                    font.pixelSize: 10
                    font.bold: true
                }
            }

            // Region Body Interaction (Select, Drag, Tooltip)
            MouseArea {
                id: bodyDragArea
                objectName: "bodyDragArea_" + modelData.region_id
                anchors.fill: parent
                z: 1
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton
                cursorShape: boxRect.isSelected ? Qt.SizeAllCursor : Qt.PointingHandCursor

                ToolTip.visible: bodyDragArea.containsMouse && !bodyDragArea.pressed
                ToolTip.delay: 400
                ToolTip.text: "Region ID: " + modelData.region_id + "\n" +
                              "Origin: " + modelData.origin + "\n" +
                              "Status: " + modelData.review_status + "\n" +
                              "BBox: [" + modelData.effective_ymin + ", " + modelData.effective_xmin +
                              ", " + modelData.effective_ymax + ", " + modelData.effective_xmax + "]"

                onPressed: function(mouse) {
                    if (mouse.button === Qt.LeftButton && controller) {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, bodyDragArea);
                        controller.startDrag(boxRect.regionId, vpPt.x, vpPt.y);
                    }
                }

                onPositionChanged: function(mouse) {
                    if (pressed && controller && controller.editorState === "dragging") {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, bodyDragArea);
                        controller.updateDrag(vpPt.x, vpPt.y);
                    }
                }

                onReleased: function(mouse) {
                    if (mouse.button === Qt.LeftButton && controller && controller.editorState === "dragging") {
                        controller.commitDrag();
                    }
                }

                onCanceled: function() {
                    if (controller && controller.editorState === "dragging") {
                        controller.cancelDrag();
                    }
                }
            }

            // 8 Resize Handles (rendered exclusively for the currently selected region)
            Repeater {
                id: handlesRepeater
                model: boxRect.isSelected && controller ? controller.getHandleRects(0, 0, boxRect.width, boxRect.height, 8.0) : []

                delegate: Rectangle {
                    id: handleItem
                    objectName: "handle_" + modelData.handle
                    x: modelData.x
                    y: modelData.y
                    width: modelData.width
                    height: modelData.height
                    color: "#ffffff"
                    border.color: "#0077b6"
                    border.width: 1
                    radius: 1
                    z: 10

                    property string handleType: modelData.handle

                    property var handleCursor: {
                        var h = modelData.handle;
                        if (h === "nw" || h === "se") return Qt.SizeFDiagCursor;
                        if (h === "ne" || h === "sw") return Qt.SizeBDiagCursor;
                        if (h === "n" || h === "s") return Qt.SizeVerCursor;
                        if (h === "w" || h === "e") return Qt.SizeHorCursor;
                        return Qt.ArrowCursor;
                    }

                    MouseArea {
                        id: handleMouseArea
                        objectName: "handleArea_" + modelData.handle
                        anchors.fill: parent
                        anchors.margins: -4 // Expanded hit target for smooth mouse interaction
                        hoverEnabled: true
                        cursorShape: handleItem.handleCursor
                        acceptedButtons: Qt.LeftButton

                        onPressed: function(mouse) {
                            if (controller) {
                                var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleMouseArea);
                                controller.startResize(boxRect.regionId, handleItem.handleType, vpPt.x, vpPt.y);
                            }
                        }

                        onPositionChanged: function(mouse) {
                            if (pressed && controller && controller.editorState === "resizing") {
                                var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleMouseArea);
                                controller.updateResize(vpPt.x, vpPt.y);
                            }
                        }

                        onReleased: function(mouse) {
                            if (controller && controller.editorState === "resizing") {
                                controller.commitResize();
                            }
                        }

                        onCanceled: function() {
                            if (controller && controller.editorState === "resizing") {
                                controller.cancelResize();
                            }
                        }
                    }
                }
            }
        }
    }

    // =========================================================================
    // Transient Rubber-Band Box Preview During Manual Region Creation
    // =========================================================================
    Rectangle {
        id: creationPreview
        objectName: "creationPreview"
        visible: controller && controller.editorState === "creating" && controller.transientItemRect && controller.transientItemRect.width !== undefined && controller.transientItemRect.width > 0
        x: visible ? controller.transientItemRect.x : 0
        y: visible ? controller.transientItemRect.y : 0
        width: visible ? controller.transientItemRect.width : 0
        height: visible ? controller.transientItemRect.height : 0
        color: Qt.rgba(0.62, 0.31, 0.87, 0.2) // #9d4edd with 0.2 alpha
        border.color: "#9d4edd"
        border.width: 2
        radius: 2
        z: 20
    }
}
