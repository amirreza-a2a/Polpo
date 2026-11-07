import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ModalDialog {
    id: root
    objectName: "latexRepairConsentModal"
    title: "ارسال به هوش مصنوعی جهت اصلاح"
    width: 540
    height: 420

    property string nodeId: ""
    property string originalSource: ""
    property string rendererType: ""
    property string diagnostics: ""
    property int errorLine: 0
    property string logExcerpt: ""
    property int startLine: 0
    property int endLine: 0

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        // Header Row
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: "ارسال به هوش مصنوعی جهت اصلاح"
                color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                font.pixelSize: 15
                font.bold: true
                Layout.alignment: Qt.AlignVCenter
            }

            Item { Layout.fillWidth: true }

            Rectangle {
                height: 22
                width: badgeText.implicitWidth + 14
                radius: 3
                color: (typeof theme !== "undefined" && theme) ? theme.surfaceActive : "transparent"
                border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
                border.width: 1

                Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: root.rendererType === "diagram" ? "نمودار TeX" : "فرمول ریاضی"
                    color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                    font.pixelSize: 11
                }
            }
        }

        // Privacy and Data Transmission Disclosure Notice
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: noticeCol.implicitHeight + 16
            radius: 4
            color: (typeof theme !== "undefined" && theme) ? theme.surface : "transparent"
            border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
            border.width: 1

            ColumnLayout {
                id: noticeCol
                anchors.fill: parent
                anchors.margins: 8
                spacing: 4

                Text {
                    text: "اطلاعیه شفافیت و حریم خصوصی:"
                    color: (typeof theme !== "undefined" && theme) ? theme.accent : "transparent"
                    font.pixelSize: 12
                    font.bold: true
                }

                Text {
                    text: "فقط قطعه‌کد معیوب زیر و خطای پالایش‌شده کامپایلر به ارائه‌دهنده هوش مصنوعی ارسال خواهد شد. هیچ بخش دیگری از سند، تصاویر یا فایل‌های محلی ارسال نمی‌شود."
                    color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
        }

        // Code Preview Box
        Text {
            text: "کد ارسالی:"
            color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
            font.pixelSize: 12
            font.bold: true
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: (typeof theme !== "undefined" && theme) ? theme.background : "transparent"
            border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
            border.width: 1
            radius: 4

            ScrollView {
                anchors.fill: parent
                anchors.margins: 8
                clip: true

                TextArea {
                    readOnly: true
                    text: root.originalSource
                    color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                    font.family: "monospace"
                    font.pixelSize: 11
                    wrapMode: TextEdit.Wrap
                    selectByMouse: true
                    background: null
                }
            }
        }

        // Diagnostic Message
        Text {
            text: "خطای گزارش‌شده: " + (root.diagnostics || "(نامشخص)")
            color: (typeof theme !== "undefined" && theme) ? theme.error : "transparent"
            font.pixelSize: 11
            elide: Text.ElideRight
            Layout.fillWidth: true
        }

        // Action Buttons
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Button {
                id: sendBtn
                text: "تأیید و ارسال به هوش مصنوعی"
                font.pixelSize: 12
                font.bold: true
                Layout.preferredHeight: 32

                background: Rectangle {
                    color: sendBtn.down ? ((typeof theme !== "undefined" && theme) ? theme.accentActive : "transparent") : (sendBtn.hovered ? ((typeof theme !== "undefined" && theme) ? theme.accentHover : "transparent") : ((typeof theme !== "undefined" && theme) ? theme.accent : "transparent"))
                    radius: 4
                }

                contentItem: Text {
                    text: sendBtn.text
                    color: (typeof theme !== "undefined" && theme) ? theme.accentText : "transparent"
                    font: sendBtn.font
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: {
                    root.close();
                    if (typeof markdownEditorController !== "undefined" && markdownEditorController && typeof markdownEditorController.requestRepair === "function") {
                        markdownEditorController.requestRepair(
                            root.nodeId,
                            root.originalSource,
                            root.rendererType,
                            root.diagnostics,
                            root.errorLine,
                            root.logExcerpt,
                            root.startLine,
                            root.endLine
                        );
                    }
                }
            }

            Item { Layout.fillWidth: true }

            Button {
                id: cancelBtn
                text: "انصراف"
                font.pixelSize: 12
                Layout.preferredHeight: 32

                background: Rectangle {
                    color: cancelBtn.down ? ((typeof theme !== "undefined" && theme) ? theme.surfaceActive : "transparent") : (cancelBtn.hovered ? ((typeof theme !== "undefined" && theme) ? theme.surfaceHover : "transparent") : "transparent")
                    radius: 4
                    border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
                    border.width: 1
                }

                contentItem: Text {
                    text: cancelBtn.text
                    color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                    font: cancelBtn.font
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: {
                    root.close();
                }
            }
        }
    }
}
