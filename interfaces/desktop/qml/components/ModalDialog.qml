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
        color: "#1F2937"
        radius: 8
        border.color: "#4B5563"
        border.width: 1
    }
}
