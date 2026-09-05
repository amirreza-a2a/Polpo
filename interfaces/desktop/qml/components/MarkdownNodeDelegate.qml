// ============================================================
//  interfaces/desktop/qml/components/MarkdownNodeDelegate.qml
//  Polymorphic AST Block Delegate with Mixed Inline Segment Support
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: delegateRoot
    objectName: "markdownNodeDelegate"

    property var controller: null
    readonly property real scaleFactor: controller ? controller.scaleFactor : 1.0
    readonly property bool isSelected: controller ? (controller.selectedNodeIndex === index) : false

    width: ListView.view ? ListView.view.width : 800
    implicitHeight: delegateContent.implicitHeight + 16

    Rectangle {
        id: selectionHighlight
        anchors.fill: parent
        color: isSelected ? "#1e293b" : "transparent"
        radius: 4
        visible: isSelected

        Rectangle {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 3
            color: "#3b82f6"
            radius: 1
        }
    }

    Item {
        id: delegateContent
        anchors.top: parent.top
        anchors.topMargin: 8
        anchors.left: parent.left
        anchors.leftMargin: 20
        anchors.right: parent.right
        anchors.rightMargin: 20
        implicitHeight: loader.item ? loader.item.implicitHeight : 0

        Loader {
            id: loader
            width: parent.width
            sourceComponent: {
                var t = model.nodeType
                if (t === "heading") return headingComponent
                if (t === "image") return imageComponent
                if (t === "code_block") return codeBlockComponent
                if (t === "list") return listComponent
                if (t === "blockquote") return blockquoteComponent
                if (t === "thematic_break") return breakComponent
                if (t === "table_fallback") return tableFallbackComponent
                return paragraphComponent
            }
        }
    }

    // -----------------------------------------------------------------------
    // Heading Component
    // -----------------------------------------------------------------------
    Component {
        id: headingComponent
        MarkdownInlineFlow {
            width: parent.width
            segments: model.segments || []
            textFallback: model.content || ""
            fontBold: true
            defaultPixelSize: {
                var l = model.level || 1
                if (l === 1) return 24
                if (l === 2) return 20
                if (l === 3) return 17
                return 15
            }
            textColor: "#f9fafb"
            scaleFactor: delegateRoot.scaleFactor
            controller: delegateRoot.controller
        }
    }

    // -----------------------------------------------------------------------
    // Paragraph Component (Mixed Inline Text & Native Image Flow)
    // -----------------------------------------------------------------------
    Component {
        id: paragraphComponent
        MarkdownInlineFlow {
            width: parent.width
            segments: model.segments || []
            textFallback: model.content || ""
            textColor: "#e5e7eb"
            defaultPixelSize: 14
            scaleFactor: delegateRoot.scaleFactor
            controller: delegateRoot.controller
        }
    }

    // -----------------------------------------------------------------------
    // Standalone Block Image Component
    // -----------------------------------------------------------------------
    Component {
        id: imageComponent
        MarkdownImageCard {
            imageUri: model.imageUri
            altText: model.altText
            regionId: model.primaryRegionId
            occurrenceId: model.primaryOccurrenceId || ""
            displayOrder: model.displayOrder
            isAssociated: model.isAssociated
            scaleFactor: delegateRoot.scaleFactor
            controller: delegateRoot.controller
        }
    }

    // -----------------------------------------------------------------------
    // Code Block Component
    // -----------------------------------------------------------------------
    Component {
        id: codeBlockComponent
        Rectangle {
            width: parent.width
            implicitHeight: codeCol.implicitHeight + 16
            color: "#121216"
            radius: 4
            border.color: "#2a2a35"
            border.width: 1

            ColumnLayout {
                id: codeCol
                anchors.fill: parent
                anchors.margins: 8
                spacing: 4

                Text {
                    visible: model.language !== ""
                    text: model.language
                    color: "#9ca3af"
                    font.pixelSize: 10
                    font.bold: true
                    Layout.alignment: Qt.AlignRight
                }

                Text {
                    text: model.content
                    color: "#f3f4f6"
                    font.family: "Monospace"
                    font.pixelSize: Math.round(12 * scaleFactor)
                    wrapMode: Text.NoWrap
                    Layout.fillWidth: true
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // List Component (Native Inline Images per Item via list_item_segments)
    // -----------------------------------------------------------------------
    Component {
        id: listComponent
        Column {
            id: listCol
            width: parent.width
            spacing: 6

            readonly property var itemSegs: model.listItemSegments || []
            readonly property var rawItems: model.listItems || []
            readonly property int itemCount: Math.max(itemSegs.length, rawItems.length)
            readonly property bool isOrderedList: model.isOrdered || false
            readonly property int startIndexVal: model.startIndex || 1

            Repeater {
                model: listCol.itemCount

                Row {
                    id: itemRow
                    width: listCol.width
                    spacing: 8

                    readonly property var currentRaw: (listCol.rawItems && index < listCol.rawItems.length) ? listCol.rawItems[index] : ""
                    readonly property bool isTaskItem: typeof currentRaw === "string" && (currentRaw.indexOf("☐ ") === 0 || currentRaw.indexOf("☑ ") === 0)

                    Text {
                        visible: !itemRow.isTaskItem
                        text: listCol.isOrderedList ? ((listCol.startIndexVal + index) + ". ") : "• "
                        color: "#9ca3af"
                        font.pixelSize: Math.round(14 * delegateRoot.scaleFactor)
                        font.bold: true
                    }

                    MarkdownInlineFlow {
                        width: itemRow.isTaskItem ? parent.width : (parent.width - 24)
                        segments: (listCol.itemSegs && index < listCol.itemSegs.length) ? listCol.itemSegs[index] : []
                        textFallback: itemRow.currentRaw
                        textColor: "#e5e7eb"
                        defaultPixelSize: 14
                        scaleFactor: delegateRoot.scaleFactor
                        controller: delegateRoot.controller
                    }
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Blockquote Component (Preserving Child Block Hierarchy & Native Inline Images)
    // -----------------------------------------------------------------------
    Component {
        id: blockquoteComponent
        Rectangle {
            id: quoteBox
            width: parent.width
            implicitHeight: quoteCol.implicitHeight + 20
            color: "#16161e"
            radius: 4

            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: 3
                color: "#6366f1"
                radius: 1
            }

            Column {
                id: quoteCol
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 10
                anchors.leftMargin: 16
                spacing: 8

                readonly property var childrenList: (model.quoteChildren && model.quoteChildren.length > 0) ? model.quoteChildren : [
                    { childType: "paragraph", content: model.content || "", level: 0, segments: model.segments || [] }
                ]

                Repeater {
                    model: quoteCol.childrenList

                    MarkdownInlineFlow {
                        width: quoteCol.width
                        segments: modelData.segments || []
                        textFallback: modelData.content || ""
                        fontItalic: modelData.childType !== "heading"
                        fontBold: modelData.childType === "heading"
                        textColor: modelData.childType === "heading" ? "#f9fafb" : "#d1d5db"
                        defaultPixelSize: {
                            if (modelData.childType === "heading") {
                                var l = modelData.level || 1
                                if (l === 1) return 20
                                if (l === 2) return 18
                                if (l === 3) return 16
                                return 14
                            }
                            return 13
                        }
                        scaleFactor: delegateRoot.scaleFactor
                        controller: delegateRoot.controller
                    }
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Thematic Break Component
    // -----------------------------------------------------------------------
    Component {
        id: breakComponent
        Rectangle {
            width: parent.width
            height: 1
            color: "#3a3a48"
        }
    }

    // -----------------------------------------------------------------------
    // Table Fallback Component (Structured cells with Inline Flow support)
    // -----------------------------------------------------------------------
    Component {
        id: tableFallbackComponent
        Rectangle {
            id: tableRect
            width: parent.width
            readonly property var cellRows: model.tableCellSegments || []
            readonly property bool hasStructuredCells: cellRows.length > 0
            implicitHeight: hasStructuredCells ? tableLayout.implicitHeight + 16 : tableText.implicitHeight + 16
            color: "#14141a"
            radius: 4
            border.color: "#2a2a35"
            border.width: 1

            Column {
                id: tableLayout
                visible: tableRect.hasStructuredCells
                anchors.fill: parent
                anchors.margins: 8
                spacing: 6

                Repeater {
                    model: tableRect.cellRows

                    Row {
                        id: rowLayout
                        width: parent.width
                        spacing: 8
                        readonly property var rowData: modelData || []

                        Repeater {
                            model: rowData

                            Rectangle {
                                width: Math.max(80, (rowLayout.width - (rowData.length - 1) * 8) / (rowData.length || 1))
                                implicitHeight: cellFlow.implicitHeight + 8
                                color: "#1a1a24"
                                radius: 3
                                border.color: "#333344"
                                border.width: 1

                                MarkdownInlineFlow {
                                    id: cellFlow
                                    anchors.centerIn: parent
                                    width: parent.width - 8
                                    segments: modelData || []
                                    textColor: "#e5e7eb"
                                    defaultPixelSize: 12
                                    scaleFactor: delegateRoot.scaleFactor
                                    controller: delegateRoot.controller
                                }
                            }
                        }
                    }
                }
            }

            Text {
                id: tableText
                visible: !tableRect.hasStructuredCells
                anchors.fill: parent
                anchors.margins: 8
                text: model.content || ""
                color: "#e5e7eb"
                font.family: "Monospace"
                font.pixelSize: Math.round(12 * scaleFactor)
                wrapMode: Text.NoWrap
            }
        }
    }
}
