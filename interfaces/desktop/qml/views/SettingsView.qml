import QtQuick
import QtQuick.Controls
import "../components"

Item {
    id: root

    Column {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 20

        Text {
            text: "Application Settings"
            color: "#F9FAFB"
            font.pixelSize: 20
            font.bold: true
        }

        Card {
            width: parent.width
            height: 320

            Column {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 16

                Row {
                    spacing: 20
                    Text {
                        text: "Max Concurrent Jobs:"
                        color: "#D1D5DB"
                        font.pixelSize: 14
                        width: 180
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    SpinBox {
                        id: concurrencySpin
                        from: 1
                        to: 8
                        value: settingsController.maxConcurrentJobs
                    }
                }

                Row {
                    spacing: 20
                    Text {
                        text: "Missed Schedule Policy:"
                        color: "#D1D5DB"
                        font.pixelSize: 14
                        width: 180
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    ComboBox {
                        id: policyCombo
                        model: ["prompt", "run_immediately", "mark_paused"]
                        currentIndex: {
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
                        width: 180
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    SpinBox {
                        id: retentionSpin
                        from: 1
                        to: 365
                        value: settingsController.artifactRetentionDays
                    }
                }

                Row {
                    spacing: 20
                    CheckBox {
                        id: autoRetryCheck
                        text: "Enable Automatic Retries on Rate Limit"
                        checked: settingsController.autoRetry
                    }
                }

                Row {
                    spacing: 16
                    Button {
                        text: "Save Preferences"
                        onClicked: {
                            settingsController.save_settings(
                                settingsController.theme,
                                concurrencySpin.value,
                                policyCombo.currentText,
                                retentionSpin.value,
                                autoRetryCheck.checked,
                                settingsController.autoPipeline2
                            );
                        }
                    }

                    Button {
                        text: "Prune Old Artifacts"
                        onClicked: settingsController.prune_artifacts(48)
                    }
                }
            }
        }
    }
}
