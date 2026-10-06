// ============================================================
//  interfaces/desktop/qml/views/ReviewWorkspaceView.qml
//  Desktop Dual-Pane Review Workspace View (PDF on Left, Markdown on Right)
// ============================================================

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "../components"

Item {
    id: reviewWorkspaceRoot
    objectName: "reviewWorkspaceView"
    implicitWidth: 1040
    implicitHeight: 700
    Layout.fillWidth: true
    Layout.fillHeight: true

    property var syncCoordinator: typeof reviewWorkspaceSyncCoordinator !== "undefined" ? reviewWorkspaceSyncCoordinator : null
    property bool splitterInitialized: false
    property int exportVersion: 0

    function getActiveJobId() {
        if (typeof markdownViewerController !== "undefined" && markdownViewerController && markdownViewerController.activeJobId > 0) {
            return markdownViewerController.activeJobId;
        }
        if (typeof markdownEditorController !== "undefined" && markdownEditorController && markdownEditorController.activeJobId > 0) {
            return markdownEditorController.activeJobId;
        }
        if (typeof documentViewerController !== "undefined" && documentViewerController && documentViewerController.currentJobId > 0) {
            return documentViewerController.currentJobId;
        }
        return 0;
    }

    function handleExportClicked(type) {
        var jid = getActiveJobId();
        if (jid <= 0) return;
        if (typeof exportController !== "undefined" && exportController) {
            exportController.requestExport(jid, type);
        }
    }

    function initializeSplitter() {
        if (width > 0 && pdfView && rightPane) {
            var half = Math.max(260, Math.floor((width - 4) / 2));
            pdfView.width = half;
            pdfView.SplitView.preferredWidth = half;
            rightPane.width = Math.max(260, width - 4 - half);
            splitterInitialized = true;
        }
        if (height > 0) {
            if (pdfView) pdfView.height = height;
            if (rightPane) rightPane.height = height;
        }
    }

    onWidthChanged: {
        if (!splitterInitialized && width > 0) {
            initializeSplitter();
        }
    }

    Component.onCompleted: {
        initializeSplitter();
    }

    SplitView {
        id: splitView
        objectName: "reviewSplitView"
        anchors.fill: parent
        orientation: Qt.Horizontal

        onHeightChanged: {
            if (pdfView) pdfView.height = height;
            if (rightPane) rightPane.height = height;
        }

        onWidthChanged: {
            if (width >= 520 && pdfView && rightPane) {
                var maxPdf = Math.max(260, width - 4 - 260);
                if (pdfView.width > maxPdf) {
                    pdfView.width = maxPdf;
                    pdfView.SplitView.preferredWidth = maxPdf;
                }
                rightPane.width = Math.max(260, width - 4 - pdfView.width);
            }
        }

        handle: Rectangle {
            implicitWidth: 4
            color: SplitHandle.pressed ? ((typeof theme !== "undefined" && theme) ? theme.accentActive : "blue") : (SplitHandle.hovered ? ((typeof theme !== "undefined" && theme) ? theme.accentHover : "lightblue") : ((typeof theme !== "undefined" && theme) ? theme.border : "gray"))
        }

        // Left Pane: Document (PDF Page) Viewer
        DocumentViewerView {
            id: pdfView
            objectName: "documentViewerView"
            SplitView.minimumWidth: 260
            SplitView.preferredWidth: 500

            Item {
                objectName: "pdfPane"
                anchors.fill: parent
            }
        }

        // Right Pane: Segmented Container hosting Rendered Preview & Source Editor
        Rectangle {
            id: rightPane
            objectName: "markdownPane"
            SplitView.minimumWidth: 260
            SplitView.preferredWidth: 500
            SplitView.fillWidth: true
            color: (typeof theme !== "undefined" && theme) ? theme.background : "black"

            property int currentTab: 0  // 0 = Preview, 1 = Editor, 2 = Dual Pane

            function setTab(tab) {
                currentTab = tab;
                if (typeof markdownViewerController !== "undefined" && markdownViewerController) {
                    markdownViewerController.flushLivePreview();
                }
                if (typeof reviewWorkspaceSyncCoordinator !== "undefined" && reviewWorkspaceSyncCoordinator) {
                    reviewWorkspaceSyncCoordinator.setDualPaneActive(tab === 2);
                }
                if (rightSplitView) {
                    rightSplitView.updateSplitLayout(tab);
                }
            }

            onCurrentTabChanged: {
                if (typeof reviewWorkspaceSyncCoordinator !== "undefined" && reviewWorkspaceSyncCoordinator) {
                    reviewWorkspaceSyncCoordinator.setDualPaneActive(currentTab === 2);
                }
                if (rightSplitView) {
                    rightSplitView.updateSplitLayout(currentTab);
                }
            }

            Component.onCompleted: {
                setTab(currentTab);
            }

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                // Mode Selector Segmented Bar
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 36
                    color: (typeof theme !== "undefined" && theme) ? theme.surface : "transparent"
                    border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
                    border.width: 1

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        spacing: 8

                        Row {
                            spacing: 4

                            Button {
                                id: previewTabBtn
                                objectName: "previewTabButton"
                                text: "Rendered Preview"
                                implicitHeight: 26
                                flat: rightPane.currentTab !== 0
                                highlighted: rightPane.currentTab === 0
                                onClicked: rightPane.setTab(0)
                            }

                            Button {
                                id: editorTabBtn
                                objectName: "editorTabButton"
                                text: "Source Editor"
                                implicitHeight: 26
                                flat: rightPane.currentTab !== 1
                                highlighted: rightPane.currentTab === 1
                                onClicked: rightPane.setTab(1)
                            }

                            Button {
                                id: splitTabBtn
                                objectName: "splitTabButton"
                                text: "Dual Pane"
                                implicitHeight: 26
                                flat: rightPane.currentTab !== 2
                                highlighted: rightPane.currentTab === 2
                                onClicked: rightPane.setTab(2)
                            }
                        }

                        Item { Layout.fillWidth: true }

                        Row {
                            spacing: 6
                            Layout.alignment: Qt.AlignVCenter

                            Button {
                                id: exportMarkdownBtn
                                objectName: "exportMarkdownButton"
                                text: "Export Markdown"
                                implicitHeight: 26
                                enabled: typeof exportController !== "undefined" && exportController && !exportController.isExporting && reviewWorkspaceRoot.getActiveJobId() > 0
                                onClicked: reviewWorkspaceRoot.handleExportClicked("markdown")
                            }

                            Button {
                                id: exportPackageBtn
                                objectName: "exportPackageButton"
                                text: "Export Package (ZIP)"
                                implicitHeight: 26
                                highlighted: true
                                enabled: typeof exportController !== "undefined" && exportController && !exportController.isExporting && reviewWorkspaceRoot.getActiveJobId() > 0
                                onClicked: reviewWorkspaceRoot.handleExportClicked("package")
                            }
                        }
                    }
                }

                // Preview Error Banner
                Rectangle {
                    id: previewErrorBanner
                    objectName: "previewErrorBanner"
                    Layout.fillWidth: true
                    implicitHeight: 28
                    color: (typeof theme !== "undefined" && theme) ? theme.errorBackground : "darkred"
                    border.color: (typeof theme !== "undefined" && theme) ? theme.errorBorder : "red"
                    border.width: 1
                    visible: (typeof markdownViewerController !== "undefined" && markdownViewerController && markdownViewerController.hasPreviewError) ? true : false

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 10
                        anchors.rightMargin: 10

                        Text {
                            id: previewErrorText
                            objectName: "previewErrorText"
                            text: "⚠️ Live preview error: " + (typeof markdownViewerController !== "undefined" && markdownViewerController ? markdownViewerController.previewErrorMessage : "")
                            color: (typeof theme !== "undefined" && theme) ? theme.errorText : "white"
                            font.pixelSize: 11
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }
                }

                // Inner SplitView hosting Source Editor on Left and Rendered Preview on Right
                SplitView {
                    id: rightSplitView
                    objectName: "rightSplitView"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    orientation: Qt.Horizontal

                    function updateSplitLayout(tab) {
                        var handleW = 4;
                        var totalW = width;
                        if (totalW <= 0) return;

                        if (tab === 0) {
                            // Mode 0: Preview Only (100% width)
                            markdownEditorPane.visible = false;
                            markdownEditorPane.width = 0;
                            markdownEditorPane.SplitView.preferredWidth = 0;

                            markdownView.visible = true;
                            markdownView.width = totalW;
                            markdownView.height = height;
                            markdownView.SplitView.preferredWidth = totalW;
                        } else if (tab === 1) {
                            // Mode 1: Editor Only (100% width)
                            markdownEditorPane.visible = true;
                            markdownEditorPane.width = totalW;
                            markdownEditorPane.height = height;
                            markdownEditorPane.SplitView.preferredWidth = totalW;

                            markdownView.visible = false;
                            markdownView.width = 0;
                            markdownView.SplitView.preferredWidth = 0;
                        } else if (tab === 2) {
                            // Mode 2: Dual-Pane Side-by-Side (50% / 50%)
                            var half = Math.floor((totalW - handleW) / 2);
                            markdownEditorPane.visible = true;
                            markdownEditorPane.width = half;
                            markdownEditorPane.height = height;
                            markdownEditorPane.SplitView.preferredWidth = half;

                            markdownView.visible = true;
                            markdownView.width = totalW - handleW - half;
                            markdownView.height = height;
                            markdownView.SplitView.preferredWidth = totalW - handleW - half;
                        }
                    }

                    onHeightChanged: {
                        if (height > 0) {
                            markdownEditorPane.height = height;
                            markdownView.height = height;
                        }
                    }

                    onWidthChanged: {
                        if (width > 0) {
                            if (rightPane.currentTab === 0) {
                                markdownView.width = width;
                                markdownView.SplitView.preferredWidth = width;
                            } else if (rightPane.currentTab === 1) {
                                markdownEditorPane.width = width;
                                markdownEditorPane.SplitView.preferredWidth = width;
                            } else if (rightPane.currentTab === 2) {
                                updateSplitLayout(2);
                            }
                        }
                    }

                    handle: Rectangle {
                        id: rightSplitHandle
                        objectName: "rightSplitHandle"
                        implicitWidth: 4
                        visible: rightPane.currentTab === 2
                        color: SplitHandle.pressed ? ((typeof theme !== "undefined" && theme) ? theme.accentActive : "blue") : (SplitHandle.hovered ? ((typeof theme !== "undefined" && theme) ? theme.accentHover : "lightblue") : ((typeof theme !== "undefined" && theme) ? theme.border : "gray"))
                    }

                    MarkdownEditorPane {
                        id: markdownEditorPane
                        objectName: "markdownEditorPane"
                        syncCoordinator: reviewWorkspaceRoot.syncCoordinator
                        visible: false
                        width: 0
                        height: rightSplitView.height
                        SplitView.preferredWidth: 0
                        SplitView.minimumWidth: visible ? 150 : 0
                    }

                    MarkdownView {
                        id: markdownView
                        objectName: "markdownView"
                        syncCoordinator: reviewWorkspaceRoot.syncCoordinator
                        visible: true
                        height: rightSplitView.height
                        SplitView.minimumWidth: visible ? 150 : 0
                    }
                }
            }
        }
    }

    // Export file dialogs and confirmation modals
    FileDialog {
        id: exportMarkdownFileDialog
        objectName: "exportMarkdownFileDialog"
        title: "Export Standalone Markdown"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Markdown Files (*.md)", "All Files (*)"]
        defaultSuffix: "md"
        onAccepted: {
            var jid = reviewWorkspaceRoot.getActiveJobId();
            if (jid > 0 && typeof exportController !== "undefined" && exportController) {
                exportController.exportMarkdown(jid, selectedFile.toString(), reviewWorkspaceRoot.exportVersion, false);
            }
        }
    }

    FileDialog {
        id: exportPackageFileDialog
        objectName: "exportPackageFileDialog"
        title: "Export Document Package (ZIP)"
        fileMode: FileDialog.SaveFile
        nameFilters: ["ZIP Archives (*.zip)", "All Files (*)"]
        defaultSuffix: "zip"
        onAccepted: {
            var jid = reviewWorkspaceRoot.getActiveJobId();
            if (jid > 0 && typeof exportController !== "undefined" && exportController) {
                exportController.exportPackage(jid, selectedFile.toString(), reviewWorkspaceRoot.exportVersion, false);
            }
        }
    }

    ModalDialog {
        id: saveBeforeExportModal
        objectName: "saveBeforeExportModal"
        title: "Unsaved Changes"
        width: 460
        height: 240

        Column {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 14

            Text {
                text: "Unsaved Changes"
                color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "white"
                font.pixelSize: 16
                font.bold: true
            }

            Text {
                text: "This document has unsaved changes in the editor. Would you like to save your changes before exporting?"
                color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "gray"
                font.pixelSize: 13
                wrapMode: Text.Wrap
                width: parent.width
            }

            Row {
                spacing: 10
                anchors.horizontalCenter: parent.horizontalCenter

                Button {
                    id: saveBeforeExportCancelBtn
                    objectName: "saveBeforeExportCancelButton"
                    text: "Cancel"
                    onClicked: {
                        saveBeforeExportModal.close();
                        if (typeof exportController !== "undefined" && exportController) {
                            exportController.cancelPendingExport();
                        }
                    }
                }

                Button {
                    id: saveBeforeExportConfirmBtn
                    objectName: "saveBeforeExportConfirmButton"
                    text: "Save & Export"
                    highlighted: true
                    onClicked: {
                        saveBeforeExportModal.close();
                        if (typeof exportController !== "undefined" && exportController) {
                            exportController.confirmSaveAndExport();
                        }
                    }
                }
            }
        }
    }

    ModalDialog {
        id: overwriteConfirmModal
        objectName: "overwriteConfirmModal"
        title: "File Already Exists"
        width: 460
        height: 240
        property string destinationPath: ""

        Column {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 14

            Text {
                text: "File Already Exists"
                color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "white"
                font.pixelSize: 16
                font.bold: true
            }

            Text {
                text: "The destination file already exists:\n" + overwriteConfirmModal.destinationPath + "\n\nWould you like to replace it?"
                color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "gray"
                font.pixelSize: 13
                wrapMode: Text.Wrap
                width: parent.width
            }

            Row {
                spacing: 10
                anchors.horizontalCenter: parent.horizontalCenter

                Button {
                    id: overwriteCancelBtn
                    objectName: "overwriteCancelButton"
                    text: "Cancel"
                    onClicked: {
                        overwriteConfirmModal.close();
                        if (typeof exportController !== "undefined" && exportController) {
                            exportController.cancelOverwrite();
                        }
                    }
                }

                Button {
                    id: overwriteReplaceBtn
                    objectName: "overwriteReplaceButton"
                    text: "Replace"
                    highlighted: true
                    onClicked: {
                        overwriteConfirmModal.close();
                        if (typeof exportController !== "undefined" && exportController) {
                            exportController.confirmOverwrite();
                        }
                    }
                }
            }
        }
    }

    Connections {
        target: typeof exportController !== "undefined" ? exportController : null
        function onSaveBeforeExportRequired(jid, expType, ver) {
            reviewWorkspaceRoot.exportVersion = ver;
            saveBeforeExportModal.open();
        }
        function onReadyForDestination(jid, expType, ver) {
            reviewWorkspaceRoot.exportVersion = ver;
            var suggested = exportController.getSuggestedFileName(jid, expType, ver);
            if (expType === "markdown") {
                var baseMd = exportMarkdownFileDialog.currentFolder.toString();
                exportMarkdownFileDialog.currentFile = (baseMd ? baseMd + (baseMd.endsWith("/") ? "" : "/") : "") + suggested;
                exportMarkdownFileDialog.open();
            } else {
                var basePkg = exportPackageFileDialog.currentFolder.toString();
                exportPackageFileDialog.currentFile = (basePkg ? basePkg + (basePkg.endsWith("/") ? "" : "/") : "") + suggested;
                exportPackageFileDialog.open();
            }
        }
        function onOverwriteRequired(jid, expType, destPath, ver) {
            overwriteConfirmModal.destinationPath = destPath;
            overwriteConfirmModal.open();
        }
    }
}
