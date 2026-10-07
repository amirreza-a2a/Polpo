import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: mathErrorCardRoot
    objectName: "mathErrorCard"

    property string mathTex: ""
    property string category: ""
    property string errorMessage: ""
    property var controller: null
    property real scaleFactor: 1.0

    readonly property var categoryConfig: {
        var cat = (category || "").toLowerCase();
        var map = {
            "syntax": { label: "Syntax Error", color: "#9a3412" },
            "timeout": { label: "Timeout", color: "#92400e" },
            "crash": { label: "Worker Crash", color: "#991b1b" },
            "buffer_limit": { label: "Buffer Limit", color: "#991b1b" },
            "circuit_breaker": { label: "Circuit Breaker", color: "#991b1b" },
            "degraded": { label: "Degraded", color: "#92400e" }
        };
        if (map[cat]) return map[cat];
        if (!cat) return { label: "Math Error", color: "#7f1d1d" };
        return { label: cat.charAt(0).toUpperCase() + cat.slice(1), color: "#7f1d1d" };
    }

    readonly property string categoryLabel: categoryConfig.label
    readonly property color categoryBadgeColor: categoryConfig.color

    function copyTex() {
        if (controller && typeof controller.copyToClipboard === "function") {
            controller.copyToClipboard(mathTex);
        }
    }

    implicitWidth: Math.min(680, Math.max(300, 560 * scaleFactor))
    implicitHeight: cardLayout.implicitHeight + 20
    radius: 6
    color: (typeof theme !== "undefined" && theme) ? theme.errorBackground : "darkred"
    border.width: 1
    border.color: (typeof theme !== "undefined" && theme) ? theme.errorBorder : "red"

    ColumnLayout {
        id: cardLayout
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: "⚠"
                color: (typeof theme !== "undefined" && theme) ? theme.error : "red"
                font.pixelSize: Math.round(14 * scaleFactor)
                Layout.alignment: Qt.AlignVCenter
            }

            Rectangle {
                height: 22
                width: badgeText.implicitWidth + 14
                radius: 4
                color: mathErrorCardRoot.categoryBadgeColor
                Layout.alignment: Qt.AlignVCenter

                Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: "[" + mathErrorCardRoot.categoryLabel + "]"
                    color: (typeof theme !== "undefined" && theme) ? theme.accentText : "white"
                    font.pixelSize: Math.round(11 * scaleFactor)
                    font.bold: true
                }
            }

            Item {
                Layout.fillWidth: true
            }

            Button {
                id: copyBtn
                text: "Copy TeX"
                font.pixelSize: Math.round(11 * scaleFactor)
                Layout.preferredHeight: 24
                Layout.alignment: Qt.AlignVCenter

                background: Rectangle {
                    color: copyBtn.down ? ((typeof theme !== "undefined" && theme) ? theme.surfaceActive : "gray") : (copyBtn.hovered ? ((typeof theme !== "undefined" && theme) ? theme.surfaceHover : "darkgray") : ((typeof theme !== "undefined" && theme) ? theme.surface : "black"))
                    radius: 3
                    border.color: (typeof theme !== "undefined" && theme) ? theme.border : "gray"
                    border.width: 1
                }

                contentItem: Text {
                    text: copyBtn.text
                    color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "white"
                    font: copyBtn.font
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: mathErrorCardRoot.copyTex()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: Math.min(240, Math.max(48, texInput.implicitHeight + 16))
            color: (typeof theme !== "undefined" && theme) ? theme.codeBackground : "black"
            radius: 4
            border.color: (typeof theme !== "undefined" && theme) ? theme.codeBorder : "gray"
            border.width: 1

            ScrollView {
                anchors.fill: parent
                anchors.margins: 8
                clip: true

                TextEdit {
                    id: texInput
                    readOnly: true
                    selectByMouse: true
                    textFormat: TextEdit.PlainText
                    text: mathErrorCardRoot.mathTex
                    color: (typeof theme !== "undefined" && theme) ? theme.codeText : "white"
                    font.family: "Monospace"
                    font.pixelSize: Math.round(12 * scaleFactor)
                    wrapMode: TextEdit.WrapAnywhere
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            visible: mathErrorCardRoot.errorMessage !== ""

            Text {
                text: "ℹ"
                color: (typeof theme !== "undefined" && theme) ? theme.errorText : "gray"
                font.pixelSize: Math.round(11 * scaleFactor)
                Layout.alignment: Qt.AlignTop
            }

            Text {
                text: mathErrorCardRoot.errorMessage
                color: (typeof theme !== "undefined" && theme) ? theme.errorText : "gray"
                font.pixelSize: Math.round(11 * scaleFactor)
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
    }
}
