// ============================================================
//  interfaces/desktop/qml/components/MarkdownEditorStatusBar.qml
//  Docked Status Bar for Desktop Markdown Editor
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: statusBarRoot
    objectName: "markdownEditorStatusBar"

    property var controller: null

    Layout.fillWidth: true
    implicitHeight: 24
    color: (typeof theme !== "undefined" && theme) ? theme.surface : "transparent"
    border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
    border.width: 1

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        spacing: 16

        // Cursor Position (1-indexed Line and Column in Qt UTF-16 code units)
        Text {
            id: cursorPositionLabel
            objectName: "editorCursorPositionLabel"
            text: {
                var line = controller ? controller.cursorLine : 1;
                var col = controller ? controller.cursorColumn : 1;
                return "Ln " + line + ", Col " + col;
            }
            color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "gray"
            font.pixelSize: 11
        }

        // Document Metrics (Word and Character counts in Unicode code points)
        Text {
            id: documentMetricsLabel
            objectName: "editorDocumentMetricsLabel"
            text: {
                var words = controller ? controller.wordCount : 0;
                var chars = controller ? controller.characterCount : 0;
                return words + " words • " + chars + " chars";
            }
            color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "gray"
            font.pixelSize: 11
        }

        Item { Layout.fillWidth: true }

        // Encoding and Language Indicator
        Text {
            id: encodingModeLabel
            objectName: "editorEncodingModeLabel"
            text: "UTF-8  •  Markdown"
            color: (typeof theme !== "undefined" && theme) ? theme.textMuted : "gray"
            font.pixelSize: 11
        }
    }
}
