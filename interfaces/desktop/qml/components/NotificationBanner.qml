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
            return t ? t.errorBackground : "transparent";
        } else if (bannerType === "warning") {
            return t ? t.warningBackground : "transparent";
        } else if (bannerType === "success") {
            return t ? t.successBackground : "transparent";
        } else {
            return t ? t.infoBackground : "transparent";
        }
    }
    border.color: {
        var t = (typeof theme !== "undefined" && theme) ? theme : null;
        if (bannerType === "error") {
            return t ? t.errorBorder : "transparent";
        } else if (bannerType === "warning") {
            return t ? t.warningBorder : "transparent";
        } else if (bannerType === "success") {
            return t ? t.successBorder : "transparent";
        } else {
            return t ? t.infoBorder : "transparent";
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
                return t ? t.errorText : "transparent";
            } else if (root.bannerType === "warning") {
                return t ? t.warningText : "transparent";
            } else if (root.bannerType === "success") {
                return t ? t.successText : "transparent";
            } else {
                return t ? t.infoText : "transparent";
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
