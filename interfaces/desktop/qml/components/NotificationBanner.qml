import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    property string message: ""
    property string bannerType: "info"
    property bool visibleBanner: !!message && message.trim() !== ""

    signal dismissed()

    visible: visibleBanner
    implicitWidth: parent ? parent.width : 400
    implicitHeight: visibleBanner ? Math.max(dismissButton.implicitHeight + 16, messageText.implicitHeight + 16, 40) : 0
    height: visibleBanner ? implicitHeight : 0
    Layout.preferredHeight: implicitHeight
    Layout.fillWidth: true

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

    Text {
        id: messageText
        objectName: "notificationMessageText"
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
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.right: dismissButton.left
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
    }

    Button {
        id: dismissButton
        objectName: "notificationDismissButton"
        text: "Dismiss"
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        onClicked: {
            root.dismissed();
        }
    }
}
