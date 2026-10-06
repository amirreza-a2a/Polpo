// ============================================================
//  interfaces/desktop/qml/components/MarkdownInlineFlow.qml
//  Reusable Native Inline Content Flow for Text and Images
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15

Item {
    id: inlineFlowRoot
    objectName: "markdownInlineFlow"

    property var segments: []
    property string textFallback: ""
    property real scaleFactor: 1.0
    property var controller: null

    property color textColor: "#e5e7eb"
    property int defaultPixelSize: 14
    property bool fontBold: false
    property bool fontItalic: false
    property real textLineHeight: 1.4

    readonly property bool hasFlowElements: {
        if (!segments || segments.length === 0) return false
        for (var i = 0; i < segments.length; i++) {
            var st = segments[i].segmentType
            if (st === "image") return true
            if (st === "math" && segments[i].hasError) return true
        }
        return false
    }

    // Compatibility alias: preserved for existing components/callers querying hasImages.
    // Retained until downstream visual region delegates are consolidated.
    readonly property bool hasImages: hasFlowElements

    width: parent ? parent.width : 600
    implicitHeight: hasFlowElements ? flowLayout.implicitHeight : singleText.implicitHeight

    // -----------------------------------------------------------------------
    // Fast path: Pure text content without embedded images or math errors
    // -----------------------------------------------------------------------
    Text {
        id: singleText
        visible: !inlineFlowRoot.hasFlowElements
        width: parent.width
        textFormat: Text.RichText
        text: inlineFlowRoot.textFallback !== "" ? inlineFlowRoot.textFallback : (
            inlineFlowRoot.segments && inlineFlowRoot.segments.length > 0 ? inlineFlowRoot.segments[0].textHtml : ""
        )
        color: inlineFlowRoot.textColor
        font.pixelSize: Math.round(inlineFlowRoot.defaultPixelSize * inlineFlowRoot.scaleFactor)
        font.bold: inlineFlowRoot.fontBold
        font.italic: inlineFlowRoot.fontItalic
        lineHeight: inlineFlowRoot.textLineHeight
        wrapMode: Text.WordWrap
        onLinkActivated: if (inlineFlowRoot.controller) inlineFlowRoot.controller.handleLinkClicked(link)
    }

    // -----------------------------------------------------------------------
    // Flow path: Mixed inline text, native images, and interactive math errors
    // -----------------------------------------------------------------------
    Flow {
        id: flowLayout
        visible: inlineFlowRoot.hasFlowElements
        width: parent.width
        spacing: 6

        Repeater {
            model: inlineFlowRoot.hasFlowElements ? (inlineFlowRoot.segments || []) : []

            delegate: Loader {
                sourceComponent: {
                    if (modelData.segmentType === "image") return inlineImageComp
                    if (modelData.segmentType === "math" && modelData.hasError) return inlineMathErrorComp
                    return inlineTextComp
                }

                Component {
                    id: inlineTextComp
                    Text {
                        textFormat: Text.RichText
                        text: modelData.textHtml
                        color: inlineFlowRoot.textColor
                        font.pixelSize: Math.round(inlineFlowRoot.defaultPixelSize * inlineFlowRoot.scaleFactor)
                        font.bold: inlineFlowRoot.fontBold
                        font.italic: inlineFlowRoot.fontItalic
                        lineHeight: inlineFlowRoot.textLineHeight
                        wrapMode: Text.WordWrap
                        onLinkActivated: if (inlineFlowRoot.controller) inlineFlowRoot.controller.handleLinkClicked(link)
                    }
                }

                Component {
                    id: inlineImageComp
                    MarkdownInlineImageItem {
                        imageRef: modelData.imageRef
                        scaleFactor: inlineFlowRoot.scaleFactor
                        controller: inlineFlowRoot.controller
                    }
                }

                Component {
                    id: inlineMathErrorComp
                    Rectangle {
                        id: errorBadge
                        color: "#2a1518"
                        radius: 3
                        border.color: "#7f1d1d"
                        border.width: 1
                        implicitWidth: badgeRow.implicitWidth + 10
                        implicitHeight: Math.max(22, badgeRow.implicitHeight + 4)

                        Row {
                            id: badgeRow
                            anchors.centerIn: parent
                            spacing: 4

                            Text {
                                text: "⚠"
                                color: "#ef4444"
                                font.pixelSize: Math.max(10, Math.round(11 * inlineFlowRoot.scaleFactor))
                                anchors.verticalCenter: parent.verticalCenter
                            }

                            Text {
                                id: formulaText
                                text: modelData.mathTex || ""
                                textFormat: Text.PlainText
                                color: "#fca5a5"
                                font.family: "Monospace"
                                font.pixelSize: Math.round(12 * inlineFlowRoot.scaleFactor)
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }

                        // Dotted underline affordance
                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: 1
                            height: 1
                            color: "#ef4444"
                            opacity: 0.8
                        }

                        MouseArea {
                            id: badgeMouseArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (inlineFlowRoot.controller && typeof inlineFlowRoot.controller.copyToClipboard === "function") {
                                    inlineFlowRoot.controller.copyToClipboard(modelData.mathTex || "")
                                }
                            }
                        }

                        ToolTip.visible: badgeMouseArea.containsMouse && (modelData.errorMessage || modelData.errorCategory || "") !== ""
                        ToolTip.delay: 300
                        ToolTip.timeout: 5000
                        ToolTip.text: {
                            var msg = modelData.errorMessage || "Math rendering error"
                            var cat = modelData.errorCategory ? ("[" + modelData.errorCategory.toUpperCase() + "] ") : ""
                            return cat + msg + "\n(Click to copy TeX)"
                        }
                    }
                }
            }
        }
    }
}
