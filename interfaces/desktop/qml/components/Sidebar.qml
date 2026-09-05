import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property int currentTab: 0
    signal tabSelected(int index)

    width: 220
    color: "#111827"

    Column {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8

        Text {
            text: "PolpoT Desktop"
            color: "#F9FAFB"
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
                color: root.currentTab === index ? "#374151" : "transparent"

                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.name
                    color: root.currentTab === index ? "#60A5FA" : "#9CA3AF"
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
