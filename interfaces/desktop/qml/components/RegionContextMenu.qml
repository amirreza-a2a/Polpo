// ============================================================
//  interfaces/desktop/qml/components/RegionContextMenu.qml
//  Generic Dynamic Context Menu for Visual Regions
// ============================================================

import QtQuick 2.15
import QtQuick.Controls 2.15

Menu {
    id: contextMenuRoot
    objectName: "regionContextMenu"

    property var controller: null
    property string contextRegionId: ""

    // Dynamic MenuItem template
    Component {
        id: menuItemComponent
        MenuItem {
            id: menuItem
            hoverEnabled: true

            property string actionId: ""
            property string disabledReason: ""
            readonly property string tipText: ToolTip.text
            readonly property bool tipVisible: ToolTip.visible

            ToolTip.text: disabledReason
            ToolTip.visible: hovered && !enabled && (disabledReason !== "")
            ToolTip.delay: 300

            onTriggered: {
                if (actionId && contextMenuRoot.contextRegionId && contextMenuRoot.controller) {
                    contextMenuRoot.controller.executeRegionAction(actionId, contextMenuRoot.contextRegionId);
                }
            }
        }
    }

    // Dynamic MenuSeparator template
    Component {
        id: menuSeparatorComponent
        MenuSeparator {}
    }

    // Safely removes and destroys all dynamic items
    function clearItems() {
        while (contextMenuRoot.count > 0) {
            var it = contextMenuRoot.takeItem(0);
            if (it) {
                it.destroy();
            }
        }
    }

    // Opens context menu populated with actions for targeted region
    function showForRegion(targetItem, mx, my, regionId) {
        clearItems();
        contextRegionId = regionId ? String(regionId) : "";
        if (!controller || !contextRegionId) {
            return;
        }

        var rawActions = controller.getRegionContextActions(contextRegionId);
        if (!rawActions || rawActions.length === 0) {
            return;
        }

        var visibleActions = [];
        for (var i = 0; i < rawActions.length; i++) {
            if (rawActions[i].is_visible) {
                visibleActions.push(rawActions[i]);
            }
        }
        if (visibleActions.length === 0) {
            return;
        }

        visibleActions.sort(function(a, b) {
            return (a.order || 0) - (b.order || 0);
        });

        var lastGroup = "";
        for (var j = 0; j < visibleActions.length; j++) {
            var act = visibleActions[j];
            if (j > 0 && act.group !== lastGroup) {
                var sep = menuSeparatorComponent.createObject(contextMenuRoot.contentItem);
                if (sep) {
                    contextMenuRoot.addItem(sep);
                }
            }
            lastGroup = act.group;

            var item = menuItemComponent.createObject(contextMenuRoot.contentItem, {
                "actionId": act.action_id || "",
                "text": act.label || "",
                "enabled": act.is_enabled === true,
                "visible": act.is_visible === true,
                "disabledReason": act.disabled_reason || "",
                "objectName": "menuItem_" + (act.action_id || "")
            });
            if (item) {
                contextMenuRoot.addItem(item);
            }
        }

        var posX = (typeof mx === "number") ? mx : 0;
        var posY = (typeof my === "number") ? my : 0;
        if (targetItem) {
            popup(targetItem, posX, posY);
        } else {
            popup(posX, posY);
        }
    }

    onClosed: {
        clearItems();
        contextRegionId = "";
    }

    // Automatic dismissal on page navigation or target region invalidation
    Connections {
        target: contextMenuRoot.controller
        function onPageChanged() {
            contextMenuRoot.dismiss();
            contextMenuRoot.contextRegionId = "";
        }
        function onRegionsChanged() {
            if (!contextMenuRoot.contextRegionId) return;
            var regions = contextMenuRoot.controller ? contextMenuRoot.controller.activeRegions : [];
            var found = false;
            if (regions) {
                var targetId = contextMenuRoot.contextRegionId.toLowerCase();
                for (var i = 0; i < regions.length; i++) {
                    var rId = String(regions[i].region_id || "").toLowerCase();
                    if (rId === targetId || rId.replace(/-/g, "") === targetId.replace(/-/g, "")) {
                        found = true;
                        break;
                    }
                }
            }
            if (!found) {
                contextMenuRoot.dismiss();
                contextMenuRoot.contextRegionId = "";
            }
        }
        function onRegionDeleted(deletedId) {
            if (deletedId && contextMenuRoot.contextRegionId) {
                var delId = String(deletedId).toLowerCase();
                var curId = contextMenuRoot.contextRegionId.toLowerCase();
                if (delId === curId || delId.replace(/-/g, "") === curId.replace(/-/g, "")) {
                    contextMenuRoot.dismiss();
                    contextMenuRoot.contextRegionId = "";
                }
            }
        }
    }
}
