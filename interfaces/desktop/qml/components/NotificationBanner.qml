import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property string message: ""
    property string bannerType: "info"
    property bool visibleBanner: message !== ""

    visible: visibleBanner
    implicitWidth: parent ? parent.width : 400
    color: {
        var t = (typeof theme !== "undefined" && theme) ? theme : null;
        if (bannerType === "error") {
            return t ? t.errorBackground : "darkred";
        } else if (bannerType === "warning") {
            return t ? t.warningBackground : "darkgoldenrod";
        } else if (bannerType === "success") {
            return t ? t.successBackground : "darkgreen";
        } else {
            return t ? t.infoBackground : "darkblue";
        }
    }
    border.color: {
        var t = (typeof theme !== "undefined" && theme) ? theme : null;
        if (bannerType === "error") {
            return t ? t.errorBorder : "red";
        } else if (bannerType === "warning") {
            return t ? t.warningBorder : "goldenrod";
        } else if (bannerType === "success") {
            return t ? t.successBorder : "green";
        } else {
            return t ? t.infoBorder : "blue";
        }
    }
    border.width: 1

    Row {
        anchors.centerIn: parent
        spacing: 12

        Text {
            text: root.message
            color: {
                var t = (typeof theme !== "undefined" && theme) ? theme : null;
                if (root.bannerType === "error") {
                    return t ? t.errorText : "white";
                } else if (root.bannerType === "warning") {
                    return t ? t.warningText : "white";
                } else if (root.bannerType === "success") {
                    return t ? t.successText : "white";
                } else {
                    return t ? t.infoText : "white";
                }
            }
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }

        Button {
            text: "Dismiss"
            anchors.verticalCenter: parent.verticalCenter
            onClicked: root.message = ""
        }
    }
}
