import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property string status: "pending"
    implicitWidth: label.implicitWidth + 16
    implicitHeight: 24
    radius: 12

    color: {
        switch (status.toLowerCase()) {
            case "processing": return "#1E40AF";
            case "done": return "#065F46";
            case "failed": return "#991B1B";
            case "cancelled": return "#4B5563";
            case "paused": return "#92400E";
            default: return "#374151";
        }
    }

    Text {
        id: label
        anchors.centerIn: parent
        text: root.status.toUpperCase()
        color: "#F9FAFB"
        font.pixelSize: 11
        font.bold: true
    }
}
