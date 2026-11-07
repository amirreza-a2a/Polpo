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
        color: (typeof theme !== "undefined" && theme) ? (isSelected ? theme.selectionBackground : "transparent") : "transparent"
        radius: 4
        visible: isSelected

        Rectangle {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 3
            color: (typeof theme !== "undefined" && theme) ? theme.accent : "transparent"
            radius: 1
        }
    }

    MouseArea {
        id: blockClickArea
        objectName: "nodeClickArea"
        anchors.fill: parent
        onClicked: {
            if (controller) {
                controller.reportNodeClicked(index, controller.modelGeneration())
            }
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
                if (t === "math_block") return mathBlockComponent
                if (t === "diagram") return diagramComponent
                if (t === "raw_block") return rawBlockComponent
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
            textColor: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
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
            textColor: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
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
            color: (typeof theme !== "undefined" && theme) ? theme.codeBackground : "transparent"
            radius: 4
            border.color: (typeof theme !== "undefined" && theme) ? theme.codeBorder : "transparent"
            border.width: 1

            ColumnLayout {
                id: codeCol
                anchors.fill: parent
                anchors.margins: 8
                spacing: 4

                Text {
                    visible: model.language !== ""
                    text: model.language
                    color: (typeof theme !== "undefined" && theme) ? theme.textMuted : "transparent"
                    font.pixelSize: 10
                    font.bold: true
                    Layout.alignment: Qt.AlignRight
                }

                Text {
                    text: model.content
                    color: (typeof theme !== "undefined" && theme) ? theme.codeText : "transparent"
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
                        color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                        font.pixelSize: Math.round(14 * delegateRoot.scaleFactor)
                        font.bold: true
                    }

                    MarkdownInlineFlow {
                        width: itemRow.isTaskItem ? parent.width : (parent.width - 24)
                        segments: (listCol.itemSegs && index < listCol.itemSegs.length) ? listCol.itemSegs[index] : []
                        textFallback: itemRow.currentRaw
                        textColor: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
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
            color: (typeof theme !== "undefined" && theme) ? theme.surfaceSunken : "transparent"
            radius: 4

            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: 3
                color: (typeof theme !== "undefined" && theme) ? theme.accent : "transparent"
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
                        textColor: (typeof theme !== "undefined" && theme) ? (modelData.childType === "heading" ? theme.textPrimary : theme.textSecondary) : "transparent"
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
            color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
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
            color: (typeof theme !== "undefined" && theme) ? theme.surfaceSunken : "transparent"
            radius: 4
            border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
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
                                color: (typeof theme !== "undefined" && theme) ? theme.surfaceElevated : "transparent"
                                radius: 3
                                border.color: (typeof theme !== "undefined" && theme) ? theme.borderSubtle : "transparent"
                                border.width: 1

                                MarkdownInlineFlow {
                                    id: cellFlow
                                    anchors.centerIn: parent
                                    width: parent.width - 8
                                    segments: modelData || []
                                    textColor: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
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
                color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                font.family: "Monospace"
                font.pixelSize: Math.round(12 * scaleFactor)
                wrapMode: Text.NoWrap
            }
        }
    }

    // -----------------------------------------------------------------------
    Component {
        id: mathBlockComponent
        Item {
            id: mathBlockRoot
            width: parent.width
            implicitHeight: (mathContentLoader.item ? mathContentLoader.item.implicitHeight : 0) + 20

            Loader {
                id: mathContentLoader
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width
                sourceComponent: model.mathHasError ? mathErrorComponent : mathSuccessComponent
            }

            Component {
                id: mathSuccessComponent
                Image {
                    anchors.horizontalCenter: parent ? parent.horizontalCenter : undefined
                    source: model.mathHash ? ("image://math/" + ((typeof theme !== "undefined" && theme && theme.resolvedTheme) ? theme.resolvedTheme : "dark") + "/" + model.mathHash) : ""
                    fillMode: Image.PreserveAspectFit
                    sourceSize.width: Math.min(implicitWidth * delegateRoot.scaleFactor, parent ? parent.width - 40 : 760)
                }
            }

            Component {
                id: mathErrorComponent
                MathErrorCard {
                    anchors.horizontalCenter: parent ? parent.horizontalCenter : undefined
                    width: Math.min(parent ? parent.width - 40 : 680, 680)
                    mathTex: model.mathTex || ""
                    category: model.mathErrorCategory || ""
                    errorMessage: model.mathErrorMessage || ""
                    nodeId: model.nodeId || ""
                    errorLine: model.errorLine || 0
                    logExcerpt: model.logExcerpt || ""
                    controller: delegateRoot.controller
                    scaleFactor: delegateRoot.scaleFactor
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Diagram Component (TikZ / PGFPlots / CircuitTikZ)
    // -----------------------------------------------------------------------
    Component {
        id: diagramComponent
        Item {
            id: diagramRoot
            width: parent.width
            implicitHeight: (diagramLoader.item ? diagramLoader.item.implicitHeight : 0) + 16

            Loader {
                id: diagramLoader
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width
                sourceComponent: model.diagramHasError ? diagramErrorComponent : diagramSuccessComponent
            }

            Component {
                id: diagramSuccessComponent
                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: Math.min(
                        (diagramImg.paintedWidth > 0 ? diagramImg.paintedWidth : (diagramImg.implicitWidth * delegateRoot.scaleFactor)) + 24,
                        parent ? parent.width - 20 : 760
                    )
                    implicitHeight: (diagramImg.paintedHeight > 0 ? diagramImg.paintedHeight : diagramImg.implicitHeight) + 20
                    radius: 6
                    color: (typeof theme !== "undefined" && theme && theme.resolvedTheme === "dark") ? theme.textPrimary : "transparent"
                    border.color: (typeof theme !== "undefined" && theme && theme.resolvedTheme === "dark") ? theme.borderSubtle : "transparent"
                    border.width: (typeof theme !== "undefined" && theme && theme.resolvedTheme === "dark") ? 1 : 0

                    Image {
                        id: diagramImg
                        anchors.centerIn: parent
                        source: model.diagramHash ? ("image://diagram/" + ((typeof theme !== "undefined" && theme && theme.resolvedTheme) ? theme.resolvedTheme : "dark") + "/" + model.diagramHash) : ""
                        fillMode: Image.PreserveAspectFit
                        sourceSize.width: Math.min(implicitWidth * delegateRoot.scaleFactor, parent ? parent.width - 44 : 736)
                        asynchronous: true
                        cache: true
                    }
                }
            }

            Component {
                id: diagramErrorComponent
                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: Math.min(parent ? parent.width - 40 : 680, 680)
                    implicitHeight: errorCol.implicitHeight + 20
                    color: (typeof theme !== "undefined" && theme) ? theme.errorBackground : "transparent"
                    radius: 6
                    border.color: (typeof theme !== "undefined" && theme) ? theme.errorBorder : "transparent"
                    border.width: 1

                    Column {
                        id: errorCol
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 6

                        RowLayout {
                            width: parent.width
                            spacing: 8

                            Text {
                                text: "⚠️ LaTeX Diagram Syntax Error"
                                color: (typeof theme !== "undefined" && theme) ? theme.error : "transparent"
                                font.bold: true
                                font.pixelSize: Math.round(13 * delegateRoot.scaleFactor)
                                Layout.alignment: Qt.AlignVCenter
                            }

                            Item {
                                Layout.fillWidth: true
                            }

                            Button {
                                id: diagRepairBtn
                                text: (typeof markdownEditorController !== "undefined" && markdownEditorController && markdownEditorController.isRepairing) ? "در حال اصلاح..." : "اصلاح با هوش مصنوعی"
                                font.pixelSize: Math.round(11 * delegateRoot.scaleFactor)
                                Layout.preferredHeight: 24
                                Layout.alignment: Qt.AlignVCenter
                                enabled: typeof markdownEditorController !== "undefined" && markdownEditorController !== null && !markdownEditorController.isRepairing

                                background: Rectangle {
                                    color: diagRepairBtn.down ? ((typeof theme !== "undefined" && theme) ? theme.accentActive : "transparent") : (diagRepairBtn.hovered ? ((typeof theme !== "undefined" && theme) ? theme.accentHover : "transparent") : ((typeof theme !== "undefined" && theme) ? theme.accent : "transparent"))
                                    radius: 3
                                }

                                contentItem: Text {
                                    text: diagRepairBtn.text
                                    color: (typeof theme !== "undefined" && theme) ? theme.accentText : "transparent"
                                    font: diagRepairBtn.font
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                onClicked: {
                                    if (typeof markdownEditorController !== "undefined" && markdownEditorController && typeof markdownEditorController.requestRepair === "function") {
                                        markdownEditorController.requestRepair(
                                            model.nodeId || ("diagram_" + index),
                                            model.diagramSource,
                                            "diagram",
                                            model.diagramErrorMessage,
                                            model.diagramErrorLine || 0,
                                            model.diagramLogExcerpt || ""
                                        );
                                    }
                                }
                            }

                            Button {
                                id: diagCopyBtn
                                text: "کپی کد"
                                font.pixelSize: Math.round(11 * delegateRoot.scaleFactor)
                                Layout.preferredHeight: 24
                                Layout.alignment: Qt.AlignVCenter

                                background: Rectangle {
                                    color: diagCopyBtn.down ? ((typeof theme !== "undefined" && theme) ? theme.surfaceActive : "transparent") : (diagCopyBtn.hovered ? ((typeof theme !== "undefined" && theme) ? theme.surfaceHover : "transparent") : ((typeof theme !== "undefined" && theme) ? theme.surface : "transparent"))
                                    radius: 3
                                    border.color: (typeof theme !== "undefined" && theme) ? (diagCopyBtn.visualFocus ? theme.focusRing : theme.border) : "transparent"
                                    border.width: 1
                                }

                                contentItem: Text {
                                    text: diagCopyBtn.text
                                    color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                                    font: diagCopyBtn.font
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                onClicked: {
                                    if (typeof markdownEditorController !== "undefined" && markdownEditorController && typeof markdownEditorController.copyToClipboard === "function") {
                                        markdownEditorController.copyToClipboard(model.diagramSource || "");
                                    }
                                }
                            }
                        }

                        Text {
                            width: parent.width
                            text: model.diagramErrorMessage || "Compilation error"
                            color: (typeof theme !== "undefined" && theme) ? theme.errorText : "transparent"
                            font.family: "Monospace"
                            font.pixelSize: Math.round(11 * delegateRoot.scaleFactor)
                            wrapMode: Text.Wrap
                        }

                        Rectangle {
                            visible: Boolean(model.diagramSource)
                            width: parent.width
                            implicitHeight: srcText.implicitHeight + 12
                            color: (typeof theme !== "undefined" && theme) ? theme.surfaceSunken : "transparent"
                            radius: 4
                            border.color: (typeof theme !== "undefined" && theme) ? theme.borderSubtle : "transparent"
                            border.width: 1

                            Text {
                                id: srcText
                                anchors.fill: parent
                                anchors.margins: 6
                                text: model.diagramSource || ""
                                color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                                font.family: "Monospace"
                                font.pixelSize: Math.round(11 * delegateRoot.scaleFactor)
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Raw Block Component
    // -----------------------------------------------------------------------
    Component {
        id: rawBlockComponent
        Rectangle {
            width: parent.width
            implicitHeight: rawText.implicitHeight + 16
            color: (typeof theme !== "undefined" && theme) ? theme.surfaceSunken : "transparent"
            radius: 4
            border.color: (typeof theme !== "undefined" && theme) ? theme.borderSubtle : "transparent"
            border.width: 1

            Text {
                id: rawText
                anchors.fill: parent
                anchors.margins: 8
                text: model.content || ""
                color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                font.family: "Monospace"
                font.pixelSize: Math.round(12 * delegateRoot.scaleFactor)
                wrapMode: Text.Wrap
            }
        }
    }
}
