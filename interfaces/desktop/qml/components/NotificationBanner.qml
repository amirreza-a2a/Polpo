import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property string message: ""
    property string bannerType: "info"
    property bool visibleBanner: message !== ""

    visible: visibleBanner
    implicitWidth: parent ? parent.width : 400
    implicitHeight: 40
    color: bannerType === "error" ? "#7F1D1D" : (bannerType === "warning" ? "#78350F" : "#1E3A8A")

    Row {
        anchors.centerIn: parent
        spacing: 12

        Text {
            text: root.message
            color: "#FFFFFF"
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
