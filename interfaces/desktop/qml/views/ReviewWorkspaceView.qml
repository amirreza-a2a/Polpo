// ============================================================
//  interfaces/desktop/qml/views/ReviewWorkspaceView.qml
//  Desktop Dual-Pane Review Workspace View (PDF on Left, Markdown on Right)
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import "../components"

Item {
    id: reviewWorkspaceRoot
    objectName: "reviewWorkspaceView"
    implicitWidth: 1040
    implicitHeight: 700
    Layout.fillWidth: true
    Layout.fillHeight: true

    property bool splitterInitialized: false

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
            color: SplitHandle.pressed ? "#3b82f6" : (SplitHandle.hovered ? "#60a5fa" : "#2a2a35")
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
            color: "#0f0f13"

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
                    color: "#13131a"
                    border.color: "#272732"
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
                    }
                }

                // Preview Error Banner
                Rectangle {
                    id: previewErrorBanner
                    objectName: "previewErrorBanner"
                    Layout.fillWidth: true
                    implicitHeight: 28
                    color: "#3b1c1c"
                    border.color: "#ef4444"
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
                            color: "#fca5a5"
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
                        color: SplitHandle.pressed ? "#3b82f6" : (SplitHandle.hovered ? "#60a5fa" : "#2a2a35")
                    }

                    MarkdownEditorPane {
                        id: markdownEditorPane
                        objectName: "markdownEditorPane"
                        visible: false
                        width: 0
                        height: rightSplitView.height
                        SplitView.preferredWidth: 0
                        SplitView.minimumWidth: visible ? 150 : 0
                    }

                    MarkdownView {
                        id: markdownView
                        objectName: "markdownView"
                        visible: true
                        height: rightSplitView.height
                        SplitView.minimumWidth: visible ? 150 : 0
                    }
                }
            }
        }
    }
}
