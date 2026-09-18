// ============================================================
//  interfaces/desktop/qml/components/ConflictResolutionBar.qml
//  Visual Conflict Resolution Toolbar for Three-Way Merge
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: conflictResolutionBarRoot
    objectName: "conflictResolutionBar"

    property var controller: typeof markdownEditorController !== "undefined" ? markdownEditorController : null

    visible: controller ? controller.mergeSessionActive : false
    implicitHeight: 48
    Layout.preferredHeight: 48

    color: "#261608"
    border.color: "#b45309"
    border.width: 1

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        spacing: 12

        Text {
            text: "⚔️"
            font.pixelSize: 14
        }

        Text {
            id: conflictLabel
            objectName: "conflictLabel"
            Layout.fillWidth: true
            text: controller ? controller.currentConflictLabel : ""
            color: "#fef3c7"
            font.pixelSize: 12
            font.bold: true
            elide: Text.ElideRight
        }

        RowLayout {
            spacing: 6

            Button {
                id: conflictPrevBtn
                objectName: "conflictPrevButton"
                text: "Previous"
                implicitHeight: 28
                enabled: controller && controller.currentConflictIndex > 0
                onClicked: if (controller) controller.prevConflictHunk()
            }

            Button {
                id: conflictNextBtn
                objectName: "conflictNextButton"
                text: "Next"
                implicitHeight: 28
                enabled: controller && controller.currentConflictIndex < controller.totalConflicts - 1
                onClicked: if (controller) controller.nextConflictHunk()
            }
        }

        Rectangle {
            width: 1
            height: 20
            color: "#78350f"
        }

        RowLayout {
            spacing: 6

            Button {
                id: acceptLocalBtn
                objectName: "conflictAcceptLocalButton"
                text: "Accept Local"
                implicitHeight: 28
                onClicked: if (controller) controller.acceptCurrentHunkLocal()
            }

            Button {
                id: acceptIncomingBtn
                objectName: "conflictAcceptIncomingButton"
                text: "Accept Incoming"
                implicitHeight: 28
                onClicked: if (controller) controller.acceptCurrentHunkIncoming()
            }

            Button {
                id: acceptBothBtn
                objectName: "conflictAcceptBothButton"
                text: "Accept Both"
                implicitHeight: 28
                onClicked: if (controller) controller.acceptCurrentHunkBoth()
            }
        }
    }
}
