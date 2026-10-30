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
"""

import argparse
import fcntl
import json
import os
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
