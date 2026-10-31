/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Razer Wolverine V3 Pro battery in the KDE system tray.
 * Runs the bundled wolverine_battery.py and shows the level as a gauge. The script is run in
 * a loop with --when-active, so the controller is only queried when it was used in the last
 * minute and can go to sleep when left alone. Optionally sets the controller's sleep timer
 * (configuration page), and sets it again if the controller reports another value.
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
    readonly property string command: "python3 '" + script + "' --json --sleep"
    readonly property string watchCommand: command + " --when-active 60"
    readonly property string firmwareCommand: command + " --firmware"
    readonly property int lowThreshold: 20

    property var reading: ({})
    property string firmware: ""
    property date readAt
    property int sleepSeconds: 0
    // Sleep timer asked for in the configuration, in minutes; 0 leaves the controller's own.
    readonly property int wantedSleep: Plasmoid.configuration.setSleep ? Plasmoid.configuration.sleepMinutes : 0
    readonly property bool available: reading.battery !== undefined
    readonly property bool low: available && reading.battery <= lowThreshold && !reading.charging

    readonly property string percent: available ? reading.battery + " %" : "—"
    readonly property string detail: {
        if (available) {
            let lines = [reading.charging ? "Charging" : "On battery",
                         "Read at " + Qt.formatTime(readAt, Qt.locale().timeFormat(Locale.ShortFormat))]
            if (sleepSeconds)
                lines.push("Sleeps after " + Math.round(sleepSeconds / 60) + " min idle")
            if (firmware)
                lines.push("Firmware " + firmware)
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
            let result
            try {
                result = JSON.parse((data["stdout"] || "").trim())
            } catch (e) {
                result = { error: "unreadable" }
            }
            let settingSleep = source.indexOf("--set-sleep") >= 0
            // Idle controller: it was not queried, keep the last reading. A failed sleep timer
            // write (controller asleep) does not replace it either.
            if (!result.idle && !(settingSleep && result.battery === undefined)) {
                root.reading = result
                root.readAt = new Date()
                if (result.firmware)
                    root.firmware = result.firmware
                if (result.sleep !== undefined)
                    root.sleepSeconds = result.sleep
                // Reapply the configured timer if the controller lost it; a failed write is
                // retried at the next reading, not right away.
                if (!settingSleep && result.sleep !== undefined)
                    root.applySleep()
            }
            // No reading yet because of an error raised before anything reached the controller
            // (permission not granted yet at login, dongle absent): retry now rather than wait
            // for the controller to be used. "Controller off" still waits for activity.
            if (result.idle && !root.available && root.reading.error !== "no_response")
                executable.connectSource(root.firmwareCommand)
            if (source === root.watchCommand)
                restart.start()
        }
    }

    function applySleep() {
        if (wantedSleep && wantedSleep * 60 !== sleepSeconds)
            executable.connectSource(command + " --set-sleep " + wantedSleep)
    }

    Connections {
        target: Plasmoid.configuration
        function onSetSleepChanged() { root.applySleep() }
        function onSleepMinutesChanged() { root.applySleep() }
    }

    // Next listening window. The short delay avoids a tight loop if the script fails at once.
    Timer {
        id: restart
        interval: 2000
        onTriggered: executable.connectSource(root.watchCommand)
    }

    Component.onCompleted: {
        // One immediate reading (with the firmware), then listen in a loop.
        executable.connectSource(root.firmwareCommand)
        executable.connectSource(root.watchCommand)
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
