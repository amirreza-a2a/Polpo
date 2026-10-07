import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property string status: "pending"
    implicitWidth: label.implicitWidth + 16
    implicitHeight: 24
    radius: 12
    border.width: 1

    color: {
        var t = (typeof theme !== "undefined" && theme) ? theme : null;
        if (!t) return "gray";
        switch (status.toLowerCase()) {
            case "processing":
            case "resuming":
                return t.infoBackground;
            case "done":
            case "saving":
            case "running_now":
                return t.successBackground;
            case "failed":
            case "cancelling":
                return t.errorBackground;
            case "paused":
            case "pausing":
            case "retrying":
                return t.warningBackground;
            case "cancelled":
                return t.surfaceSunken;
            default:
                return t.surfaceElevated;
        }
    }

    border.color: {
        var t = (typeof theme !== "undefined" && theme) ? theme : null;
        if (!t) return "transparent";
        switch (status.toLowerCase()) {
            case "processing":
            case "resuming":
                return t.infoBorder;
            case "done":
            case "saving":
            case "running_now":
                return t.successBorder;
            case "failed":
            case "cancelling":
                return t.errorBorder;
            case "paused":
            case "pausing":
            case "retrying":
                return t.warningBorder;
            default:
                return t.border;
        }
    }

    Text {
        id: label
        anchors.centerIn: parent
        text: {
            switch (root.status.toLowerCase()) {
                case "cancelling": return "CANCELLING…";
                case "pausing": return "PAUSING…";
                case "retrying": return "RETRYING…";
                case "resuming": return "RESUMING…";
                case "saving": return "SAVING…";
                case "running_now": return "STARTING…";
                default: return root.status.toUpperCase();
            }
        }
        color: {
            var t = (typeof theme !== "undefined" && theme) ? theme : null;
            if (!t) return "white";
            switch (root.status.toLowerCase()) {
                case "processing":
                case "resuming":
                    return t.infoText;
                case "done":
                case "saving":
                case "running_now":
                    return t.successText;
                case "failed":
                case "cancelling":
                    return t.errorText;
                case "paused":
                case "pausing":
                case "retrying":
                    return t.warningText;
                case "cancelled":
                    return t.textMuted;
                default:
                    return t.textPrimary;
            }
        }
        font.pixelSize: 11
        font.bold: true
    }
}
