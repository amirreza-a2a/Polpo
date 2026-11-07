import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"

ModalDialog {
    id: root
    objectName: "latexRepairDialog"
    title: "اصلاح فرمول / نمودار با هوش مصنوعی"
    width: 720
    height: 560

    property var proposal: null

    onClosed: {
        if (typeof markdownEditorController !== "undefined" && markdownEditorController) {
            markdownEditorController.rejectRepair();
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        // Header Row
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Text {
                text: "اصلاح فرمول / نمودار با هوش مصنوعی"
                color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                font.pixelSize: 16
                font.bold: true
                Layout.alignment: Qt.AlignVCenter
            }

            Item { Layout.fillWidth: true }

            Rectangle {
                visible: root.proposal !== null && Boolean(root.proposal.validationPassed)
                height: 24
                width: validBadgeText.implicitWidth + 16
                radius: 4
                color: (typeof theme !== "undefined" && theme) ? theme.success : "transparent"
                Layout.alignment: Qt.AlignVCenter

                Text {
                    id: validBadgeText
                    anchors.centerIn: parent
                    text: "✓ کامپایل و اعتبارسنجی موفق"
                    color: (typeof theme !== "undefined" && theme) ? theme.accentText : "transparent"
                    font.pixelSize: 11
                    font.bold: true
                }
            }

            Rectangle {
                visible: root.proposal !== null && !Boolean(root.proposal.validationPassed)
                height: 24
                width: invalidBadgeText.implicitWidth + 16
                radius: 4
                color: (typeof theme !== "undefined" && theme) ? theme.error : "transparent"
                Layout.alignment: Qt.AlignVCenter

                Text {
                    id: invalidBadgeText
                    anchors.centerIn: parent
                    text: "⚠ خطا در اعتبارسنجی"
                    color: (typeof theme !== "undefined" && theme) ? theme.accentText : "transparent"
                    font.pixelSize: 11
                    font.bold: true
                }
            }
        }

        // Summary & Invariants Section
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                text: root.proposal ? (root.proposal.changeSummary || "اصلاح انجام شد.") : ""
                color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                font.pixelSize: 13
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            // Preserved invariants flow
            Flow {
                Layout.fillWidth: true
                spacing: 6
                visible: root.proposal && root.proposal.preservedInvariants && root.proposal.preservedInvariants.length > 0

                Repeater {
                    model: root.proposal ? (root.proposal.preservedInvariants || []) : []
                    Rectangle {
                        height: 20
                        width: invariantText.implicitWidth + 12
                        radius: 3
                        color: (typeof theme !== "undefined" && theme) ? theme.surfaceSunken : "transparent"
                        border.color: (typeof theme !== "undefined" && theme) ? theme.borderSubtle : "transparent"
                        border.width: 1

                        Text {
                            id: invariantText
                            anchors.centerIn: parent
                            text: modelData
                            color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                            font.pixelSize: 10
                        }
                    }
                }
            }
        }

        // Code Comparison Split Cards
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 180
            spacing: 12

            // Original Source Card
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: (typeof theme !== "undefined" && theme) ? theme.errorBackground : "transparent"
                radius: 6
                border.color: (typeof theme !== "undefined" && theme) ? theme.errorBorder : "transparent"
                border.width: 1

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    Text {
                        text: "کد اصلی (دارای خطا)"
                        color: (typeof theme !== "undefined" && theme) ? theme.error : "transparent"
                        font.pixelSize: 11
                        font.bold: true
                    }

                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true

                        TextArea {
                            readOnly: true
                            selectByMouse: true
                            text: root.proposal ? (root.proposal.originalSource || "") : ""
                            font.family: "Monospace"
                            font.pixelSize: 11
                            color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                            background: null
                            wrapMode: TextEdit.WrapAnywhere
                        }
                    }
                }
            }

            // Repaired Candidate Card
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: (typeof theme !== "undefined" && theme) ? theme.surface : "transparent"
                radius: 6
                border.color: (typeof theme !== "undefined" && theme) ? theme.accent : "transparent"
                border.width: 1

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "کد اصلاح‌شده توسط هوش مصنوعی"
                            color: (typeof theme !== "undefined" && theme) ? theme.accent : "transparent"
                            font.pixelSize: 11
                            font.bold: true
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: root.proposal && root.proposal.modelName ? root.proposal.modelName : ""
                            color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                            font.pixelSize: 10
                        }
                    }

                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true

                        TextArea {
                            readOnly: true
                            selectByMouse: true
                            text: root.proposal ? (root.proposal.repairedSource || "") : ""
                            font.family: "Monospace"
                            font.pixelSize: 11
                            color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                            background: null
                            wrapMode: TextEdit.WrapAnywhere
                        }
                    }
                }
            }
        }

        // Live Rendered Vector Preview
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 120
            color: (typeof theme !== "undefined" && theme) ? theme.surfaceSunken : "transparent"
            radius: 6
            border.color: (typeof theme !== "undefined" && theme) ? theme.borderSubtle : "transparent"
            border.width: 1
            visible: root.proposal !== null && Boolean(root.proposal.renderedPreviewXml)

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 4

                Text {
                    text: "پیش‌نمایش خروجی کامپایل‌شده:"
                    color: (typeof theme !== "undefined" && theme) ? theme.textSecondary : "transparent"
                    font.pixelSize: 11
                }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    Image {
                        id: previewImage
                        anchors.centerIn: parent
                        source: (root.proposal && root.proposal.renderedPreviewXml)
                            ? ("data:image/svg+xml;utf8," + encodeURIComponent(root.proposal.renderedPreviewXml))
                            : ""
                        fillMode: Image.PreserveAspectFit
                        sourceSize.height: 80
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }

        // Bottom Action Bar
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Button {
                id: applyBtn
                text: "اعمال اصلاح در سند"
                font.pixelSize: 12
                font.bold: true
                Layout.preferredHeight: 32
                enabled: root.proposal !== null && Boolean(root.proposal.validationPassed)

                background: Rectangle {
                    color: applyBtn.down ? ((typeof theme !== "undefined" && theme) ? theme.accentActive : "transparent") : (applyBtn.hovered ? ((typeof theme !== "undefined" && theme) ? theme.accentHover : "transparent") : ((typeof theme !== "undefined" && theme) ? theme.accent : "transparent"))
                    radius: 4
                }

                contentItem: Text {
                    text: applyBtn.text
                    color: (typeof theme !== "undefined" && theme) ? theme.accentText : "transparent"
                    font: applyBtn.font
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: {
                    if (typeof markdownEditorController !== "undefined" && markdownEditorController) {
                        markdownEditorController.applyRepair();
                    }
                    root.close();
                }
            }

            Button {
                id: copyRepairedBtn
                text: "کپی کد اصلاح‌شده"
                font.pixelSize: 12
                Layout.preferredHeight: 32

                background: Rectangle {
                    color: copyRepairedBtn.down ? ((typeof theme !== "undefined" && theme) ? theme.surfaceActive : "transparent") : (copyRepairedBtn.hovered ? ((typeof theme !== "undefined" && theme) ? theme.surfaceHover : "transparent") : ((typeof theme !== "undefined" && theme) ? theme.surface : "transparent"))
                    radius: 4
                    border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
                    border.width: 1
                }

                contentItem: Text {
                    text: copyRepairedBtn.text
                    color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "transparent"
                    font: copyRepairedBtn.font
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                onClicked: {
                    if (typeof markdownEditorController !== "undefined" && markdownEditorController && root.proposal) {
                        markdownEditorController.copyToClipboard(root.proposal.repairedSource || "");
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
                    if (typeof markdownEditorController !== "undefined" && markdownEditorController) {
                        markdownEditorController.rejectRepair();
                    }
                    root.close();
                }
            }
        }
    }
}
