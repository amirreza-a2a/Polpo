import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property int currentTab: 0
    signal tabSelected(int index)

    width: 220
    color: (typeof theme !== "undefined" && theme) ? theme.surfaceSunken : "transparent"

    Column {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8

        Text {
            text: "PolpoT Desktop"
            color: (typeof theme !== "undefined" && theme) ? theme.textPrimary : "white"
            font.pixelSize: 18
            font.bold: true
            bottomPadding: 16
        }

        Repeater {
            model: [
                { name: "Active Queue", icon: "queue" },
                { name: "Job History", icon: "history" },
                { name: "Quick Convert", icon: "image" },
                { name: "API Keys", icon: "key" },
                { name: "Prompts", icon: "edit" },
                { name: "Settings", icon: "settings" },
                { name: "Review Workspace", icon: "document" }
            ]

            Rectangle {
                width: parent.width
                height: 40
                radius: 6
                color: root.currentTab === index ? ((typeof theme !== "undefined" && theme) ? theme.surfaceHover : "gray") : "transparent"

                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.name
                    color: root.currentTab === index ? ((typeof theme !== "undefined" && theme) ? theme.accent : "blue") : ((typeof theme !== "undefined" && theme) ? theme.textSecondary : "gray")
                    font.pixelSize: 14
                    font.bold: root.currentTab === index
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        root.currentTab = index;
                        root.tabSelected(index);
                    }
                }
            }
        }
    }
}
