import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
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

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 20

        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            Text {
                text: "Application Settings"
                color: "#F9FAFB"
                font.pixelSize: 20
                font.bold: true
            }

            Item { Layout.fillWidth: true }

            Text {
                text: root.statusNotice
                color: "#10B981"
                font.pixelSize: 13
                visible: root.statusNotice !== ""
            }
        }

        Card {
            Layout.fillWidth: true
            Layout.preferredHeight: 380

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 16

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 20
                    Text {
                        text: "UI Theme:"
                        color: "#D1D5DB"
                        font.pixelSize: 14
                        Layout.preferredWidth: 200
                    }
                    ComboBox {
                        id: themeCombo
                        Layout.preferredWidth: 220
                        model: ["system", "dark", "light"]
                        currentIndex: {
                            if (typeof settingsController === "undefined" || !settingsController) return 0;
                            var t = settingsController.theme;
                            if (t === "dark") return 1;
                            if (t === "light") return 2;
                            return 0;
                        }
                    }
                    Item { Layout.fillWidth: true }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 20
                    Text {
                        text: "Max Concurrent Jobs:"
                        color: "#D1D5DB"
                        font.pixelSize: 14
                        Layout.preferredWidth: 200
                    }
                    SpinBox {
                        id: concurrencySpin
                        from: 1
                        to: 8
                        value: typeof settingsController !== "undefined" && settingsController ? settingsController.maxConcurrentJobs : 2
                    }
                    Item { Layout.fillWidth: true }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 20
                    Text {
                        text: "Missed Schedule Policy:"
                        color: "#D1D5DB"
                        font.pixelSize: 14
                        Layout.preferredWidth: 200
                    }
                    ComboBox {
                        id: policyCombo
                        Layout.preferredWidth: 220
                        model: ["prompt", "run_immediately", "mark_paused"]
                        currentIndex: {
                            if (typeof settingsController === "undefined" || !settingsController) return 0;
                            var p = settingsController.missedSchedulePolicy;
                            if (p === "run_immediately") return 1;
                            if (p === "mark_paused") return 2;
                            return 0;
                        }
                    }
                    Item { Layout.fillWidth: true }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 20
                    Text {
                        text: "Artifact Retention (Days):"
                        color: "#D1D5DB"
                        font.pixelSize: 14
                        Layout.preferredWidth: 200
                    }
                    SpinBox {
                        id: retentionSpin
                        from: 1
                        to: 365
                        value: typeof settingsController !== "undefined" && settingsController ? settingsController.artifactRetentionDays : 30
                    }
                    Item { Layout.fillWidth: true }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 20
                    CheckBox {
                        id: autoRetryCheck
                        text: "Enable Automatic Retries on Rate Limit"
                        checked: typeof settingsController !== "undefined" && settingsController ? settingsController.autoRetry : true
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 20
                    CheckBox {
                        id: autoPipeline2Check
                        text: "Enable Default Pipeline 2 Markdown Refinement"
                        checked: typeof settingsController !== "undefined" && settingsController ? settingsController.autoPipeline2 : false
                    }
                }

                Item { Layout.fillHeight: true }

                RowLayout {
                    spacing: 16
                    Button {
                        id: savePrefsBtn
                        objectName: "savePreferencesButton"
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

        Item { Layout.fillHeight: true }
    }
}
