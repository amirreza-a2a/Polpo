// ============================================================
//  interfaces/desktop/qml/views/MarkdownView.qml
//  Desktop Native Markdown AST Virtualized Viewer View
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import "../components"

Item {
    id: markdownViewRoot
    objectName: "markdownView"

    property var controller: typeof markdownViewerController !== "undefined" ? markdownViewerController : null
    property var syncCoordinator: typeof reviewWorkspaceSyncCoordinator !== "undefined" ? reviewWorkspaceSyncCoordinator : null

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // =====================================================================
        // Top Toolbar: Zoom Controls, Version Info & Metrics
        // =====================================================================
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            color: "#18181f"
            border.color: "#272732"
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 12

                Text {
                    text: "Markdown Document"
                    color: "#f3f4f6"
                    font.pixelSize: 13
                    font.bold: true
                }

                // Version Badge
                Rectangle {
                    visible: controller && controller.hasDocument
                    height: 20
                    width: versionText.implicitWidth + 10
                    radius: 3
                    color: "#374151"

                    Text {
                        id: versionText
                        anchors.centerIn: parent
                        text: controller ? "v" + controller.activeVersion : "v1"
                        color: "#93c5fd"
                        font.pixelSize: 11
                        font.bold: true
                    }
                }

                Item { Layout.fillWidth: true }

                // Zoom Controls
                Button {
                    text: "-"
                    implicitWidth: 32
                    implicitHeight: 28
                    enabled: controller && controller.scaleFactor > 0.5
                    onClicked: if (controller) controller.zoomOut()
                }

                Text {
                    text: controller ? Math.round(controller.scaleFactor * 100) + "%" : "100%"
                    color: "#e0e0e0"
                    font.pixelSize: 12
                    Layout.preferredWidth: 44
                    horizontalAlignment: Text.AlignHCenter
                }

                Button {
                    text: "+"
                    implicitWidth: 32
                    implicitHeight: 28
                    enabled: controller && controller.scaleFactor < 3.0
                    onClicked: if (controller) controller.zoomIn()
                }

                Button {
                    text: "Reset"
                    implicitHeight: 28
                    enabled: controller && controller.scaleFactor !== 1.0
                    onClicked: if (controller) controller.resetZoom()
                }
            }
        }

        // =====================================================================
        // Central View Area: Virtualized ListView / Loading / Empty State
        // =====================================================================
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#0f0f13"

            // 1. Loading State
            ColumnLayout {
                anchors.centerIn: parent
                visible: controller && controller.isLoading
                spacing: 12

                BusyIndicator {
                    Layout.alignment: Qt.AlignHCenter
                    running: controller && controller.isLoading
                }

                Text {
                    text: "Rendering Markdown AST..."
                    color: "#9ca3af"
                    font.pixelSize: 13
                    Layout.alignment: Qt.AlignHCenter
                }
            }

            // 2. Error State
            ColumnLayout {
                anchors.centerIn: parent
                visible: controller && !controller.isLoading && !controller.hasDocument && controller.errorMessage !== ""
                spacing: 10

                Text {
                    text: "⚠️"
                    font.pixelSize: 32
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: "Failed to Load Markdown"
                    color: "#f87171"
                    font.pixelSize: 15
                    font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: controller ? controller.errorMessage : ""
                    color: "#9ca3af"
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                    Layout.maximumWidth: 480
                    horizontalAlignment: Text.AlignHCenter
                    Layout.alignment: Qt.AlignHCenter
                }
            }

            // 3. Empty State
            ColumnLayout {
                anchors.centerIn: parent
                visible: controller && !controller.isLoading && !controller.hasDocument && controller.errorMessage === ""
                spacing: 8

                Text {
                    text: "📄"
                    font.pixelSize: 32
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: "No Document Loaded"
                    color: "#6b7280"
                    font.pixelSize: 14
                    font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }

                Text {
                    text: "Select a job to view its rendered Markdown document."
                    color: "#4b5563"
                    font.pixelSize: 12
                    Layout.alignment: Qt.AlignHCenter
                }
            }

            // 4. Virtualized AST ListView
            ListView {
                id: markdownListView
                objectName: "markdownListView"
                anchors.fill: parent
                anchors.topMargin: 12
                anchors.bottomMargin: 12
                visible: controller && controller.hasDocument && !controller.isLoading

                model: controller ? controller.model : null
                delegate: MarkdownNodeDelegate {
                    controller: markdownViewRoot.controller
                }

                clip: true
                reuseItems: true
                cacheBuffer: 800
                spacing: 10

                ScrollBar.vertical: ScrollBar {
                    policy: ScrollBar.AsNeeded
                }

                property bool isProgrammaticScrolling: false

                onContentYChanged: {
                    if (isProgrammaticScrolling) return
                    if (!syncCoordinator || !syncCoordinator.isDualPane) return
                    var maxScroll = markdownListView.contentHeight - markdownListView.height
                    if (maxScroll <= 0) return
                    var progress = Math.max(0.0, Math.min(1.0, (markdownListView.contentY - markdownListView.originY) / maxScroll))
                    syncCoordinator.reportPreviewScrollProgress(progress)
                }

                Timer {
                    id: scrollResetTimer
                    interval: 150
                    repeat: false
                    onTriggered: markdownListView.isProgrammaticScrolling = false
                }

                Connections {
                    target: controller
                    function onRequestScrollToNode(nodeIndex) {
                        if (nodeIndex >= 0 && nodeIndex < markdownListView.count) {
                            markdownListView.isProgrammaticScrolling = true
                            markdownListView.positionViewAtIndex(nodeIndex, ListView.Beginning)
                            scrollResetTimer.restart()
                        }
                    }
                }

                Connections {
                    target: syncCoordinator
                    function onRequestScrollPreviewToProgress(progress) {
                        if (!markdownListView) return
                        var maxScroll = markdownListView.contentHeight - markdownListView.height
                        if (maxScroll <= 0) return
                        markdownListView.isProgrammaticScrolling = true
                        var targetY = markdownListView.originY + progress * maxScroll
                        markdownListView.contentY = Math.max(markdownListView.originY, Math.min(markdownListView.originY + maxScroll, targetY))
                        markdownListView.isProgrammaticScrolling = false
                    }
                }
            }

            // 5. Preview Paused Overlay
            Rectangle {
                id: previewPausedOverlay
                objectName: "previewPausedOverlay"
                anchors.fill: parent
                visible: controller && controller.previewPaused
                color: Qt.rgba(0.06, 0.06, 0.08, 0.85)
                z: 100

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 12

                    Text {
                        text: "⚠️ Preview Paused"
                        color: "#f59e0b"
                        font.pixelSize: 18
                        font.bold: true
                        Layout.alignment: Qt.AlignHCenter
                    }

                    Text {
                        text: controller ? controller.previewPausedReason : ""
                        color: "#9ca3af"
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                        Layout.maximumWidth: 440
                        horizontalAlignment: Text.AlignHCenter
                        Layout.alignment: Qt.AlignHCenter
                    }
                }
            }
        }
    }
}
