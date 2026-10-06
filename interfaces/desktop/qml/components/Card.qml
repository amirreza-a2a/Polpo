import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    implicitWidth: 300
    implicitHeight: 120
    radius: 8
    color: (typeof theme !== "undefined" && theme) ? theme.surface : "transparent"
    border.color: (typeof theme !== "undefined" && theme) ? theme.border : "transparent"
    border.width: 1
}
