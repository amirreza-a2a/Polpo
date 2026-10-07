import QtQuick
import QtQuick.Controls

Item {
    id: root
    property real value: 0.0
    implicitWidth: 200
    implicitHeight: 8

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: (typeof theme !== "undefined" && theme) ? theme.surfaceHover : "gray"
    }

    Rectangle {
        width: Math.max(0, Math.min(parent.width, parent.width * (root.value / 100.0)))
        height: parent.height
        radius: height / 2
        color: (typeof theme !== "undefined" && theme) ? theme.accent : "blue"
    }
}
