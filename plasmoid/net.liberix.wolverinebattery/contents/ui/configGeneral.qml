// SPDX-License-Identifier: GPL-2.0-or-later
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami

KCM.SimpleKCM {
    id: page

    property alias cfg_setSleep: setSleep.checked
    property int cfg_sleepMinutes
    // The choices offered by Razer Synapse; the script refuses any other value.
    readonly property var choices: [5, 15, 30, 45, 60]

    Kirigami.FormLayout {
        QQC2.CheckBox {
            id: setSleep
            Kirigami.FormData.label: "Sleep timer:"
            text: "Set the controller's sleep timer"
        }
        QQC2.ComboBox {
            Kirigami.FormData.label: "Sleep after:"
            enabled: setSleep.checked
            model: page.choices.map(minutes => minutes + " min")
            currentIndex: Math.max(0, page.choices.indexOf(page.cfg_sleepMinutes))
            onActivated: index => page.cfg_sleepMinutes = page.choices[index]
        }
        QQC2.Label {
            Layout.fillWidth: true
            Layout.maximumWidth: Kirigami.Units.gridUnit * 25
            wrapMode: Text.WordWrap
            font: Kirigami.Theme.smallFont
            text: "Same choices as Razer Synapse. The setting is applied the next time the controller "
                  + "is in use, and again if the controller reports another value. Leave unchecked to "
                  + "keep the controller's own setting."
        }
    }
}
