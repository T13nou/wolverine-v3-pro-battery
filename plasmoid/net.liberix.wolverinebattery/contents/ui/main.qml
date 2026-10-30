/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Razer Wolverine V3 Pro battery in the KDE system tray.
 * Runs the bundled wolverine_battery.py --json every minute and shows the level as a gauge.
 */
import QtQuick
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.plasma.plasmoid

PlasmoidItem {
    id: root

    // The script ships inside the package, next to this file.
    readonly property string script: decodeURIComponent(Qt.resolvedUrl("../code/wolverine_battery.py").toString().replace("file://", ""))
    readonly property string command: "python3 '" + script + "' --json --firmware"
    readonly property int lowThreshold: 20

    property var reading: ({})
    readonly property bool available: reading.battery !== undefined
    readonly property bool low: available && reading.battery <= lowThreshold && !reading.charging

    readonly property string percent: available ? reading.battery + " %" : "—"
    readonly property string detail: {
        if (available) {
            let lines = [reading.charging ? "Charging" : "On battery"]
            if (reading.firmware)
                lines.push("Firmware " + reading.firmware)
            return lines.join("\n")
        }
        switch (reading.error) {
        case "not_found":
            return "Controller not found or not in PC mode"
        case "permission":
            return "Permission denied: udev rule missing"
        case "no_response":
            return "Controller off or out of range"
        default:
            return "Reading…"
        }
    }

    preferredRepresentation: compactRepresentation
    toolTipMainText: "Wolverine V3 Pro" + (available ? ": " + percent : "")
    toolTipSubText: detail
    Plasmoid.icon: "input-gamepad"
    // Low battery: the system tray keeps the icon visible and flags it.
    Plasmoid.status: !available ? PlasmaCore.Types.PassiveStatus
                     : low ? PlasmaCore.Types.NeedsAttentionStatus : PlasmaCore.Types.ActiveStatus

    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            disconnectSource(source)
            try {
                root.reading = JSON.parse((data["stdout"] || "").trim())
            } catch (e) {
                root.reading = { error: "unreadable" }
            }
        }
    }

    Timer {
        interval: 60 * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: executable.connectSource(root.command)
    }

    // The gamepad is the gauge: its silhouette fills from left to right with the level,
    // green while charging and red below the threshold. The system tray sets the size;
    // the exact percentage is in the tooltip.
    readonly property string silhouette: Qt.resolvedUrl("../icons/gamepad.svg")
    readonly property color gaugeColor: reading.charging ? Kirigami.Theme.positiveTextColor
                                        : low ? Kirigami.Theme.negativeTextColor
                                        : Kirigami.Theme.textColor

    compactRepresentation: MouseArea {
        id: area
        onClicked: executable.connectSource(root.command)

        // Empty part of the gauge (or the whole gamepad, dimmed, when it does not answer).
        Kirigami.Icon {
            anchors.fill: parent
            source: root.silhouette
            isMask: true
            color: Kirigami.Theme.textColor
            opacity: 0.3
        }
        // Filled part: the silhouette spans x = 3..19 out of 22 in the SVG.
        Item {
            visible: root.available
            width: area.width * (3 + 16 * (root.reading.battery || 0) / 100) / 22
            height: area.height
            clip: true

            Kirigami.Icon {
                width: area.width
                height: area.height
                source: root.silhouette
                isMask: true
                color: root.gaugeColor
            }
        }
    }

    fullRepresentation: PlasmaComponents.Label {
        text: root.toolTipMainText + "\n" + root.detail
        padding: Kirigami.Units.largeSpacing
    }
}
