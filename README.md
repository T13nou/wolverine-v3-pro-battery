# Wolverine V3 Pro battery for Linux

Read the battery level of a **Razer Wolverine V3 Pro** controller on Linux, and show it in the
KDE Plasma 6 system tray.

Razer ships no Linux software for this controller, and the kernel does not report its battery.
This small tool asks the controller directly, the way
[HeadsetControl](https://github.com/Sapd/HeadsetControl) does for headsets: no kernel module,
no driver to unbind, no disconnection while you play.

- `wolverine_battery.py`: command-line tool (Python 3 standard library only).
- A udev rule, so it runs without root.
- A Plasma 6 widget: a gamepad icon in the system tray that fills up with the battery level,
  turns green while charging and red below 20 %. The exact percentage is in the tooltip.
- Set the controller's sleep timer (5, 15, 30, 45 or 60 minutes, as in Razer Synapse), from the
  command line or the widget.

## Requirements

- The controller in **PC mode** (USB ID `1532:0a4c`). Check with `lsusb | grep 1532`.
  Xbox mode (`1532:0a3f`) is not supported: it has no HID interface (see below).
- Python 3.8 or later (standard library only).
- For the udev rule without root: a distribution with systemd-logind or elogind (`uaccess` tag).
- For the widget: KDE Plasma 6.

Nothing here is tied to one distribution: it only uses the kernel's `hidraw` driver (enabled in
every mainstream kernel), udev and Python. It should work on any recent Linux distribution
(Arch, Fedora, openSUSE, Debian, Ubuntu…); the widget needs a distribution that ships Plasma 6.

Tested setup:

| Component  | Version                                                       |
|------------|---------------------------------------------------------------|
| Distro     | CachyOS (Arch-based)                                          |
| Kernel     | 7.3                                                           |
| Desktop    | KDE Plasma 6                                                  |
| Python     | 3.14                                                          |
| Controller | firmware 2.04.00 (updated with Razer's Windows app), PC mode, 2.4 GHz dongle |

A USB cable in PC mode should work the same way but has not been tested yet. Reports from other
distributions and setups are welcome: please open an issue with your distro, kernel and the output
of `./wolverine_battery.py -v --firmware`.

## Installation

```sh
git clone https://github.com/T13nou/wolverine-v3-pro-battery.git
cd wolverine-v3-pro-battery
```

### udev rule (read without root)

```sh
sudo install -m 644 udev/70-wolverine-v3-pro-battery.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=hidraw
```

The rule grants the logged-in user access to the controller's hidraw node only (`uaccess`).
To remove it: delete the file and run the last two commands again.

### Command line

```console
$ ./wolverine_battery.py --firmware
Battery: 96 % (raw value 244)
Charging: no (raw value 0)
Firmware: 2.04 (raw bytes 02 04)
$ ./wolverine_battery.py --json
{"battery": 96, "charging": false}
```

`-v` prints the raw reports, `--dry-run` prints what would be sent without touching the device.

Each query travels over the air to the controller, and the controller counts it as activity: polling
it every minute keeps it from going to sleep. `--when-active SECONDS` first listens to the gamepad
input for that long, and only queries the controller if a button was pressed or a stick moved:

```console
$ ./wolverine_battery.py --json --when-active 60
{"idle": true}
```

Listening needs read access to the gamepad's input device, which systemd's own `uaccess` rule
already grants to the logged-in user (games rely on it). If it is not readable, the script
queries anyway.

### Plasma widget

Either download `wolverine-v3-pro-battery-<version>.plasmoid` from the
[releases](https://github.com/T13nou/wolverine-v3-pro-battery/releases) and install it with
*Add Widgets… → Get New Widgets → Install Widget From Local File…*, or from the cloned repository:

```sh
kpackagetool6 -t Plasma/Applet -i plasmoid/net.liberix.wolverinebattery   # install
kpackagetool6 -t Plasma/Applet -u plasmoid/net.liberix.wolverinebattery   # update
kpackagetool6 -t Plasma/Applet -r net.liberix.wolverinebattery            # remove
systemctl --user restart plasma-plasmashell
```

The system tray only discovers new widgets when Plasma restarts. The widget then shows up in
the Hardware section, and hides itself in the tray popup while the controller is off or absent.
To force it visible or hidden: right-click the tray arrow → *Configure System Tray…* → *Entries*.
You can also add it directly to a panel. It reads the battery once at startup, then once a minute
while the controller is in use (`--when-active`), so an idle controller still goes to sleep. The
tooltip shows the time of the last reading; click the icon to read it now.

### Sleep timer

The controller goes to sleep after 15 minutes idle by default. To change it, pick one of the
values Razer Synapse offers, in minutes:

```sh
./wolverine_battery.py --set-sleep 30    # 5, 15, 30, 45 or 60
./wolverine_battery.py --sleep           # read the current value
```

The script reads the value back and fails if the controller stored something else. It refuses
any other value: the firmware raises anything below 5 minutes to 5 minutes, and values outside
the Synapse list are not needed.

In the widget: right-click → *Configure…* → *Set the controller's sleep timer*. The widget writes
it the next time the controller is in use, and again whenever the controller reports another
value. Unchecking the option leaves the controller at its last value: pick 15 minutes first to
go back to the default.

The widget ships its own copy of the script, so the cloned repository is not needed once it is
installed. It still needs the udev rule.

## How it works

In PC mode, the dongle exposes a HID interface (interface 1) whose descriptor declares a
90-byte vendor feature report (usage page `0xff00`, report ID 4). It speaks the same report
format as other Razer devices supported by [openrazer](https://github.com/openrazer/openrazer):

| Byte  | Meaning                                   |
|-------|-------------------------------------------|
| 0     | status (`0x02` = success)                 |
| 1     | transaction ID (`0x1f`)                   |
| 5     | data size                                 |
| 6, 7  | command class and ID                      |
| 8..87 | data                                      |
| 88    | XOR of bytes 2..87                        |

The tool sends the query with `HIDIOCSFEATURE` and reads the answer with `HIDIOCGFEATURE` on
`/dev/hidrawN`. Commands used:

| Class/ID    | Reply                                          |
|-------------|------------------------------------------------|
| `0x07/0x80` | battery level, last data byte, 0..255          |
| `0x07/0x84` | charging, last data byte, 1 or 0               |
| `0x00/0x81` | firmware version, major and minor (`02 04`)    |
| `0x07/0x83` | sleep timer, seconds, little-endian (`84 03` = 900) |
| `0x07/0x03` | **write** the sleep timer, same encoding       |

**Only these four reads and this one write can be sent**, and the write only with one of the
Synapse values: the script refuses anything else before it reaches the device. Other vendor
commands change the controller's state (one of them is a reset/power-off path in the firmware),
so please keep this safety net if you experiment.

The sleep timer commands are the ones openrazer uses for wireless mice, with the bytes in the
opposite order.

In Xbox mode the controller speaks the GIP protocol instead. The same commands travel inside GIP
vendor messages (`0x0f`/`0x10`) on the interrupt endpoints, which the kernel's xpad driver owns.
Reading them from user space means unbinding xpad for a moment, which disconnects the
controller, so this tool does not do it. A kernel-side solution is the proper fix there.

## Credits

- [Netruk44/wolverine-v3-pro-linux](https://github.com/Netruk44/wolverine-v3-pro-linux)
  documented the vendor frames over GIP, which led to the battery commands.
- The Razer Insider thread "Wolverine V3 Pro always reports 10% battery to Windows APIs"
  pointed at the real battery query.
- [openrazer](https://github.com/openrazer/openrazer) for the report format and command IDs.
- The tray icon is derived from the Breeze `input-gamepad-symbolic` icon (LGPL-3.0-or-later).

This tool was written with the help of an AI assistant (Claude). It was tested on my own
controller, under the conditions described above.

## License

GPL-2.0-or-later, see [LICENSE](LICENSE). The icon `gamepad.svg` is LGPL-3.0-or-later.

Not affiliated with Razer. Use at your own risk.
