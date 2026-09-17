// ============================================================
//  interfaces/desktop/qml/views/DocumentViewerView.qml
//  Desktop PDF Page Viewer & Semantic Region Overlay View
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import "../components"

Item {
    id: viewerViewRoot
    objectName: "documentViewerView"

    property var controller: typeof documentViewerController !== "undefined" ? documentViewerController : null

    onVisibleChanged: {
        if (visible && viewportArea) {
            viewportArea.syncViewportAndScene();
        }
    }

    onControllerChanged: {
        if (viewportArea) {
            viewportArea.syncViewportAndScene();
        }
    }

    Connections {
        target: viewerViewRoot.controller
        function onPageChanged() {
            if (viewportArea) {
                viewportArea.syncViewportAndScene();
            }
        }
        function onInteractionModeChanged() {
            var ctrl = viewerViewRoot.controller;
            if (ctrl) {
                panSelectBtn.checked = (ctrl.interactionMode === "pan_select");
                createRegionBtn.checked = (ctrl.interactionMode === "create_region");
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // =====================================================================
        // Top Toolbar: Navigation, Zoom & Metrics
        // =====================================================================
        // =====================================================================
        // Top Toolbar: Responsive Two-Tier Layout (Tier 1: Nav/Zoom, Tier 2: Modes/Edit)
        // =====================================================================
        Rectangle {
            id: toolbarContainer
            objectName: "toolbarContainer"
            Layout.fillWidth: true
            implicitHeight: toolbarContent.implicitHeight + 12
            color: "#1e1e24"
            border.color: "#2a2a35"
            border.width: 1

            ColumnLayout {
                id: toolbarContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 6
                spacing: 6

                // Tier 1: Document Navigation, Zoom & Metrics
                Flow {
                    Layout.fillWidth: true
                    spacing: 6

                    // Page Navigation Group
                    RowLayout {
                        spacing: 4
                        Button {
                            text: "<"
                            implicitWidth: 32
                            enabled: controller && controller.currentPage > 1 && !controller.isLoading
                            onClicked: controller.previousPage()
                        }

                        Text {
                            text: controller ? "p. " + controller.currentPage + " / " + Math.max(1, controller.totalPages) : "p. 1 / 1"
                            color: "#f0f0f0"
                            font.pixelSize: 12
                            font.bold: true
                            verticalAlignment: Text.AlignVCenter
                        }

                        Button {
                            text: ">"
                            implicitWidth: 32
                            enabled: controller && controller.currentPage < controller.totalPages && !controller.isLoading
                            onClicked: controller.nextPage()
                        }
                    }

                    // Zoom Controls Group
                    RowLayout {
                        spacing: 4
                        Button {
                            text: "-"
                            implicitWidth: 28
                            enabled: controller && controller.zoom > 0.2
                            onClicked: controller.setZoom(controller.zoom - 0.15)
                        }

                        Text {
                            text: controller ? Math.round(controller.zoom * 100) + "%" : "100%"
                            color: "#e0e0e0"
                            font.pixelSize: 11
                            Layout.preferredWidth: 36
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }

                        Button {
                            text: "+"
                            implicitWidth: 28
                            enabled: controller && controller.zoom < 5.0
                            onClicked: controller.setZoom(controller.zoom + 0.15)
                        }

                        Button {
                            text: "Fit"
                            implicitWidth: 42
                            enabled: controller && !controller.isLoading
                            onClicked: controller.fitToPage()
                        }

                        Button {
                            text: "1:1"
                            implicitWidth: 38
                            enabled: controller && (controller.zoom !== 1.0 || controller.panX !== 0 || controller.panY !== 0)
                            onClicked: controller.resetView()
                        }
                    }

                    // Region Count Badge
                    Rectangle {
                        height: 24
                        width: regionCountText.implicitWidth + 12
                        radius: 12
                        color: "#283044"
                        border.color: "#00b4d8"
                        visible: controller !== null

                        Text {
                            id: regionCountText
                            anchors.centerIn: parent
                            text: controller ? controller.activeRegions.length + " Regions" : "0 Regions"
                            color: "#00b4d8"
                            font.pixelSize: 10
                            font.bold: true
                        }
                    }
                }

                // Tier 2: Pointer Tool Modes & Region Review Controls
                Flow {
                    Layout.fillWidth: true
                    spacing: 6

                    ButtonGroup {
                        id: modeButtonGroup
                    }

                    Button {
                        id: panSelectBtn
                        objectName: "panSelectButton"
                        text: "Pan / Select"
                        checkable: true
                        checked: controller ? controller.interactionMode === "pan_select" : true
                        ButtonGroup.group: modeButtonGroup
                        enabled: controller && !controller.isLoading
                        ToolTip.visible: hovered
                        ToolTip.delay: 400
                        ToolTip.text: "Pan canvas (drag) & Select/resize regions"
                        onClicked: {
                            if (controller) controller.setInteractionMode("pan_select");
                        }
                    }

                    Button {
                        id: createRegionBtn
                        objectName: "createRegionButton"
                        text: "+ New Region"
                        checkable: true
                        checked: controller ? controller.interactionMode === "create_region" : false
                        ButtonGroup.group: modeButtonGroup
                        enabled: controller && !controller.isLoading
                        ToolTip.visible: hovered
                        ToolTip.delay: 400
                        ToolTip.text: "Create region: drag anywhere on page to draw a new box"
                        onClicked: {
                            if (controller) controller.setInteractionMode("create_region");
                        }
                    }

                    Button {
                        id: deleteRegionBtn
                        objectName: "deleteRegionButton"
                        text: "Delete Region"
                        enabled: controller && controller.hasSelection && !controller.isLoading
                        ToolTip.visible: hovered
                        ToolTip.delay: 400
                        ToolTip.text: "Delete selected visual region"
                        onClicked: controller.deleteSelectedRegion()
                    }

                    Button {
                        id: resetToAiBtn
                        objectName: "resetToAiButton"
                        text: "Reset to AI"
                        enabled: controller && controller.canResetSelectedToAi && !controller.isLoading
                        ToolTip.visible: hovered
                        ToolTip.delay: 400
                        ToolTip.text: "Reset selected region back to original AI detected bounds"
                        onClicked: controller.resetSelectedRegionToAi()
                    }
                }
            }
        }

        // =====================================================================
        // Center Viewport: Authoritative Pan & Zoom Canvas (Model B - Shared Transformed Scene)
        // =====================================================================
        Rectangle {
            id: viewportArea
            objectName: "viewportArea"
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#121216"
            clip: true

            function syncViewportAndScene() {
                var ctrl = viewerViewRoot.controller;
                if (ctrl && width >= 50 && height >= 50) {
                    ctrl.setViewportDimensions(width, height);
                    ctrl.setItemDimensions(width, height);
                }
            }

            onWidthChanged: syncViewportAndScene()
            onHeightChanged: syncViewportAndScene()
            onVisibleChanged: {
                if (visible) syncViewportAndScene()
            }
            Component.onCompleted: syncViewportAndScene()

            // Viewport Canvas Pan & Zoom Mouse Interaction (for margin space & wheel)
            MouseArea {
                id: panZoomArea
                objectName: "panZoomArea"
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
                cursorShape: {
                    if (!controller || controller.zoom <= 1.0) return Qt.ArrowCursor;
                    return pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor;
                }

                property real lastX: 0
                property real lastY: 0

                onPressed: function(mouse) {
                    lastX = mouse.x;
                    lastY = mouse.y;
                    if (mouse.button === Qt.LeftButton && controller) {
                        controller.clearSelection();
                    }
                }

                onPositionChanged: function(mouse) {
                    if (pressed && controller) {
                        var dx = mouse.x - lastX;
                        var dy = mouse.y - lastY;
                        controller.panBy(-dx, -dy);
                        lastX = mouse.x;
                        lastY = mouse.y;
                    }
                }

                onWheel: function(wheel) {
                    if (controller) {
                        var factor = wheel.angleDelta.y > 0 ? 1.15 : (1.0 / 1.15);
                        controller.zoomAt(controller.zoom * factor, wheel.x, wheel.y);
                    }
                }
            }

            // Shared Transformed Scene: Page image and overlay share exact same affine transform
            Item {
                id: pageScene
                objectName: "pageScene"
                x: controller ? -controller.panX : 0
                y: controller ? -controller.panY : 0
                width: controller ? controller.itemWidth : 800
                height: controller ? controller.itemHeight : 600
                scale: controller ? controller.zoom : 1.0
                transformOrigin: Item.TopLeft

                // Rendered Page Raster Image
                Image {
                    id: pageImage
                    objectName: "pageImage"
                    anchors.fill: parent
                    source: controller ? controller.pageImageUri : ""
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                    cache: false

                    // Semantic Bounding Box Overlay sharing the exact same scene transform
                    BoundingBoxOverlay {
                        id: regionOverlay
                        objectName: "regionOverlay"
                        anchors.fill: parent
                        controller: viewerViewRoot.controller
                        viewportArea: viewportArea
                        fitMode: "preserve_aspect_fit"
                    }
                }
            }

            // Loading Indicator
            Rectangle {
                anchors.fill: parent
                color: Qt.rgba(0, 0, 0, 0.6)
                visible: controller ? controller.isLoading : false

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 12

                    BusyIndicator {
                        running: controller ? controller.isLoading : false
                        Layout.alignment: Qt.AlignHCenter
                    }

                    Text {
                        text: "Rendering PDF Page..."
                        color: "#ffffff"
                        font.pixelSize: 13
                        Layout.alignment: Qt.AlignHCenter
                    }
                }
            }

            // Error Banner
            Rectangle {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 16
                height: 48
                radius: 6
                color: "#d90429"
                visible: controller ? controller.errorMessage !== "" : false

                Text {
                    anchors.centerIn: parent
                    text: controller ? controller.errorMessage : ""
                    color: "white"
                    font.pixelSize: 12
                    font.bold: true
                }
            }
        }
    }
}
