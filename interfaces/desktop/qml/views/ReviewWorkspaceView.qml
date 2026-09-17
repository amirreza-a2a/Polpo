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
        if (width > 0 && pdfView && markdownView) {
            var half = Math.max(260, Math.floor((width - 4) / 2));
            pdfView.width = half;
            pdfView.SplitView.preferredWidth = half;
            markdownView.width = Math.max(260, width - 4 - half);
            splitterInitialized = true;
        }
        if (height > 0) {
            if (pdfView) pdfView.height = height;
            if (markdownView) markdownView.height = height;
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
            if (markdownView) markdownView.height = height;
        }

        onWidthChanged: {
            if (width >= 520 && pdfView && markdownView) {
                var maxPdf = Math.max(260, width - 4 - 260);
                if (pdfView.width > maxPdf) {
                    pdfView.width = maxPdf;
                    pdfView.SplitView.preferredWidth = maxPdf;
                }
                markdownView.width = Math.max(260, width - 4 - pdfView.width);
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

        // Right Pane: Rendered Markdown Document Viewer
        MarkdownView {
            id: markdownView
            objectName: "markdownView"
            SplitView.minimumWidth: 260
            SplitView.preferredWidth: 500
            SplitView.fillWidth: true

            Item {
                objectName: "markdownPane"
                anchors.fill: parent
            }
        }
    }
}
