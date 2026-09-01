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
            case "cancelling": return "#7F1D1D";
            case "paused": return "#92400E";
            case "retrying": return "#B45309";
            case "resuming": return "#1D4ED8";
            case "saving": return "#047857";
            default: return "#374151";
        }
    }

    Text {
        id: label
        anchors.centerIn: parent
        text: {
            switch (root.status.toLowerCase()) {
                case "cancelling": return "CANCELLING…";
                case "retrying": return "RETRYING…";
                case "resuming": return "RESUMING…";
                case "saving": return "SAVING…";
                default: return root.status.toUpperCase();
            }
        }
        color: "#F9FAFB"
        font.pixelSize: 11
        font.bold: true
    }
}
