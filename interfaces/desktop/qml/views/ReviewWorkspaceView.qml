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

    SplitView {
        id: splitView
        objectName: "reviewSplitView"
        anchors.fill: parent
        orientation: Qt.Horizontal

        handle: Rectangle {
            implicitWidth: 4
            color: SplitHandle.pressed ? "#3b82f6" : (SplitHandle.hovered ? "#60a5fa" : "#2a2a35")
        }

        // Left Pane: Document (PDF Page) Viewer
        Item {
            id: pdfPane
            SplitView.preferredWidth: parent.width * 0.5
            SplitView.minimumWidth: 320
            SplitView.fillHeight: true

            DocumentViewerView {
                anchors.fill: parent
            }
        }

        // Right Pane: Rendered Markdown Document Viewer
        Item {
            id: markdownPane
            SplitView.preferredWidth: parent.width * 0.5
            SplitView.minimumWidth: 320
            SplitView.fillHeight: true

            MarkdownView {
                anchors.fill: parent
            }
        }
    }
}
