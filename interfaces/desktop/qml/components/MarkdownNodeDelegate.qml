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
        Text {
            width: parent.width
            textFormat: Text.RichText
            text: model.content
            color: "#f9fafb"
            wrapMode: Text.WordWrap
            font.bold: true
            font.pixelSize: {
                var l = model.level || 1
                if (l === 1) return Math.round(24 * scaleFactor)
                if (l === 2) return Math.round(20 * scaleFactor)
                if (l === 3) return Math.round(17 * scaleFactor)
                return Math.round(15 * scaleFactor)
            }
            onLinkActivated: if (controller) controller.handleLinkClicked(link)
        }
    }

    // -----------------------------------------------------------------------
    // Paragraph Component (Mixed Inline Text & Native Image Flow)
    // -----------------------------------------------------------------------
    Component {
        id: paragraphComponent
        Item {
            id: paraItem
            width: parent.width
            implicitHeight: hasMultipleSegments ? flowLayout.implicitHeight : simpleText.implicitHeight

            readonly property bool hasMultipleSegments: model.segments && model.segments.length > 1

            // Simple fast path for pure text paragraphs
            Text {
                id: simpleText
                visible: !paraItem.hasMultipleSegments
                width: parent.width
                textFormat: Text.RichText
                text: model.content
                color: "#e5e7eb"
                font.pixelSize: Math.round(14 * scaleFactor)
                lineHeight: 1.4
                wrapMode: Text.WordWrap
                onLinkActivated: if (controller) controller.handleLinkClicked(link)
            }

            // Mixed inline layout for paragraphs with embedded images
            Flow {
                id: flowLayout
                visible: paraItem.hasMultipleSegments
                width: parent.width
                spacing: 6

                Repeater {
                    model: model.segments || []

                    delegate: Loader {
                        sourceComponent: modelData.segmentType === "image" ? inlineImageComp : inlineTextComp

                        Component {
                            id: inlineTextComp
                            Text {
                                textFormat: Text.RichText
                                text: modelData.textHtml
                                color: "#e5e7eb"
                                font.pixelSize: Math.round(14 * scaleFactor)
                                lineHeight: 1.4
                                wrapMode: Text.WordWrap
                                onLinkActivated: if (controller) controller.handleLinkClicked(link)
                            }
                        }

                        Component {
                            id: inlineImageComp
                            MarkdownInlineImageItem {
                                imageRef: modelData.imageRef
                                scaleFactor: delegateRoot.scaleFactor
                                controller: delegateRoot.controller
                            }
                        }
                    }
                }
            }
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
    // List Component
    // -----------------------------------------------------------------------
    Component {
        id: listComponent
        Text {
            width: parent.width
            textFormat: Text.RichText
            text: model.content
            color: "#e5e7eb"
            font.pixelSize: Math.round(14 * scaleFactor)
            lineHeight: 1.4
            wrapMode: Text.WordWrap
            onLinkActivated: if (controller) controller.handleLinkClicked(link)
        }
    }

    // -----------------------------------------------------------------------
    // Blockquote Component
    // -----------------------------------------------------------------------
    Component {
        id: blockquoteComponent
        Rectangle {
            width: parent.width
            implicitHeight: quoteText.implicitHeight + 16
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

            Text {
                id: quoteText
                anchors.fill: parent
                anchors.margins: 10
                anchors.leftMargin: 16
                textFormat: Text.RichText
                text: model.content
                color: "#d1d5db"
                font.italic: true
                font.pixelSize: Math.round(13 * scaleFactor)
                wrapMode: Text.WordWrap
                onLinkActivated: if (controller) controller.handleLinkClicked(link)
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
    // Table Fallback Component
    // -----------------------------------------------------------------------
    Component {
        id: tableFallbackComponent
        Rectangle {
            width: parent.width
            implicitHeight: tableText.implicitHeight + 16
            color: "#14141a"
            radius: 4
            border.color: "#2a2a35"
            border.width: 1

            Text {
                id: tableText
                anchors.fill: parent
                anchors.margins: 8
                text: model.content
                color: "#e5e7eb"
                font.family: "Monospace"
                font.pixelSize: Math.round(12 * scaleFactor)
                wrapMode: Text.NoWrap
            }
        }
    }
}
