// ============================================================
//  interfaces/desktop/qml/components/MarkdownEditorPane.qml
//  Native Raw Markdown Editor Pane with OCC Save and Conflict Handling
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: editorPaneRoot
    objectName: "markdownEditorPane"

    property var controller: typeof markdownEditorController !== "undefined" ? markdownEditorController : null
    property var syncCoordinator: typeof reviewWorkspaceSyncCoordinator !== "undefined" ? reviewWorkspaceSyncCoordinator : null
    property bool isProgrammaticScrolling: false

    function scrollPositionIntoView(pos) {
        if (!sourceTextArea || !editorScrollView || !editorScrollView.contentItem) return
        var rect = sourceTextArea.positionToRectangle(pos)
        var targetY = Math.max(0, rect.y - editorScrollView.height / 3)
        isProgrammaticScrolling = true
        editorScrollView.contentItem.contentY = targetY
        isProgrammaticScrolling = false
    }

    onControllerChanged: {
        if (controller && sourceTextArea && sourceTextArea.textDocument) {
            controller.attachTextDocument(sourceTextArea.textDocument)
        }
    }

    Connections {
        target: controller
        function onMatchSelected(start, end) {
            sourceTextArea.select(start, end)
        }
        function onRequestScrollToPosition(pos) {
            scrollPositionIntoView(pos)
        }
        function onRequestNavigateToPosition(pos) {
            if (sourceTextArea) {
                sourceTextArea.cursorPosition = pos
                sourceTextArea.forceActiveFocus()
            }
            scrollPositionIntoView(pos)
        }
    }

    Connections {
        target: syncCoordinator
        function onRequestScrollSourceToProgress(progress) {
            if (!editorScrollView || !editorScrollView.contentItem) return
            var flick = editorScrollView.contentItem
            var maxScroll = flick.contentHeight - flick.height
            if (maxScroll <= 0) return
            isProgrammaticScrolling = true
            flick.contentY = Math.max(0.0, Math.min(maxScroll, progress * maxScroll))
            isProgrammaticScrolling = false
        }
    }

    Connections {
        target: (editorScrollView && editorScrollView.contentItem) ? editorScrollView.contentItem : null
        function onContentYChanged() {
            if (isProgrammaticScrolling) return
            if (!syncCoordinator || !syncCoordinator.isDualPane) return
            var flick = editorScrollView.contentItem
            if (!flick) return
            var maxScroll = flick.contentHeight - flick.height
            if (maxScroll <= 0) return
            var progress = Math.max(0.0, Math.min(1.0, flick.contentY / maxScroll))
            syncCoordinator.reportSourceScrollProgress(progress)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // =====================================================================
        // Top Toolbar: Actions, Status & Version Badge
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
                    text: "Markdown Source Editor"
                    color: "#f3f4f6"
                    font.pixelSize: 13
                    font.bold: true
                }

                // Canonical Version Badge
                Rectangle {
                    visible: controller && controller.activeJobId > 0
                    height: 20
                    width: versionText.implicitWidth + 10
                    radius: 3
                    color: "#374151"

                    Text {
                        id: versionText
                        anchors.centerIn: parent
                        text: controller ? "v" + controller.activeVersion : "v0"
                        color: "#93c5fd"
                        font.pixelSize: 11
                        font.bold: true
                    }
                }

                // Dirty State Indicator
                Row {
                    visible: controller ? controller.isDirty : false
                    spacing: 5
                    Layout.alignment: Qt.AlignVCenter

                    Rectangle {
                        width: 8
                        height: 8
                        radius: 4
                        color: "#f59e0b"
                        anchors.verticalCenter: parent.verticalCenter
                    }

                    Text {
                        text: "Modified"
                        color: "#f59e0b"
                        font.pixelSize: 11
                        font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                Item { Layout.fillWidth: true }

                // Find Button
                Button {
                    id: findButton
                    objectName: "editorFindButton"
                    text: "Find (Ctrl+F)"
                    implicitHeight: 28
                    onClicked: {
                        if (controller) {
                            controller.openSearch()
                            searchBar.focusSearchField()
                        }
                    }
                }

                // Discard Button
                Button {
                    id: discardButton
                    objectName: "editorDiscardButton"
                    text: "Discard"
                    implicitHeight: 28
                    enabled: controller && controller.isDirty && !controller.isSaving
                    onClicked: {
                        if (controller) {
                            controller.discard()
                        }
                    }
                }

                // Save Button
                Button {
                    id: saveButton
                    objectName: "editorSaveButton"
                    text: controller && controller.isSaving ? "Saving..." : "Save (Ctrl+S)"
                    implicitHeight: 28
                    highlighted: true
                    enabled: controller && controller.isDirty && !controller.isSaving && (!controller.hasConflict || (controller.mergeSessionActive && controller.canSaveConflict))
                    onClicked: {
                        if (controller) {
                            controller.save()
                        }
                    }
                }
            }
        }

        // =====================================================================
        // Conflict Warning Banner (Legacy Fallback)
        // =====================================================================
        Rectangle {
            id: conflictBanner
            objectName: "editorConflictBanner"
            Layout.fillWidth: true
            Layout.preferredHeight: 44
            visible: controller ? (controller.hasConflict && !controller.mergeSessionActive) : false
            color: "#451a03"
            border.color: "#b45309"
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 12

                Text {
                    text: "⚠️"
                    font.pixelSize: 14
                }

                Text {
                    Layout.fillWidth: true
                    text: controller ? controller.conflictMessage : "Document modified externally."
                    color: "#fef3c7"
                    font.pixelSize: 12
                    font.bold: true
                    elide: Text.ElideRight
                }

                Button {
                    text: "Reload Latest"
                    implicitHeight: 28
                    onClicked: {
                        if (controller) {
                            controller.loadSource(controller.activeJobId)
                        }
                    }
                }
            }
        }

        // =====================================================================
        // Visual Conflict Resolution Toolbar
        // =====================================================================
        ConflictResolutionBar {
            id: conflictResolutionBar
            objectName: "conflictResolutionBar"
            Layout.fillWidth: true
            controller: editorPaneRoot.controller
        }

        // =====================================================================
        // Auto-Merge Notification Banner
        // =====================================================================
        Rectangle {
            id: autoMergeBanner
            objectName: "editorAutoMergeBanner"
            Layout.fillWidth: true
            Layout.preferredHeight: 36
            property bool dismissed: false
            visible: (controller ? controller.autoMergeNotification !== "" : false) && !dismissed
            color: "#064e3b"
            border.color: "#059669"
            border.width: 1

            Connections {
                target: controller
                function onAutoMergeNotified() {
                    autoMergeBanner.dismissed = false
                }
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 12

                Text {
                    text: "✓"
                    color: "#a7f3d0"
                    font.pixelSize: 13
                    font.bold: true
                }

                Text {
                    id: autoMergeText
                    objectName: "editorAutoMergeText"
                    Layout.fillWidth: true
                    text: controller ? controller.autoMergeNotification : ""
                    color: "#ecfdf5"
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }

                Button {
                    id: autoMergeDismissBtn
                    objectName: "editorAutoMergeDismissButton"
                    text: "✕"
                    implicitHeight: 24
                    implicitWidth: 24
                    onClicked: autoMergeBanner.dismissed = true
                }
            }
        }

        // =====================================================================
        // Error Banner
        // =====================================================================
        Rectangle {
            id: errorBanner
            Layout.fillWidth: true
            Layout.preferredHeight: 40
            visible: controller && controller.errorMessage !== "" && !controller.hasConflict
            color: "#450a0a"
            border.color: "#b91c1c"
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 12

                Text {
                    Layout.fillWidth: true
                    text: controller ? controller.errorMessage : ""
                    color: "#fecaca"
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
            }
        }

        // =====================================================================
        // Docked Search and Replace Drawer
        // =====================================================================
        MarkdownEditorSearchBar {
            id: searchBar
            objectName: "markdownEditorSearchBar"
            Layout.fillWidth: true
            controller: editorPaneRoot.controller
            targetTextArea: sourceTextArea
        }

        // =====================================================================
        // Central Native PlainText TextArea
        // =====================================================================
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#0f0f13"

            ScrollView {
                id: editorScrollView
                anchors.fill: parent
                clip: true

                TextArea {
                    id: sourceTextArea
                    objectName: "markdownSourceTextArea"
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.Wrap
                    selectByMouse: true
                    color: "#e5e7eb"
                    font.family: "Monospace"
                    font.pixelSize: 13
                    padding: 16
                    background: Rectangle { color: "transparent" }

                    text: controller ? controller.sourceText : ""

                    onTextChanged: {
                        if (controller && text !== controller.sourceText) {
                            controller.setSourceText(text)
                        }
                    }

                    onCursorPositionChanged: {
                        if (controller) {
                            controller.updateCursorPosition(cursorPosition)
                        }
                    }

                    Component.onCompleted: {
                        if (controller && textDocument) {
                            controller.attachTextDocument(textDocument)
                        }
                    }

                    Shortcut {
                        sequences: [StandardKey.Save]
                        enabled: controller && controller.isDirty && !controller.isSaving && (!controller.hasConflict || (controller.mergeSessionActive && controller.canSaveConflict))
                        onActivated: {
                            if (controller) {
                                controller.save()
                            }
                        }
                    }

                    Shortcut {
                        sequences: [StandardKey.Find]
                        onActivated: {
                            if (controller) {
                                controller.openSearch()
                                searchBar.focusSearchField()
                            }
                        }
                    }

                    Shortcut {
                        sequences: [StandardKey.Replace]
                        onActivated: {
                            if (controller) {
                                controller.openReplace()
                                searchBar.focusReplaceField()
                            }
                        }
                    }

                    Shortcut {
                        sequence: "Esc"
                        enabled: controller && controller.isSearchOpen
                        onActivated: {
                            if (controller) {
                                controller.closeSearch()
                            }
                            sourceTextArea.forceActiveFocus()
                        }
                    }
                }
            }

            // Busy Overlay for loading or saving
            ColumnLayout {
                anchors.centerIn: parent
                visible: controller && (controller.isLoading || controller.isSaving)
                spacing: 12

                BusyIndicator {
                    Layout.alignment: Qt.AlignHCenter
                    running: controller && (controller.isLoading || controller.isSaving)
                }

                Text {
                    text: controller && controller.isSaving ? "Saving canonical Markdown..." : "Loading source..."
                    color: "#9ca3af"
                    font.pixelSize: 13
                }
            }
        }

        // =====================================================================
        // Docked Status Bar
        // =====================================================================
        MarkdownEditorStatusBar {
            id: statusBar
            objectName: "markdownEditorStatusBar"
            Layout.fillWidth: true
            controller: editorPaneRoot.controller
        }
    }
}
