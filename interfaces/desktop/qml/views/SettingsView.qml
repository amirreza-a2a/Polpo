import QtQuick
import QtQuick.Controls
import "../components"

Item {
    id: root
    property string statusNotice: ""

    Connections {
        target: settingsController
        function onSettings_changed() {
            root.statusNotice = "Preferences saved and applied.";
        }
    }

    Column {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 20

        Row {
            width: parent.width
            spacing: 16

            Text {
                text: "Application Settings"
                color: "#F9FAFB"
                font.pixelSize: 20
                font.bold: true
                anchors.verticalCenter: parent.verticalCenter
            }

            Item { width: 1; height: 1; anchors.fill: parent }

            Text {
                text: root.statusNotice
                color: "#10B981"
                font.pixelSize: 13
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        Card {
            width: parent.width
            height: 380

            Column {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 16

                Row {
                    spacing: 20
                    Text {
                        text: "UI Theme:"
                        color: "#D1D5DB"
                        font.pixelSize: 14
                        width: 200
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    ComboBox {
                        id: themeCombo
                        width: 220
                        model: ["system", "dark", "light"]
                        currentIndex: {
                            if (typeof settingsController === "undefined" || !settingsController) return 0;
                            var t = settingsController.theme;
                            if (t === "dark") return 1;
                            if (t === "light") return 2;
                            return 0;
                        }
                    }
                }

                Row {
                    spacing: 20
                    Text {
                        text: "Max Concurrent Jobs:"
                        color: "#D1D5DB"
                        font.pixelSize: 14
                        width: 200
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    SpinBox {
                        id: concurrencySpin
                        from: 1
                        to: 8
                        value: typeof settingsController !== "undefined" && settingsController ? settingsController.maxConcurrentJobs : 2
                    }
                }

                Row {
                    spacing: 20
                    Text {
                        text: "Missed Schedule Policy:"
                        color: "#D1D5DB"
                        font.pixelSize: 14
                        width: 200
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    ComboBox {
                        id: policyCombo
                        width: 220
                        model: ["prompt", "run_immediately", "mark_paused"]
                        currentIndex: {
                            if (typeof settingsController === "undefined" || !settingsController) return 0;
                            var p = settingsController.missedSchedulePolicy;
                            if (p === "run_immediately") return 1;
                            if (p === "mark_paused") return 2;
                            return 0;
                        }
                    }
                }

                Row {
                    spacing: 20
                    Text {
                        text: "Artifact Retention (Days):"
                        color: "#D1D5DB"
                        font.pixelSize: 14
                        width: 200
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    SpinBox {
                        id: retentionSpin
                        from: 1
                        to: 365
                        value: typeof settingsController !== "undefined" && settingsController ? settingsController.artifactRetentionDays : 30
                    }
                }

                Row {
                    spacing: 20
                    CheckBox {
                        id: autoRetryCheck
                        text: "Enable Automatic Retries on Rate Limit"
                        checked: typeof settingsController !== "undefined" && settingsController ? settingsController.autoRetry : true
                    }
                }

                Row {
                    spacing: 20
                    CheckBox {
                        id: autoPipeline2Check
                        text: "Enable Default Pipeline 2 Markdown Refinement"
                        checked: typeof settingsController !== "undefined" && settingsController ? settingsController.autoPipeline2 : false
                    }
                }

                Row {
                    spacing: 16
                    Button {
                        text: "Save Preferences"
                        highlighted: true
                        onClicked: {
                            settingsController.save_settings(
                                themeCombo.currentText,
                                concurrencySpin.value,
                                policyCombo.currentText,
                                retentionSpin.value,
                                autoRetryCheck.checked,
                                autoPipeline2Check.checked
                            );
                        }
                    }

                    Button {
                        text: "Prune Old Artifacts"
                        onClicked: {
                            var pruned = settingsController.prune_artifacts(48);
                            root.statusNotice = "Pruned " + pruned + " expired artifacts.";
                        }
                    }
                }
            }
        }
    }
}
