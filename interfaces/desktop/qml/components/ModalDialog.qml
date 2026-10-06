import QtQuick
import QtQuick.Controls

Popup {
    id: root
    property string title: ""
    modal: true
    focus: true
    anchors.centerIn: parent
    width: 440
    height: 320
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    background: Rectangle {
        color: (typeof theme !== "undefined" && theme) ? theme.surfaceElevated : "transparent"
        radius: 8
        border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
        border.width: 1
    }
}
