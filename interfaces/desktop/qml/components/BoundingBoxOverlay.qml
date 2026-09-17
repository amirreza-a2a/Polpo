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
    readonly property real panDragThreshold: 4.0 // Viewport pixels before canvas pan engages

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

    // Trigger re-computation only on dimensions change, active regions change, or fitMode change
    property var overlayItems: {
        if (!controller || width <= 0 || height <= 0) return [];
        var _active = controller.activeRegions;
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
        cursorShape: {
            if (controller && controller.interactionMode === "create_region") {
                return Qt.CrossCursor;
            }
            if (!controller || controller.zoom <= 1.0) {
                return Qt.ArrowCursor;
            }
            return isPanDragging ? Qt.ClosedHandCursor : Qt.OpenHandCursor;
        }
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton

        property real startStationaryX: 0
        property real startStationaryY: 0
        property real lastStationaryX: 0
        property real lastStationaryY: 0
        property bool isPanDragging: false

        onPressed: function(mouse) {
            var stationaryPt = overlayRoot.mapToViewport(mouse.x, mouse.y, emptyArea);
            startStationaryX = stationaryPt.x;
            startStationaryY = stationaryPt.y;
            lastStationaryX = stationaryPt.x;
            lastStationaryY = stationaryPt.y;
            isPanDragging = false;

            if (mouse.button === Qt.RightButton || mouse.button === Qt.MiddleButton) {
                isPanDragging = true;
            } else if (mouse.button === Qt.LeftButton && controller) {
                if (controller.interactionMode === "create_region") {
                    var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, emptyArea);
                    controller.startCreateManual(vpPt.x, vpPt.y);
                }
                // In pan_select mode, do not pan or deselect on press; wait for drag or release.
            }
        }

        onPositionChanged: function(mouse) {
            var stationaryPt = overlayRoot.mapToViewport(mouse.x, mouse.y, emptyArea);

            if (controller && controller.interactionMode === "create_region") {
                if (pressed && controller.editorState === "creating") {
                    var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, emptyArea);
                    controller.updateCreateManual(vpPt.x, vpPt.y);
                }
                return;
            }

            // In pan_select mode (or right/middle button pan in either mode):
            if (pressed && controller) {
                if (!isPanDragging) {
                    var distX = stationaryPt.x - startStationaryX;
                    var distY = stationaryPt.y - startStationaryY;
                    if ((distX * distX + distY * distY) >= (overlayRoot.panDragThreshold * overlayRoot.panDragThreshold)) {
                        isPanDragging = true;
                        lastStationaryX = stationaryPt.x;
                        lastStationaryY = stationaryPt.y;
                    }
                }

                if (isPanDragging) {
                    var dx = stationaryPt.x - lastStationaryX;
                    var dy = stationaryPt.y - lastStationaryY;
                    controller.panBy(-dx, -dy);
                    lastStationaryX = stationaryPt.x;
                    lastStationaryY = stationaryPt.y;
                }
            }
        }

        onReleased: function(mouse) {
            if (controller && controller.interactionMode === "create_region") {
                if (mouse.button === Qt.LeftButton && controller.editorState === "creating") {
                    controller.commitCreateManual();
                }
            } else if (controller && controller.interactionMode === "pan_select") {
                if (mouse.button === Qt.LeftButton) {
                    if (!isPanDragging) {
                        // Click without drag: deselect
                        controller.clearSelection();
                    }
                }
            }
            isPanDragging = false;
        }

        onCanceled: function() {
            isPanDragging = false;
            if (controller && controller.editorState === "creating") {
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
        objectName: "regionRepeater"
        model: overlayRoot.overlayItems

        delegate: Rectangle {
            id: boxRect
            objectName: "boxRect_" + modelData.region_id
            x: modelData.x
            y: modelData.y
            width: modelData.width
            height: modelData.height
            z: boxRect.isSelected ? 5 : 1

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
                enabled: controller ? controller.interactionMode === "pan_select" : true
                cursorShape: {
                    if (controller && controller.interactionMode === "create_region") {
                        return Qt.CrossCursor;
                    }
                    return boxRect.isSelected ? Qt.SizeAllCursor : Qt.PointingHandCursor;
                }

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
        }
    }

    // =========================================================================
    // Permanent Selection Manipulator & 8 Static Resize Handles (Architecture A)
    // =========================================================================
    Item {
        id: selectionManipulator
        objectName: "selectionManipulator"
        property var controller: overlayRoot.controller
        visible: Boolean(controller && controller.hasSelection && controller.selectedItemRect && controller.selectedItemRect.width > 0)
        x: controller && controller.selectedItemRect ? (controller.selectedItemRect.x || 0) : 0
        y: controller && controller.selectedItemRect ? (controller.selectedItemRect.y || 0) : 0
        width: controller && controller.selectedItemRect ? (controller.selectedItemRect.width || 0) : 0
        height: controller && controller.selectedItemRect ? (controller.selectedItemRect.height || 0) : 0
        z: 15

                // Visual selection highlight border
        Rectangle {
            anchors.fill: parent
            color: "transparent"
            border.color: "#00f0ff"
            border.width: 2
            radius: 2
        }

        // Body Drag Interaction for Selected Region
        MouseArea {
            id: manipulatorDragArea
            objectName: "manipulatorDragArea"
            anchors.fill: parent
            z: 1
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton
            enabled: controller ? controller.interactionMode === "pan_select" : false
            cursorShape: (controller && controller.interactionMode === "create_region") ? Qt.CrossCursor : Qt.SizeAllCursor

            ToolTip.visible: manipulatorDragArea.containsMouse && !manipulatorDragArea.pressed && controller && controller.selectedRegion
            ToolTip.delay: 400
            ToolTip.text: {
                var sel = controller ? controller.selectedRegion : null;
                if (!sel) return "";
                return "Region ID: " + (sel.region_id || "") + "\n" +
                       "Origin: " + (sel.origin || "") + "\n" +
                       "Status: " + (sel.review_status || "") + "\n" +
                       "BBox: [" + (sel.effective_ymin || 0) + ", " + (sel.effective_xmin || 0) +
                       ", " + (sel.effective_ymax || 0) + ", " + (sel.effective_xmax || 0) + "]";
            }

            onPressed: function(mouse) {
                if (mouse.button === Qt.LeftButton && controller) {
                    var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, manipulatorDragArea);
                    controller.startDrag(controller.selectedRegionId, vpPt.x, vpPt.y);
                }
            }

            onPositionChanged: function(mouse) {
                if (pressed && controller && controller.editorState === "dragging") {
                    var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, manipulatorDragArea);
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

        // 8 Static Resize Handles (permanent QML Items; zero Repeater recreations)
        // 1. Top-Left (NW)
        Rectangle {
            id: handle_nw
            objectName: "handle_nw"
            property string handleType: "nw"
            x: -4
            y: -4
            width: 8
            height: 8
            color: "#ffffff"
            border.color: "#0077b6"
            border.width: 1
            radius: 1
            z: 10

            MouseArea {
                id: handleArea_nw
                objectName: "handleArea_nw"
                anchors.fill: parent
                anchors.margins: -4
                hoverEnabled: true
                cursorShape: Qt.SizeFDiagCursor
                acceptedButtons: Qt.LeftButton
                enabled: controller ? controller.interactionMode === "pan_select" : false

                onPressed: function(mouse) {
                    if (controller) {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_nw);
                        controller.startResize(controller.selectedRegionId, "nw", vpPt.x, vpPt.y);
                    }
                }

                onPositionChanged: function(mouse) {
                    if (pressed && controller && controller.editorState === "resizing") {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_nw);
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

        // 2. Top-Center (N)
        Rectangle {
            id: handle_n
            objectName: "handle_n"
            property string handleType: "n"
            x: Math.round((parent.width - 8) / 2)
            y: -4
            width: 8
            height: 8
            color: "#ffffff"
            border.color: "#0077b6"
            border.width: 1
            radius: 1
            z: 10

            MouseArea {
                id: handleArea_n
                objectName: "handleArea_n"
                anchors.fill: parent
                anchors.margins: -4
                hoverEnabled: true
                cursorShape: Qt.SizeVerCursor
                acceptedButtons: Qt.LeftButton
                enabled: controller ? controller.interactionMode === "pan_select" : false

                onPressed: function(mouse) {
                    if (controller) {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_n);
                        controller.startResize(controller.selectedRegionId, "n", vpPt.x, vpPt.y);
                    }
                }

                onPositionChanged: function(mouse) {
                    if (pressed && controller && controller.editorState === "resizing") {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_n);
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

        // 3. Top-Right (NE)
        Rectangle {
            id: handle_ne
            objectName: "handle_ne"
            property string handleType: "ne"
            x: parent.width - 4
            y: -4
            width: 8
            height: 8
            color: "#ffffff"
            border.color: "#0077b6"
            border.width: 1
            radius: 1
            z: 10

            MouseArea {
                id: handleArea_ne
                objectName: "handleArea_ne"
                anchors.fill: parent
                anchors.margins: -4
                hoverEnabled: true
                cursorShape: Qt.SizeBDiagCursor
                acceptedButtons: Qt.LeftButton
                enabled: controller ? controller.interactionMode === "pan_select" : false

                onPressed: function(mouse) {
                    if (controller) {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_ne);
                        controller.startResize(controller.selectedRegionId, "ne", vpPt.x, vpPt.y);
                    }
                }

                onPositionChanged: function(mouse) {
                    if (pressed && controller && controller.editorState === "resizing") {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_ne);
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

        // 4. Middle-Right (E)
        Rectangle {
            id: handle_e
            objectName: "handle_e"
            property string handleType: "e"
            x: parent.width - 4
            y: Math.round((parent.height - 8) / 2)
            width: 8
            height: 8
            color: "#ffffff"
            border.color: "#0077b6"
            border.width: 1
            radius: 1
            z: 10

            MouseArea {
                id: handleArea_e
                objectName: "handleArea_e"
                anchors.fill: parent
                anchors.margins: -4
                hoverEnabled: true
                cursorShape: Qt.SizeHorCursor
                acceptedButtons: Qt.LeftButton
                enabled: controller ? controller.interactionMode === "pan_select" : false

                onPressed: function(mouse) {
                    if (controller) {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_e);
                        controller.startResize(controller.selectedRegionId, "e", vpPt.x, vpPt.y);
                    }
                }

                onPositionChanged: function(mouse) {
                    if (pressed && controller && controller.editorState === "resizing") {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_e);
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

        // 5. Bottom-Right (SE)
        Rectangle {
            id: handle_se
            objectName: "handle_se"
            property string handleType: "se"
            x: parent.width - 4
            y: parent.height - 4
            width: 8
            height: 8
            color: "#ffffff"
            border.color: "#0077b6"
            border.width: 1
            radius: 1
            z: 10

            MouseArea {
                id: handleArea_se
                objectName: "handleArea_se"
                anchors.fill: parent
                anchors.margins: -4
                hoverEnabled: true
                cursorShape: Qt.SizeFDiagCursor
                acceptedButtons: Qt.LeftButton
                enabled: controller ? controller.interactionMode === "pan_select" : false

                onPressed: function(mouse) {
                    if (controller) {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_se);
                        controller.startResize(controller.selectedRegionId, "se", vpPt.x, vpPt.y);
                    }
                }

                onPositionChanged: function(mouse) {
                    if (pressed && controller && controller.editorState === "resizing") {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_se);
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

        // 6. Bottom-Center (S)
        Rectangle {
            id: handle_s
            objectName: "handle_s"
            property string handleType: "s"
            x: Math.round((parent.width - 8) / 2)
            y: parent.height - 4
            width: 8
            height: 8
            color: "#ffffff"
            border.color: "#0077b6"
            border.width: 1
            radius: 1
            z: 10

            MouseArea {
                id: handleArea_s
                objectName: "handleArea_s"
                anchors.fill: parent
                anchors.margins: -4
                hoverEnabled: true
                cursorShape: Qt.SizeVerCursor
                acceptedButtons: Qt.LeftButton
                enabled: controller ? controller.interactionMode === "pan_select" : false

                onPressed: function(mouse) {
                    if (controller) {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_s);
                        controller.startResize(controller.selectedRegionId, "s", vpPt.x, vpPt.y);
                    }
                }

                onPositionChanged: function(mouse) {
                    if (pressed && controller && controller.editorState === "resizing") {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_s);
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

        // 7. Bottom-Left (SW)
        Rectangle {
            id: handle_sw
            objectName: "handle_sw"
            property string handleType: "sw"
            x: -4
            y: parent.height - 4
            width: 8
            height: 8
            color: "#ffffff"
            border.color: "#0077b6"
            border.width: 1
            radius: 1
            z: 10

            MouseArea {
                id: handleArea_sw
                objectName: "handleArea_sw"
                anchors.fill: parent
                anchors.margins: -4
                hoverEnabled: true
                cursorShape: Qt.SizeBDiagCursor
                acceptedButtons: Qt.LeftButton
                enabled: controller ? controller.interactionMode === "pan_select" : false

                onPressed: function(mouse) {
                    if (controller) {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_sw);
                        controller.startResize(controller.selectedRegionId, "sw", vpPt.x, vpPt.y);
                    }
                }

                onPositionChanged: function(mouse) {
                    if (pressed && controller && controller.editorState === "resizing") {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_sw);
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

        // 8. Middle-Left (W)
        Rectangle {
            id: handle_w
            objectName: "handle_w"
            property string handleType: "w"
            x: -4
            y: Math.round((parent.height - 8) / 2)
            width: 8
            height: 8
            color: "#ffffff"
            border.color: "#0077b6"
            border.width: 1
            radius: 1
            z: 10

            MouseArea {
                id: handleArea_w
                objectName: "handleArea_w"
                anchors.fill: parent
                anchors.margins: -4
                hoverEnabled: true
                cursorShape: Qt.SizeHorCursor
                acceptedButtons: Qt.LeftButton
                enabled: controller ? controller.interactionMode === "pan_select" : false

                onPressed: function(mouse) {
                    if (controller) {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_w);
                        controller.startResize(controller.selectedRegionId, "w", vpPt.x, vpPt.y);
                    }
                }

                onPositionChanged: function(mouse) {
                    if (pressed && controller && controller.editorState === "resizing") {
                        var vpPt = overlayRoot.mapToViewport(mouse.x, mouse.y, handleArea_w);
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
