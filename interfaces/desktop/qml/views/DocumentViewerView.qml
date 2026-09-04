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
    anchors.fill: parent

    property var controller: documentViewerController

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // =====================================================================
        // Top Toolbar: Navigation, Zoom & Metrics
        // =====================================================================
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 52
            color: "#1e1e24"
            border.color: "#2a2a35"
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 12

                // Page Navigation
                Button {
                    text: "< Prev"
                    enabled: controller && controller.currentPage > 1 && !controller.isLoading
                    onClicked: controller.previousPage()
                }

                Text {
                    text: controller ? "Page " + controller.currentPage + " / " + Math.max(1, controller.totalPages) : "Page 1 / 1"
                    color: "#f0f0f0"
                    font.pixelSize: 13
                    font.bold: true
                }

                Button {
                    text: "Next >"
                    enabled: controller && controller.currentPage < controller.totalPages && !controller.isLoading
                    onClicked: controller.nextPage()
                }

                Rectangle {
                    width: 1
                    Layout.preferredHeight: 24
                    color: "#3a3a48"
                }

                // Zoom Controls
                Button {
                    text: "-"
                    enabled: controller && controller.zoom > 0.2
                    onClicked: controller.setZoom(controller.zoom - 0.15)
                }

                Text {
                    text: controller ? Math.round(controller.zoom * 100) + "%" : "100%"
                    color: "#e0e0e0"
                    font.pixelSize: 12
                    Layout.preferredWidth: 44
                    horizontalAlignment: Text.AlignHCenter
                }

                Button {
                    text: "+"
                    enabled: controller && controller.zoom < 5.0
                    onClicked: controller.setZoom(controller.zoom + 0.15)
                }

                Button {
                    text: "Fit to Page"
                    enabled: controller && !controller.isLoading
                    onClicked: controller.fitToPage()
                }

                Button {
                    text: "Reset 100%"
                    enabled: controller && (controller.zoom !== 1.0 || controller.panX !== 0 || controller.panY !== 0)
                    onClicked: controller.resetView()
                }

                Item { Layout.fillWidth: true }

                // Region Count Badge
                Rectangle {
                    Layout.preferredHeight: 26
                    Layout.preferredWidth: regionCountText.implicitWidth + 16
                    radius: 13
                    color: "#283044"
                    border.color: "#00b4d8"

                    Text {
                        id: regionCountText
                        anchors.centerIn: parent
                        text: controller ? controller.activeRegions.length + " Regions" : "0 Regions"
                        color: "#00b4d8"
                        font.pixelSize: 11
                        font.bold: true
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
                if (controller && width > 0 && height > 0) {
                    controller.setViewportDimensions(width, height);
                    controller.setItemDimensions(width, height);
                }
            }

            onWidthChanged: syncViewportAndScene()
            onHeightChanged: syncViewportAndScene()
            Component.onCompleted: syncViewportAndScene()

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
                        fitMode: "preserve_aspect_fit"
                    }
                }
            }

            // Viewport Pan & Zoom Mouse Interaction
            MouseArea {
                id: panZoomArea
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton

                property real lastX: 0
                property real lastY: 0

                onPressed: function(mouse) {
                    lastX = mouse.x;
                    lastY = mouse.y;
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
