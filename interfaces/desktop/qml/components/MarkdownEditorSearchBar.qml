// ============================================================
//  interfaces/desktop/qml/components/MarkdownEditorSearchBar.qml
//  Docked Search and Replace Bar for Desktop Markdown Editor
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: searchBarRoot
    objectName: "markdownEditorSearchBar"

    property var controller: null
    property var targetTextArea: null

    function focusSearchField() {
        searchField.forceActiveFocus();
        searchField.selectAll();
    }

    function focusReplaceField() {
        replaceField.forceActiveFocus();
        replaceField.selectAll();
    }

    visible: controller ? controller.isSearchOpen : false
    implicitHeight: visible ? (controller.isReplaceOpen ? 76 : 40) : 0
    color: "#18181f"
    border.color: "#272732"
    border.width: 1
    clip: true

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        anchors.topMargin: 6
        anchors.bottomMargin: 6
        spacing: 4

        // =====================================================================
        // Row 1: Find Row
        // =====================================================================
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            spacing: 8

            Text {
                text: "🔍"
                font.pixelSize: 12
            }

            TextField {
                id: searchField
                objectName: "editorSearchField"
                Layout.preferredWidth: 200
                implicitHeight: 26
                placeholderText: "Find in document..."
                color: "#f3f4f6"
                placeholderTextColor: "#6b7280"
                font.pixelSize: 12
                background: Rectangle {
                    color: "#0f0f13"
                    border.color: searchField.activeFocus ? "#3b82f6" : "#272732"
                    border.width: 1
                    radius: 3
                }

                text: controller ? controller.searchQuery : ""
                onTextChanged: {
                    if (controller && text !== controller.searchQuery) {
                        controller.setSearchQuery(text)
                    }
                }

                Keys.onReturnPressed: {
                    if (!controller) return;
                    var pos = targetTextArea ? targetTextArea.cursorPosition : -1;
                    if (event.modifiers & Qt.ShiftModifier) {
                        controller.findPrevious(pos);
                    } else {
                        controller.findNext(pos);
                    }
                }

                Keys.onEscapePressed: {
                    if (controller) controller.closeSearch();
                    if (targetTextArea) targetTextArea.forceActiveFocus();
                }
            }

            // Match counter label
            Text {
                id: matchCountLabel
                objectName: "editorMatchCountLabel"
                Layout.preferredWidth: 80
                text: {
                    if (!controller || !controller.searchQuery) return "";
                    if (controller.searchTotalMatches === 0) return "No matches";
                    return controller.searchMatchIndex + " of " + controller.searchTotalMatches;
                }
                color: controller && controller.searchTotalMatches === 0 && controller.searchQuery ? "#f87171" : "#9ca3af"
                font.pixelSize: 11
                elide: Text.ElideRight
            }

            // Previous Button (↑)
            Button {
                id: prevButton
                objectName: "editorFindPrevButton"
                text: "↑"
                implicitWidth: 26
                implicitHeight: 26
                enabled: controller && controller.searchTotalMatches > 0
                onClicked: {
                    if (controller) {
                        var pos = targetTextArea ? targetTextArea.cursorPosition : -1;
                        controller.findPrevious(pos);
                    }
                }
            }

            // Next Button (↓)
            Button {
                id: nextButton
                objectName: "editorFindNextButton"
                text: "↓"
                implicitWidth: 26
                implicitHeight: 26
                enabled: controller && controller.searchTotalMatches > 0
                onClicked: {
                    if (controller) {
                        var pos = targetTextArea ? targetTextArea.cursorPosition : -1;
                        controller.findNext(pos);
                    }
                }
            }

            // Case Sensitive Toggle (Aa)
            Button {
                id: caseButton
                objectName: "editorCaseSensitiveButton"
                text: "Aa"
                implicitWidth: 28
                implicitHeight: 26
                checkable: true
                checked: controller ? controller.searchCaseSensitive : false
                highlighted: checked
                onClicked: {
                    if (controller) {
                        controller.setSearchCaseSensitive(!controller.searchCaseSensitive);
                    }
                }
            }

            // Whole Word Toggle (\b)
            Button {
                id: wholeWordButton
                objectName: "editorWholeWordButton"
                text: "\\b"
                implicitWidth: 28
                implicitHeight: 26
                checkable: true
                checked: controller ? controller.searchWholeWord : false
                highlighted: checked
                onClicked: {
                    if (controller) {
                        controller.setSearchWholeWord(!controller.searchWholeWord);
                    }
                }
            }

            // Replace Row Toggle Button
            Button {
                id: toggleReplaceButton
                objectName: "editorToggleReplaceButton"
                text: controller && controller.isReplaceOpen ? "▼ Replace" : "▶ Replace"
                implicitHeight: 26
                flat: true
                onClicked: {
                    if (controller) {
                        if (controller.isReplaceOpen) {
                            controller.closeReplace();
                        } else {
                            controller.openReplace();
                        }
                    }
                }
            }

            Item { Layout.fillWidth: true }

            // Close Button (✕)
            Button {
                id: closeButton
                objectName: "editorCloseSearchButton"
                text: "✕"
                implicitWidth: 26
                implicitHeight: 26
                flat: true
                onClicked: {
                    if (controller) controller.closeSearch();
                    if (targetTextArea) targetTextArea.forceActiveFocus();
                }
            }
        }

        // =====================================================================
        // Row 2: Replace Row (Visible when isReplaceOpen)
        // =====================================================================
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            visible: controller ? controller.isReplaceOpen : false
            spacing: 8

            Text {
                text: "⇄"
                font.pixelSize: 12
            }

            TextField {
                id: replaceField
                objectName: "editorReplaceField"
                Layout.preferredWidth: 200
                implicitHeight: 26
                placeholderText: "Replace with..."
                color: "#f3f4f6"
                placeholderTextColor: "#6b7280"
                font.pixelSize: 12
                background: Rectangle {
                    color: "#0f0f13"
                    border.color: replaceField.activeFocus ? "#3b82f6" : "#272732"
                    border.width: 1
                    radius: 3
                }

                text: controller ? controller.replaceQuery : ""
                onTextChanged: {
                    if (controller && text !== controller.replaceQuery) {
                        controller.setReplaceQuery(text);
                    }
                }

                Keys.onReturnPressed: {
                    if (controller && targetTextArea) {
                        controller.replaceCurrent(targetTextArea.selectionStart, targetTextArea.selectionEnd);
                    }
                }

                Keys.onEscapePressed: {
                    if (controller) controller.closeSearch();
                    if (targetTextArea) targetTextArea.forceActiveFocus();
                }
            }

            Button {
                id: replaceButton
                objectName: "editorReplaceButton"
                text: "Replace"
                implicitHeight: 26
                enabled: controller && controller.searchTotalMatches > 0
                onClicked: {
                    if (controller && targetTextArea) {
                        controller.replaceCurrent(targetTextArea.selectionStart, targetTextArea.selectionEnd);
                    }
                }
            }

            Button {
                id: replaceAllButton
                objectName: "editorReplaceAllButton"
                text: "Replace All"
                implicitHeight: 26
                enabled: controller && controller.searchTotalMatches > 0
                onClicked: {
                    if (controller) {
                        controller.replaceAll();
                    }
                }
            }

            Item { Layout.fillWidth: true }
        }
    }
}
