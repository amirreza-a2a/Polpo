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

            property int currentTab: 0  // 0 = Preview, 1 = Editor

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
                                onClicked: rightPane.currentTab = 0
                            }

                            Button {
                                id: editorTabBtn
                                objectName: "editorTabButton"
                                text: "Source Editor"
                                implicitHeight: 26
                                flat: rightPane.currentTab !== 1
                                highlighted: rightPane.currentTab === 1
                                onClicked: rightPane.currentTab = 1
                            }
                        }

                        Item { Layout.fillWidth: true }
                    }
                }

                // StackLayout hosting Preview and Editor
                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: rightPane.currentTab

                    MarkdownView {
                        id: markdownView
                        objectName: "markdownView"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                    }

                    MarkdownEditorPane {
                        id: markdownEditorPane
                        objectName: "markdownEditorPane"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                    }
                }
            }
        }
    }
}
