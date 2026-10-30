#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read the battery level of a Razer Wolverine V3 Pro through its HID interface.

In PC mode (1532:0a4c, Xbox 360 protocol), the dongle exposes on interface 1 a HID
interface whose descriptor declares a 90-byte Razer feature report (report ID 4,
usage page 0xff00). This script sends openrazer-style queries to it through hidraw
(HIDIOCSFEATURE then HIDIOCGFEATURE), the way HeadsetControl talks to a headset:
xpad stays bound and the controller is never disconnected.

Only the battery (0x07/0x80), charging (0x07/0x84) and firmware (0x00/0x81) reads
are allowed: any other command is refused before it reaches the device.

Every query travels over the air to the controller and counts as activity, which
keeps it from going to sleep. With --when-active, the script first listens to the
gamepad input device and only queries the controller if it was used meanwhile.
"""

import argparse
import fcntl
import json
import os
import select
import struct
import sys
import time
from pathlib import Path

VID, PIDS = "1532", {"0a4c": "PC mode"}
REPORT_ID = 0x04
REPORT_SIZE = 90
# Razer feature report in the descriptor: usage page 0xff00, report ID 4.
SIGNATURE = bytes.fromhex("0600ff8504")

# Safety net: (class, command) -> label. Nothing else is ever sent to the controller.
ALLOWED_READS = {
    (0x07, 0x80): "battery",
    (0x07, 0x84): "charging",
    (0x00, 0x81): "firmware",
}

STATUSES = {
    0x00: "new",
    0x01: "busy",
    0x02: "success",
    0x03: "failure",
    0x04: "timeout",
    0x05: "not supported",
}


def ioctl_feature(number, length):
    # _IOC(_IOC_READ | _IOC_WRITE, 'H', number, length)
    return (3 << 30) | (length << 16) | (ord("H") << 8) | number


HIDIOCSFEATURE = ioctl_feature(0x06, REPORT_SIZE + 1)
HIDIOCGFEATURE = ioctl_feature(0x07, REPORT_SIZE + 1)


def build_report(class_, command, transaction, size):
    if (class_, command) not in ALLOWED_READS:
        raise ValueError(f"command refused: class 0x{class_:02x}, id 0x{command:02x}")
    report = bytearray(REPORT_SIZE)
    report[1] = transaction
    report[5] = size
    report[6] = class_
    report[7] = command
    crc = 0
    for byte in report[2:88]:
        crc ^= byte
    report[88] = crc
    return bytes(report)


def find_hidraw():
    for entry in sorted(Path("/sys/class/hidraw").iterdir()):
        hid = (entry / "device").resolve()
        _, vid, pid = hid.name.split(".")[0].split(":")
        if vid.lower() != VID or pid.lower() not in PIDS:
            continue
        try:
            descriptor = (hid / "report_descriptor").read_bytes()
        except OSError:
            continue
        if SIGNATURE in descriptor:
            return Path("/dev") / entry.name, PIDS[pid.lower()]
    return None, None


# struct input_event: struct timeval, __u16 type, __u16 code, __s32 value.
INPUT_EVENT = struct.Struct("llHHi")
EV_KEY, EV_ABS = 0x01, 0x03


def eviocgabs(code):
    # _IOR('E', 0x40 + code, struct input_absinfo), six __s32.
    return (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + code)


def find_gamepad_inputs():
    """Event nodes of the gamepad (axes declared), readable thanks to systemd's uaccess rule."""
    nodes = []
    for entry in sorted(Path("/sys/class/input").glob("event*")):
        device = entry / "device"
        try:
            vid = (device / "id" / "vendor").read_text().strip()
            pid = (device / "id" / "product").read_text().strip()
            axes = (device / "capabilities" / "abs").read_text().strip()
        except OSError:
            continue
        if vid == VID and pid in PIDS and axes != "0":
            nodes.append(Path("/dev/input") / entry.name)
    return nodes


def wait_for_activity(nodes, seconds):
    """True if a button is pressed or an axis moves by 1/8 of its range within `seconds`.

    Always takes the full `seconds` when the controller is used.

    None if no input node can be opened. Small stick jitter is not activity.
    """
    fds = []
    for node in nodes:
        try:
            fds.append(os.open(node, os.O_RDONLY | os.O_NONBLOCK))
        except OSError:
            pass
    if not fds:
        return None
    axes = {}  # (fd, code) -> (last value, threshold)

    def moved(fd, code, value):
        if (fd, code) not in axes:
            info = bytearray(24)
            try:
                fcntl.ioctl(fd, eviocgabs(code), info)
                current, minimum, maximum = struct.unpack_from("iii", info)
            except OSError:
                current, minimum, maximum = value, 0, 0
            axes[fd, code] = (current, max(1, (maximum - minimum) // 8))
        last, threshold = axes[fd, code]
        return abs(value - last) >= threshold

    try:
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            ready, _, _ = select.select(fds, [], [], left)
            for fd in ready:
                try:
                    data = os.read(fd, INPUT_EVENT.size * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    # Node gone (dongle unplugged): stop listening to it.
                    fds.remove(fd)
                    os.close(fd)
                    if not fds:
                        return False
                    continue
                for offset in range(0, len(data) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
                    _, _, type_, code, value = INPUT_EVENT.unpack_from(data, offset)
                    if type_ == EV_KEY or (type_ == EV_ABS and moved(fd, code, value)):
                        # Keep the full window, so a controller in use is queried once per window.
                        time.sleep(max(0, end - time.monotonic()))
                        return True
        return False
    finally:
        for fd in fds:
            os.close(fd)


def hexdump(data, length=12):
    return " ".join(f"{b:02x}" for b in data[:length]) + " …"


def query(fd, class_, command, transaction, size, verbose, pause):
    report = build_report(class_, command, transaction, size)
    if verbose:
        print(f"  → {hexdump(report)}")
    fcntl.ioctl(fd, HIDIOCSFEATURE, bytes([REPORT_ID]) + report)
    time.sleep(pause)
    buffer = bytearray([REPORT_ID]) + bytearray(REPORT_SIZE)
    fcntl.ioctl(fd, HIDIOCGFEATURE, buffer)
    response = bytes(buffer[1:])
    if verbose:
        print(f"  ← {hexdump(response)}")
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the reports without opening hidraw")
    parser.add_argument("-v", "--verbose", action="store_true", help="print the raw exchanges")
    parser.add_argument("--firmware", action="store_true", help="also read the firmware version")
    parser.add_argument(
        "--transaction",
        type=lambda v: int(v, 0),
        default=0x1F,
        help="transaction ID in the report header (default 0x1f)",
    )
    parser.add_argument("--json", action="store_true", help="one-line JSON output (used by the Plasma widget)")
    parser.add_argument("--pause", type=float, default=0.05, help="delay between write and read, in seconds")
    parser.add_argument(
        "--when-active",
        type=float,
        metavar="SECONDS",
        help="listen to the gamepad for SECONDS and only query it if it was used (lets it sleep)",
    )
    args = parser.parse_args()

    # (class, command, announced data size), sizes taken from openrazer.
    reads = [(0x07, 0x80, 2), (0x07, 0x84, 2)]
    if args.firmware:
        reads.append((0x00, 0x81, 2))

    if args.dry_run:
        for class_, command, size in reads:
            report = build_report(class_, command, args.transaction, size)
            print(f"{ALLOWED_READS[(class_, command)]}: {report.hex(' ')}")
        return 0

    if args.when_active is not None:
        active = wait_for_activity(find_gamepad_inputs(), args.when_active)
        if active is False:
            if args.json:
                print(json.dumps({"idle": True}))
            else:
                print("Controller idle: not queried.")
            return 0
        if active is None:
            print("Gamepad input not readable: querying anyway.", file=sys.stderr)

    path, mode = find_hidraw()
    if path is None:
        if args.json:
            print(json.dumps({"error": "not_found"}))
        print("Razer HID interface not found: the controller must be in PC mode (1532:0a4c).", file=sys.stderr)
        return 1
    if args.verbose:
        print(f"{mode}: {path}")

    results = {}
    try:
        fd = os.open(path, os.O_RDWR)
    except PermissionError:
        if args.json:
            print(json.dumps({"error": "permission"}))
        print(f"Cannot open {path}: install the udev rule or run with sudo.", file=sys.stderr)
        return 1
    except OSError as error:
        # The device went away between detection and open (unplugged, mode switch).
        if args.json:
            print(json.dumps({"error": "not_found"}))
        print(f"Cannot open {path} ({error}).", file=sys.stderr)
        return 1
    try:
        for class_, command, size in reads:
            label = ALLOWED_READS[(class_, command)]
            if args.verbose:
                print(f"Reading {label} (0x{class_:02x}/0x{command:02x}):")
            try:
                response = query(fd, class_, command, args.transaction, size, args.verbose, args.pause)
            except OSError as error:
                print(f"{label}: hidraw error ({error}).", file=sys.stderr)
                continue
            status = response[0]
            if status != 0x02 or response[6:8] != bytes([class_, command]):
                print(
                    f"{label}: unexpected response (status {STATUSES.get(status, hex(status))}). "
                    "Try -v and another --transaction value.",
                    file=sys.stderr,
                )
                continue
            results[label] = response[8:8 + max(response[5], 1)]
    finally:
        os.close(fd)

    if args.json:
        output = {}
        if "battery" in results:
            output["battery"] = round(results["battery"][-1] * 100 / 255)
        else:
            output["error"] = "no_response"
        if "charging" in results:
            output["charging"] = bool(results["charging"][-1])
        if "firmware" in results:
            output["firmware"] = f"{results['firmware'][0]}.{results['firmware'][1]:02d}"
        print(json.dumps(output))
        return 0 if "battery" in results else 1

    if "battery" in results:
        raw = results["battery"][-1]
        print(f"Battery: {round(raw * 100 / 255)} % (raw value {raw})")
    if "charging" in results:
        raw = results["charging"][-1]
        print(f"Charging: {'yes' if raw else 'no'} (raw value {raw})")
    if "firmware" in results:
        version = results["firmware"]
        print(f"Firmware: {version[0]}.{version[1]:02d} (raw bytes {version.hex(' ')})")
    return 0 if "battery" in results else 1


if __name__ == "__main__":
    sys.exit(main())
