// ============================================================
//  interfaces/desktop/qml/components/MarkdownInlineFlow.qml
//  Reusable Native Inline Content Flow for Text and Images
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

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

    readonly property bool hasImages: {
        if (!segments || segments.length === 0) return false
        for (var i = 0; i < segments.length; i++) {
            if (segments[i].segmentType === "image") return true
        }
        return false
    }

    width: parent ? parent.width : 600
    implicitHeight: hasImages ? flowLayout.implicitHeight : singleText.implicitHeight

    // -----------------------------------------------------------------------
    // Fast path: Pure text content without embedded images
    // -----------------------------------------------------------------------
    Text {
        id: singleText
        visible: !inlineFlowRoot.hasImages
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
        onLinkActivated: if (controller) controller.handleLinkClicked(link)
    }

    // -----------------------------------------------------------------------
    // Flow path: Mixed inline text and native image segments
    // -----------------------------------------------------------------------
    Flow {
        id: flowLayout
        visible: inlineFlowRoot.hasImages
        width: parent.width
        spacing: 6

        Repeater {
            model: inlineFlowRoot.hasImages ? (inlineFlowRoot.segments || []) : []

            delegate: Loader {
                sourceComponent: modelData.segmentType === "image" ? inlineImageComp : inlineTextComp

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
                        onLinkActivated: if (controller) controller.handleLinkClicked(link)
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
            }
        }
    }
}
