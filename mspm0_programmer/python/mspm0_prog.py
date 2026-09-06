# MSPM0 BSL Programmer
# rev 2 - Rich UI integration - September 2026
# This program can be used to program MSPM0L110x devices via USB Serial Bootloader (BSL).
# Requires:
# pySerial:  pip install pyserial
# Rich UI:   pip install rich
# Usage:
# python ./mspm0_prog.py [--port COMx] [--auto] firmware.hex
# python ./mspm0_prog.py --port none --saveflashfile firmware.hex
# python ./mspm0_prog.py [--port COMx] [--auto] firmware.flash  (todo - not currently implemented!)
# python ./mspm0_prog.py [--port COMx] sim
# By specifying firmware.hex, the code will convert to a binary flash file, and then program it
# By specifying firmware.flash, the code will program the flash file directly
# By specifying sim, the code will simulate the MSPM0 BSL and respond to commands
# the --auto option will not prompt the user, and will automatically try to boot/reset the device
# the --saveflashfile option will save the interim flash file with a .flash suffix

import argparse
try:
    import serial
except ImportError:
    serial = None
import binascii
import time
import sys
import os
import builtins
import threading
from collections import deque
from pathlib import Path

try:
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich.prompt import Prompt
except ImportError:
    Console = None



_active_ui = None
_active_live = None
_ui_thread = None
_ui_stop = None
_ui_partial = ""


def _log_style(message):
    low = message.lower()
    if "error" in low or "failed" in low or "giving up" in low:
        return "bold red"
    if "warning" in low:
        return "bold yellow"
    if "success" in low or "complete" in low or "started" in low or "passed" in low:
        return "green"
    return None


def program_print(*values, sep=" ", end="\n", flush=False, **kwargs):
    """print()-compatible output that goes to the Rich activity pane when active."""
    global _ui_partial
    if _active_ui is None:
        builtins.print(*values, sep=sep, end=end, flush=flush, **kwargs)
        return

    text = sep.join(str(v) for v in values) + end
    _ui_partial += text
    while "\n" in _ui_partial:
        line, _ui_partial = _ui_partial.split("\n", 1)
        if line:
            # Collapse the old waiting-dot output; the lower-pane spinner now shows waiting.
            if line.startswith("Still waiting for response from MSPM0 chip"):
                _active_ui.log("Still waiting for response from MSPM0 chip...", "grey50")
            else:
                display_line = _compact_ui_line(line)
                _active_ui.log(display_line, _log_style(line))


def _compact_ui_line(line):
    """Make very large debug records readable in the one-line Rich activity log."""
    if line.startswith("Sending Program Data command: "):
        hexdata = line.split(": ", 1)[1]
        if len(hexdata) > 80:
            byte_count = len(hexdata) // 2
            return (f"Sending Program Data command: {hexdata[:48]}..."
                    f"{hexdata[-16:]}  [{byte_count} bytes]")
    return line


def ui_status(action, target, message, progress=None, prompt=""):
    if _active_ui is not None:
        _active_ui.set_status(action, str(target), message, progress=progress, prompt=prompt)


def _ui_animation_worker():
    while _ui_stop is not None and not _ui_stop.wait(0.06):
        if _active_ui is not None and _active_live is not None:
            _active_ui.tick()
            try:
                _active_live.update(_active_ui.render(), refresh=True)
            except Exception:
                # Never let cosmetic terminal rendering interfere with programming.
                pass


def start_programmer_ui(target):
    """Start the alternate-screen Rich UI. Returns True when enabled."""
    global _active_ui, _active_live, _ui_thread, _ui_stop
    if Console is None or not sys.stdout.isatty():
        return False
    _active_ui = RichProgrammerUI()
    _active_ui.set_status("Prepare", target, "Preparing programmer")
    _active_live = Live(_active_ui.render(), console=_active_ui.console,
                        refresh_per_second=20, screen=True)
    _active_live.start(refresh=True)
    _ui_stop = threading.Event()
    _ui_thread = threading.Thread(target=_ui_animation_worker, daemon=True)
    _ui_thread.start()
    return True


def stop_programmer_ui(final_action=None, target=None, message=None,
                       success=True, wait=True):
    """Leave the final full-screen result visible, optionally waiting for one key."""
    global _active_ui, _active_live, _ui_thread, _ui_stop, _ui_partial
    if _active_ui is None:
        return
    if _ui_partial:
        _active_ui.log(_ui_partial, _log_style(_ui_partial))
        _ui_partial = ""
    if final_action and message:
        prompt = "↑/↓ scroll • any other key exits" if wait and sys.stdin.isatty() else ""
        _active_ui.set_status(final_action, target or _active_ui.target, message,
                              progress=_active_ui.progress, prompt=prompt)
        if success:
            _active_ui.log(message, "bold green")
    if _active_live is not None:
        _active_live.update(_active_ui.render(), refresh=True)
    if wait and sys.stdin.isatty():
        review_final_screen()
    if _ui_stop is not None:
        _ui_stop.set()
    if _ui_thread is not None:
        _ui_thread.join(timeout=0.3)
    if _active_live is not None:
        _active_live.stop()
    _active_ui = None
    _active_live = None
    _ui_thread = None
    _ui_stop = None


def ui_wait_for_key(action, target, message, prompt="Press any key to continue"):
    """Display a prompt in the fixed lower pane and wait for a single key."""
    if _active_ui is None:
        builtins.print(message)
        input()
        return
    _active_ui.set_status(action, str(target), message, progress=None, prompt=prompt)
    if _active_live is not None:
        _active_live.update(_active_ui.render(), refresh=True)
    wait_for_any_key()
    _active_ui.set_status(action, str(target), message, progress=None, prompt="")

def _read_review_key():
    """Read one key and recognise a few navigation keys used on the final screen."""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {
                "I": "page_up",
                "Q": "page_down",
                "H": "up",
                "P": "down",
                "G": "home",
                "O": "end",
            }.get(code, "exit")
        return "exit"

    # POSIX: caller has already put the terminal in cbreak mode.
    import select
    ch = sys.stdin.read(1)
    if ch != "\x1b":
        return "exit"

    seq = ch
    # Escape sequences arrive as a short burst. Gather what is immediately ready.
    for _ in range(5):
        ready, _, _ = select.select([sys.stdin], [], [], 0.03)
        if not ready:
            break
        seq += sys.stdin.read(1)

    return {
        "\x1b[5~": "page_up",
        "\x1b[6~": "page_down",
        "\x1b[A": "up",
        "\x1b[B": "down",
        "\x1b[H": "home",
        "\x1b[F": "end",
        "\x1b[1~": "home",
        "\x1b[4~": "end",
    }.get(seq, "exit")


def review_final_screen():
    """Allow log review after completion; any non-navigation key exits."""
    if _active_ui is None or not sys.stdin.isatty():
        return

    def handle_key(key):
        if key == "page_up":
            _active_ui.scroll_up()
        elif key == "page_down":
            _active_ui.scroll_down()
        elif key == "up":
            _active_ui.scroll_up()
        elif key == "down":
            _active_ui.scroll_down()
        elif key == "home":
            _active_ui.scroll_home()
        elif key == "end":
            _active_ui.scroll_end()
        else:
            return False

        if _active_live is not None:
            _active_live.update(_active_ui.render(), refresh=True)
        return True

    if os.name == "nt":
        while handle_key(_read_review_key()):
            pass
        return

    import termios
    import tty
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while handle_key(_read_review_key()):
            pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def wait_for_any_key():
    """Wait for one keypress without requiring Enter, on Windows or a POSIX terminal."""
    if not sys.stdin.isatty():
        return

    if os.name == "nt":
        import msvcrt
        msvcrt.getwch()
        return

    # Linux/macOS/other POSIX terminals.
    import termios
    import tty
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


class RichProgrammerUI:
    """Small Rich UI prototype for MSPM0 programmer status/log output."""

    # 8-dot Braille cell, with one perimeter dot removed per frame.
    _BRAILLE_FULL = 0x28FF
    _DOT_BITS = (0x01, 0x02, 0x04, 0x40, 0x80, 0x20, 0x10, 0x08)

    def __init__(self):
        if Console is None:
            raise RuntimeError("Rich is not installed; run: pip install rich")
        self.console = Console()
        self.logs = deque(maxlen=10000)
        self.action = "Ready"
        self.target = "mspm0_prog"
        self.message = "Waiting"
        self.progress = None
        self.prompt = ""
        self.frame = 0
        self.scroll_offset = 0

        self.layout = Layout()
        self.layout.split_column(
            Layout(name="log", ratio=3),
            Layout(name="status", size=9),
        )

    def log(self, message, style=None):
        self.logs.append((message, style))
        self.scroll_offset = 0

    def visible_log_rows(self):
        # Panel border/title consume a couple of rows; status pane is fixed at 9.
        return max(3, self.console.size.height - 12)

    def scroll_up(self, amount=None):
        rows = self.visible_log_rows()
        step = amount if amount is not None else max(1, rows - 2)
        max_offset = max(0, len(self.logs) - rows)
        self.scroll_offset = min(max_offset, self.scroll_offset + step)

    def scroll_down(self, amount=None):
        rows = self.visible_log_rows()
        step = amount if amount is not None else max(1, rows - 2)
        self.scroll_offset = max(0, self.scroll_offset - step)

    def scroll_home(self):
        rows = self.visible_log_rows()
        self.scroll_offset = max(0, len(self.logs) - rows)

    def scroll_end(self):
        self.scroll_offset = 0

    def set_status(self, action, target, message, progress=None, prompt=""):
        self.action = action
        self.target = target
        self.message = message
        self.progress = progress
        self.prompt = prompt

    def spinner(self):
        bit = self._DOT_BITS[self.frame % len(self._DOT_BITS)]
        return chr(self._BRAILLE_FULL & ~bit)

    def pulse_text(self, message):
        text = Text()
        if not message:
            return text
        span = max(1, len(message) - 1)
        travel = span * 2
        centre = self.frame % max(1, travel)
        if centre > span:
            centre = travel - centre
        for i, char in enumerate(message):
            distance = abs(i - centre)
            if distance == 0:
                style = "bold bright_cyan"
            elif distance == 1:
                style = "cyan"
            elif distance <= 3:
                style = "grey70"
            else:
                style = "grey42"
            text.append(char, style=style)
        return text

    def _progress_bar(self, width=36):
        if self.progress is None:
            return Text("")
        current, total = self.progress
        total = max(1, total)
        fraction = max(0.0, min(1.0, current / total))
        filled = round(width * fraction)
        t = Text()
        t.append("  ")
        t.append("━" * filled, style="cyan")
        t.append("━" * (width - filled), style="grey23")
        t.append(f"  {current}/{total}", style="grey70")
        return t

    def render(self):
        log_text = Text()
        # Keep each activity record to one display row. This is important for large
        # Program Data packets: one logical line must not wrap over the whole pane.
        rows = self.visible_log_rows()
        all_logs = list(self.logs)
        end = max(0, len(all_logs) - self.scroll_offset)
        start = max(0, end - rows)
        width = max(20, self.console.size.width - 6)

        for message, style in all_logs[start:end]:
            display = message
            if len(display) > width:
                display = display[:max(1, width - 1)] + "…"
            log_text.append(display, style=style or "grey70")
            log_text.append("\n")

        if not self.logs:
            log_text.append("No activity yet.", style="grey42")

        headline = Text()
        headline.append("● ", style="cyan")
        headline.append(self.action, style="bold bright_cyan")
        headline.append("  [", style="grey42")
        headline.append(self.target, style="bright_blue")
        headline.append("]", style="grey42")

        busy = Text()
        if self.action == "Done":
            busy.append("  ✓  ", style="bold green")
            busy.append(self.message, style="bold green")
        elif self.action == "Failed":
            busy.append("  ✗  ", style="bold red")
            busy.append(self.message, style="bold red")
        else:
            busy.append("  " + self.spinner() + "  ", style="bright_cyan")
            busy.append_text(self.pulse_text(self.message))

        prompt = Text()
        if self.prompt:
            prompt.append("  > ", style="bold cyan")
            prompt.append(self.prompt, style="grey62")

        status_group = Group(headline, busy, self._progress_bar(), prompt)
        if self.scroll_offset:
            title = f"MSPM0 activity  [scroll: -{self.scroll_offset}]"
        else:
            title = "MSPM0 activity"
        self.layout["log"].update(Panel(log_text, title=title, border_style="grey35"))
        self.layout["status"].update(Panel(status_group, border_style="cyan"))
        return self.layout

    def tick(self):
        self.frame += 1


def run_rich_ui_demo():
    """Show the UI without requiring a board or serial port."""
    if Console is None:
        program_print("Rich is not installed. Install it with: pip install rich")
        return

    ui = RichProgrammerUI()
    ui.log("MSPM0 BSL Programmer — Rich UI demonstration", "bold")
    ui.log("No hardware is accessed in --ui-demo mode.", "grey50")

    stages = [
        ("Read", "firmware.hex", "Parsing Intel HEX file", 1.35, [
            "processing line 1: :020000040000FA",
            "Found contiguous range at 0x00000000",
            "Sanity checking the .hex content...",
        ]),
        ("Open", "COM6", "Connecting to MSPM0 bootloader", 1.25, [
            "Opened serial port COM6 at 9600 baud.",
            "Sending Connection Command (0x12)",
            "Received Device Info: command interpreter 0x0100",
        ]),
        ("Erase", "0x00000000", "Erasing flash blocks", 1.5, [
            "Unlocking Bootloader (0x21)",
            "Bootloader unlocked successfully",
            "Erasing Flash block: Address 0x00000000, length 1024 bytes",
        ]),
        ("Program", "firmware.hex", "Programming flash", 2.3, [
            "Programming Data Entry 0: Address 0x00000000, Length 1024 bytes",
            "Programming Data Entry 1: Address 0x00000400, Length 1024 bytes",
            "Programming Data Entry 2: Address 0x00000800, Length 736 bytes",
        ]),
        ("Start", "MSPM0L1105", "Starting application", 1.0, [
            "Sending Start Application Command (0x40)",
            "Application started on MSPM0 successfully",
        ]),
    ]

    with Live(ui.render(), console=ui.console, refresh_per_second=20, screen=True) as live:
        for action, target, message, duration, lines in stages:
            total = len(lines)
            ui.set_status(action, target, message, progress=(0, total))
            line_index = 0
            frames = max(1, int(duration * 20))
            for n in range(frames):
                desired = min(total, int((n + 1) / frames * (total + 1)))
                while line_index < desired and line_index < total:
                    style = "green" if "success" in lines[line_index].lower() else None
                    ui.log(lines[line_index], style)
                    line_index += 1
                ui.progress = (line_index, total)
                ui.tick()
                live.update(ui.render())
                time.sleep(0.05)

        ui.log("Programming complete.", "bold green")
        ui.set_status("Done", "firmware.hex", "Programming complete", progress=(3, 3),
                      prompt="Press any key to exit")
        ui.tick()
        live.update(ui.render())
        wait_for_any_key()


port = 'COM4'  # Adjust this to your serial port
baudrate = 9600  # Standard baudrate for MSPM0 BSL
ser = None  # Serial port object, initialized later

# bytearray to hold the entire data packet to send over serial port.
# format will be:
# byte 1: Header
# byte 2, 3: Length (2 bytes)
# byte 4: Command
# byte 5..n: Optional Data
# last 4 bytes: CRC32-ISO3309 polynomial bit reversed, inital seed 0xFFFFFFFF
data_packet = bytearray()
# bytearray to hold entire flash data that was sent; 
# this is used for a checksum for standalone verification
data_for_verification_calc = bytearray()

# data pulled from .hex file, for building an 'interim' file that is easier to use
addr_len_list= []  # List of tuples (address, length) for each address range
data_list = []  # List of bytearrays to hold the data for each address range
interim_file_data = bytearray()  # This will hold the interim file data

# serial port capabilities; set to False if you don't want to use RTS/DTR
rts_capability = True
dtr_capability = True

# calc_crc - calculates CRC32 bytes for the entire given payload.
# example: to calculate CRC for data_packet[3:] (after header and 2-byte length):
# calc_crc(data_packet[3:])
# Note: when simulating the microcontroller end, ensure 4 bytes are skipped, since here is a 0x00 prepended.
def calc_crc(payload):
    """Calculate CRC32 (IEEE/ISO, reflected), covering bytes after header+length, and append LE."""
    crc = (binascii.crc32(payload, 0) ^ 0xFFFFFFFF) & 0xFFFFFFFF
    return crc.to_bytes(4, 'little')

# Build packet with the given header, command and optional data
# Example: Header = 0x80, Command = 0x12, Data = [], CRC32=4 bytes
# Result will be [0x80, 0x01, 0x00, 0x12, 0x3a, 0x61, 0x44, 0xde]
def build_packet(header, command, data):
    """Create a data packet with the given header, command and optional data and CRC32."""
    global data_packet
    length = len(data) + 1  # +1 for the command byte
    data_packet.clear()  # Clear previous data packet
    data_packet.append(header)
    data_packet.extend(length.to_bytes(2, 'little'))  # Length is 2 bytes
    data_packet.append(command)
    data_packet.extend(data)  # Append optional data
    data_packet.extend(calc_crc(data_packet[3:]))  # Calculate and append CRC
    # program_print(f"Data packet built: {data_packet.hex()}")

def sanity_check():
    # data is a bytearray of 32 0xff bytes
    data = bytearray([0xff] * 32)
    build_packet(0x80, 0x21, data)
    expected = bytearray([0x80, 0x21, 0x00, 0x21]) + data + bytearray([0x02, 0xaa, 0xf0, 0x3d])
    if data_packet != expected:
        program_print(f"***** ERROR - Sanity check failed: {data_packet.hex()} != {expected.hex()}")
        exit(1)
    else:
        program_print("Sanity check passed.")

def print_banner():
    program_print("\n\n\n\n\n\n");
    program_print("                      _     __ __  ___  _____ ");
    program_print(r"                     | |   /_ /_ |/ _ \| ____|");
    program_print("  ___  __ _ ___ _   _| |    | || | | | | |__  ");
    program_print(r" / _ \/ _` / __| | | | |    | || | | | |___ \ ");
    program_print(r"|  __/ (_| \__ \ |_| | |____| || | |_| |___) |");
    program_print(r" \___|\__,_|___/\__, |______|_||_|\___/|____/ ");
    program_print("                 __/ |                        ");
    program_print("                |___/                         ");
    program_print("MSPM0 BSL Programmer - rev 1 - shabaz - August 2025")
    program_print(" ");


def hexparse(hex_file):
    """Read an Intel HEX file to memory and parse it into address and data lists."""
    global addr_len_list
    global data_list

    addr_len_list = []
    data_list = []

    cur_data_bytes = bytearray()
    max_data_len = 1024
    tot_data_len = 0
    upper_addr_word = 0x0000

    current_range_start = None  # track the start address for the current contiguous range

    def flush_current():
        """Flush cur_data_bytes into data_list/addr_len_list using 1024-byte chunks + final partial."""
        nonlocal cur_data_bytes, current_range_start, tot_data_len
        if current_range_start is None or len(cur_data_bytes) == 0:
            return
        start = current_range_start
        buf = cur_data_bytes
        # full 1024-byte chunks
        while len(buf) >= max_data_len:
            data_list.append(bytearray(buf[:max_data_len]))
            addr_len_list.append((start, max_data_len))
            tot_data_len += max_data_len
            buf = buf[max_data_len:]
            start += max_data_len
        # final partial (if any)
        if len(buf) > 0:
            data_list.append(bytearray(buf))
            addr_len_list.append((start, len(buf)))
            tot_data_len += len(buf)
        # reset
        cur_data_bytes = bytearray()
        current_range_start = None

    with open(hex_file, 'r') as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line or not line.startswith(':'):
                program_print(f"line # {line_num}: Skipping content: '{line}'")
                continue
            program_print(f"processing line {line_num}: {line}")
            try:
                byte_count  = int(line[1:3], 16)
                addr16      = int(line[3:7], 16)
                record_type = int(line[7:9], 16)
                data        = bytes.fromhex(line[9:9 + 2*byte_count])
            except ValueError as e:
                program_print(f"Error parsing line '{line}': {e}")
                continue  # keep going
            if record_type == 4:
                # Extended Linear Address
                upper_addr_word = int.from_bytes(data, 'big') << 16
            elif record_type == 0:
                # Data record
                addr32 = (upper_addr_word | addr16) & 0xFFFFFFFF
                # Start new range or check contiguity
                if current_range_start is None:
                    current_range_start = addr32
                elif addr32 != (current_range_start + len(cur_data_bytes)):
                    # non-contiguous -> flush current range first
                    flush_current()
                    current_range_start = addr32
                # append incoming bytes
                cur_data_bytes.extend(data)
                # Split out any full 1024-byte chunks immediately
                while len(cur_data_bytes) >= max_data_len:
                    data_list.append(bytearray(cur_data_bytes[:max_data_len]))
                    addr_len_list.append((current_range_start, max_data_len))
                    tot_data_len += max_data_len
                    cur_data_bytes = cur_data_bytes[max_data_len:]
                    current_range_start += max_data_len
            elif record_type == 1:
                program_print(f"End of file record (0x01) on line {line_num}")
                program_print(f"Successfully read .hex file.")
                break
            else:
                # other record types
                continue
    # Final flush
    flush_current()

    # Sanity checks
    program_print(f"Sanity checking the .hex content...")
    if len(addr_len_list) != len(data_list):
        program_print(f"**** ERROR: Length of addr_len_list ({len(addr_len_list)}) does not match length of data_list ({len(data_list)}), aborting! ****")
        return False

    for addr, length in addr_len_list:
        if length == 0:
            program_print(f"**** ERROR: Address {addr:#010x} has length 0, aborting! ****")
            return False
        if addr % 8 != 0:
            program_print(f"**** ERROR: Address {addr:#010x} is not 8-byte aligned, aborting! ****")
            return False

    # pad to 8B if needed
    for i, (addr, length) in enumerate(addr_len_list):
        if length % 8 != 0:
            padding_length = 8 - (length % 8)
            addr_pad_start = addr + length
            addr_pad_stop  = addr_pad_start + padding_length - 1
            program_print(f"Data length for Entry {i} is not divisible by 8, padding..")
            program_print(f"Padding Entry {i} with {padding_length} x '0xff' byte(s) at {addr_pad_start:#010x}-{addr_pad_stop:#010x}")
            data_list[i] += bytearray([0xff] * padding_length)
            addr_len_list[i] = (addr, length + padding_length)

    for i, (addr, length) in enumerate(addr_len_list):
        program_print(f"  Addr/Len Entry {i}: Address: {addr:#010x}, Length: {length} bytes")
    for i, data in enumerate(data_list):
        program_print(f"  Data Entry {i}: Length: {len(data)} bytes, Content: {data.hex()}")
    program_print(f"Sanity check of .hex content completed successfully.")

def build_interim_array():
    """Build an interim file array from the address and data lists that came from the parsed hex file."""
    global interim_file_data
    interim_file_data.clear()  # Clear previous data
    # interim array format:
    # 256 bytes: set to 0x00 for now
    # 4 bytes: 'ADDR' (ASCII)
    # 2 bytes: number of addr_len entries (little-endian)
    # addr_len section: 6 bytes per entry, 4 bytes address, 2 bytes length (little-endian)
    # 4 bytes: 'DATA' (ASCII)
    # 2 bytes: length of first data entry in bytes (little-endian)
    # n bytes: data for the first address range
    # 2 bytes: length of second data entry in bytes (little-endian)
    # n bytes: data for the second address range
    # ... and so on for all address ranges

    interim_file_data.extend(bytearray(256))  # Fill the first 256 bytes with 0x00
    interim_file_data.extend(b'ADDR')  # Append 'ADDR' ASCII
    interim_file_data.extend(len(addr_len_list).to_bytes(2, 'little'))  # Number of addr_len entries in little-endian format
    # Append the addr_len_list entries
    for addr, length in addr_len_list:
        interim_file_data.extend(addr.to_bytes(4, 'little'))
        interim_file_data.extend(length.to_bytes(2, 'little'))
    interim_file_data.extend(b'DATA')  # Append 'DATA' ASCII
    # Append the data entries
    for i, data in enumerate(data_list):
        interim_file_data.extend(len(data).to_bytes(2, 'little'))  # Length of data entry in bytes
        interim_file_data.extend(data)  # Append the data bytes
    # Print the interim file data for debugging
    # print in format: idx : data (hex) : data (ascii) 16 bytes per line
    program_print("Interim file data:")
    for i in range(0, len(interim_file_data), 16):
        line = interim_file_data[i:i+16]
        hex_data = ' '.join(f'{b:02x}' for b in line)
        ascii_data = ''.join(chr(b) if 32 <= b < 127 else '.' for b in line)
        program_print(f"{i:04x} : {hex_data:<48} : {ascii_data}")
    return True

def bootload_interim_array():
    """Convert the interim file data to bootloader commands and send them to the MSPM0 chip."""
    global ser
    global data_for_verification_calc
    data_for_verification_calc.clear()  # Clear previous data for verification
    ui_status("Connect", port, "Connecting to MSPM0 bootloader")
    program_print("Sending Connection Command (0x12) to MSPM0 chip")
    build_packet(0x80, 0x12, bytearray())  # No data for connection command
    ser.write(data_packet)  # Send the data packet
    result = mspm0_wait_response(1, exp_bytes=1)  # wait 1 second
    if result is None or len(result) != 1 or result[0] != 0x00:
        program_print("***** ERROR: Failed to establish connection with MSPM0 chip, exiting. ******")
        return False
    ui_status("Read", "device info", "Reading MSPM0 device information")
    program_print("Issuing Get Device Info Command (0x19) to MSPM0 chip")
    build_packet(0x80, 0x19, bytearray())  # No data for Get Device Info command
    ser.write(data_packet)  # Send the data packet
    result = mspm0_wait_response(2)  # wait 2 seconds, expect multiple bytes
    if result is None or len(result) < 5:
        program_print("***** ERROR: Failed to get device info from MSPM0 chip, exiting. ******")
        return False
    program_print(f"Received Device Info: {result.hex()}, length ={len(result)} bytes")
    cmd_interp_version = int.from_bytes(result[5:7], 'little')  # Command Interpreter Version
    build_id = int.from_bytes(result[7:9], 'little')  # Build ID
    app_ver = int.from_bytes(result[9:13], 'little')  # Application Version
    plugin_ver = int.from_bytes(result[13:15], 'little')  # Plugin Version
    bsl_max_buf_size = int.from_bytes(result[15:17], 'little')  # BSL Max Buffer Size
    bsl_buf_start_addr = int.from_bytes(result[17:21], 'little')  # BSL Buffer Start Address
    bcr_id = int.from_bytes(result[21:25], 'little')  # BCR ID
    bsl_id = int.from_bytes(result[25:29], 'little')  # BSL ID
    if cmd_interp_version != 0x0100:
        program_print(f"***** ERROR: Unsupported Command Interpreter Version {cmd_interp_version:#04x}, exiting. ******")
        return False
    if build_id != 0x0100:
        program_print(f"***** ERROR: Unsupported Build ID {build_id:#04x}, exiting. ******")
        return False
    if app_ver != 0x00000000:
        program_print(f"***** ERROR: Unsupported Application Version {app_ver:#010x}, exiting. ******")
        return False
    if plugin_ver != 0x0001:
        program_print(f"***** ERROR: Unsupported Plugin Version {plugin_ver:#04x}, exiting. ******")
        return False
    if bsl_max_buf_size < 1024:
        program_print(f"***** ERROR: Unsupported BSL Max Buffer Size {bsl_max_buf_size:#04x}, exiting. ******")
        return False
    if bsl_buf_start_addr != 0x20000160:
        program_print(f"***** ERROR: Unsupported BSL Buffer Start Address {bsl_buf_start_addr:#010x}, exiting. ******")
        return False
    if bcr_id != 0x00000001:
        program_print(f"***** ERROR: Unsupported BCR ID {bcr_id:#010x}, exiting. ******")
        return False
    if bsl_id != 0x00000001:
        program_print(f"***** ERROR: Unsupported BSL ID {bsl_id:#010x}, exiting. ******")
        return False
    ui_status("Unlock", "bootloader", "Unlocking MSPM0 bootloader")
    program_print("Unlocking Bootloader (0x21)")
    build_packet(0x80, 0x21, bytearray([0xff] * 32))  # Send 32 bytes of 0xff for Unlock Bootloader command
    ser.write(data_packet)  # Send the data packet
    result = mspm0_wait_response(2)  # wait 2 seconds, expect multiple bytes
    succ = False
    if result is not None and len(result) >= 5:
        if result[4] == 0x3b: # BSL Core Message Response
            if result[5] == 0x00:  # Operation Successful
                succ = True
    if not succ:
        program_print("***** ERROR: Failed to unlock bootloader, exiting. ******")
        return False
    program_print("Bootloader unlocked successfully")
    ui_status("Erase", "flash", "Preparing flash erase")
    program_print("Performing Flash Range Erase (0x23) operation(s)")
    idx_addr_section = 256  # Index for the address in the interim file data
    num_addr_len_entries = int.from_bytes(interim_file_data[idx_addr_section+4:idx_addr_section+6], 'little')  # Number of addr_len entries
    idx_addr_section += 6  # Move to the start of the addr_len section
    erase_block_list = []  # List to hold the erase blocks
    addr_len_local_list = []  # Local list to hold address and length pairs
    for i in range(num_addr_len_entries):
        addr = int.from_bytes(interim_file_data[idx_addr_section+i*6:idx_addr_section+i*6+4], 'little')
        length = int.from_bytes(interim_file_data[idx_addr_section+i*6+4:idx_addr_section+i*6+6], 'little')
        addr_len_local_list.append((addr, length))
        # we can only erase 1kbyte blocks, so divide addr by 1024 and round down to the nearest 1kbyte block
        erase_start_block = (addr // 1024) * 1024  # Round down to the nearest 1kbyte block
        # check if addr + length is in the same 1kbyte block
        erase_end_block = ((addr + length - 1) // 1024) * 1024  # Round down to the nearest 1kbyte block
        program_print(f"Entry {i}: Address: {addr:#010x}, Length: {length} bytes, Erase Start Block: {erase_start_block:#010x}, Erase End Block: {erase_end_block:#010x}")
        # add erase_start_block and erase_end_block to the erase_block_list if not already present
        if erase_start_block not in erase_block_list:
            erase_block_list.append(erase_start_block)
        if erase_end_block not in erase_block_list:
            erase_block_list.append(erase_end_block)
    if len(erase_block_list) == 0:
        program_print("***** ERROR: No Flash Range Erase operations to perform, exiting. ******")
        return False
    for i in range(len(erase_block_list)):
        addr = erase_block_list[i]
        length = 1024  # Length is always 1024 bytes for each erase operation
        end_addr = addr + length - 1
        ui_status("Erase", f"{addr:#010x}", "Erasing flash blocks", progress=(i, len(erase_block_list)))
        program_print(f"Erasing Flash block: Address {addr:#010x}, length {length} bytes")
        build_packet(0x80, 0x23, addr.to_bytes(4, 'little') + end_addr.to_bytes(4, 'little'))
        # ser.write(data_packet)  # Send the data packet
        # print the packet for debugging
        program_print(f"Sending Flash Range Erase command: {data_packet.hex()}")
        ser.write(data_packet)
        result = mspm0_wait_response(1)  # wait 1 second, expect multiple bytes
        if result is None or len(result) < 10:
            program_print(f"***** ERROR: Failed to erase flash range at address {addr:#010x}, exiting. ******")
            return False
        succ = False
        if result[4] == 0x3b:  # BSL Core Message Response
            if result[5] == 0x00:  # Operation Successful
                succ = True
        if not succ:
            program_print(f"***** ERROR: Failed to erase flash range at address {addr:#010x}, exiting. ******")
            if result[4] == 0x3b:  # BSL Core Message Response
                if result[5] == 0x01:
                    program_print("BSL Lock Error")
                elif result[5] == 0x02:
                    program_print("BSL Password Error")
                elif result[5] == 0x05: 
                    program_print("Invalid Memory Range")
                elif result[5] == 0x0a:
                    program_print("Invalid Address or Length Alignment")
                else:
                    program_print(f"BSL Core Message Response MSG: {result[5]:#04x}")
            return False
    ui_status("Erase", "flash", "Flash erase complete", progress=(len(erase_block_list), len(erase_block_list)))
    program_print(f"{len(erase_block_list)} Flash Range Erase operation(s) completed successfully")
    program_print("Programming Data (0x20 operations) to MSPM0 chip")
    idx = 256
    # search for 'DATA' starting at 256 bytes offset
    idx_data_section = interim_file_data.find(b'DATA', idx)
    # first two bytes after 'DATA' are the length of the first data entry
    if idx_data_section == -1:
        program_print("***** ERROR: 'DATA' section not found in interim file data, exiting. ******")
        return False
    idx_data_section += 4  # Move to the length of the first data entry
    for i in range(len(addr_len_local_list)):
        addr, length = addr_len_local_list[i]
        data_length = int.from_bytes(interim_file_data[idx_data_section:idx_data_section+2], 'little')  # Length of first data entry
        # sanity: check that data_length is equal to the length in addr_len_local_list
        if data_length != length:
            program_print(f"***** ERROR: interim data internal inconsistency! *****")
            program_print(f"data length {data_length} for address {addr:#010x} does not match length {length}, exiting. ******")
            return False
        idx_data_section += 2  # Move to the start of the data entry
        data = interim_file_data[idx_data_section:idx_data_section+data_length]  # Data for the first address range
        # check that the data length is a multiple of 8 bytes
        if data_length % 8 != 0:
            program_print(f"***** ERROR: interim data internal inconsistency! *****")
            program_print(f"length {data_length} for address {addr:#010x} is not a multiple of 8 bytes, exiting. ******")
            return False
        ui_status("Program", f"{addr:#010x}", "Programming flash", progress=(i, len(addr_len_local_list)))
        program_print(f"Programming Data Entry {i}: Address: {addr:#010x}, Length: {data_length} bytes")
        build_packet(0x80, 0x20, addr.to_bytes(4, 'little') + data)  # Build the packet with address and data
        # print the packet for debugging
        program_print(f"Sending Program Data command: {data_packet.hex()}")
        ser.write(data_packet)  # Send the data packet
        result = mspm0_wait_response(1)  # wait 1 second, expect multiple bytes
        if result is None or len(result) < 10:
            program_print(f"***** ERROR: Failed to program data at address {addr:#010x}, exiting. ******")
            return False
        succ = False
        if result[4] == 0x3b:  # BSL Core Message Response
            if result[5] == 0x00:  # Operation Successful
                succ = True
        if not succ:
            program_print(f"***** ERROR: Failed to program data at address {addr:#010x}, exiting. ******")
            if result[4] == 0x3b:  # BSL Core Message Response
                if result[5] == 0x01:
                    program_print("BSL Lock Error")
                elif result[5] == 0x02:
                    program_print("BSL Password Error")
                elif result[5] == 0x05: 
                    program_print("Invalid Memory Range")
                elif result[5] == 0x0a:
                    program_print("Invalid Address or Length Alignment")
                else:
                    program_print(f"BSL Core Message Response MSG: {result[5]:#04x}")
            return False
        idx_data_section += data_length
    ui_status("Program", "flash", "Programming complete", progress=(len(addr_len_local_list), len(addr_len_local_list)))
    program_print(f"{len(addr_len_local_list)} Data Programming operation(s) completed successfully")
    ui_status("Start", "application", "Starting application")
    program_print(f"Sending Start Application Command (0x40) to MSPM0 chip")
    build_packet(0x80, 0x40, bytearray())  # No data for Start Application command
    ser.write(data_packet)  # Send the data packet

    result = mspm0_wait_response(1,exp_bytes=1)  # wait 1 second, expect 1 byte
    if result is None or len(result) != 1 or result[0] != 0x00:
        program_print("***** ERROR: Failed to start application on MSPM0 chip, exiting. ******")
        return False
    program_print("Application started on MSPM0 successfully")
    return True

def read_chip_contents():
    # not implemented yet
    return True

def sim_bsl_core_message(status_code):
    """Build a BSL Core Message with the given status code."""
    msg = bytearray([0x00, 0x08, 0x02, 0x00, 0x3b])  # BSL Core Message header
    msg.append(status_code)  # Status code
    msg.extend(calc_crc(msg[4:]))  # Calculate and append CRC
    return msg

def sim_parse_command(rx_data):
    header = rx_data[0]
    command = rx_data[3]
    if (command == 0x12):  # Connection command, we return a single byte: 0x00
        # rx_data example: 800100123a6144de
        program_print("Received connection command (0x12), responding with 0x00")
        ser.write(bytearray([0x00]))
    if (command == 0x19): # Get Device Info command
        # rx_data example: 80010019b2b89649
        program_print("Received Get Device Info command (0x19), responding with device info")
        response = bytearray([0x00, 0x08, 0x19, 0x00, 0x31])
        cmd_interp_version =0x0100
        build_id = 0x0100
        app_ver = 0x00000000
        plugin_ver = 0x0001
        bsl_max_buf_size = 0x06c0
        bsl_buf_start_addr = 0x20000160
        bcr_id = 0x00000001
        bsl_id = 0x00000001
        # append all the data to the response
        response.extend(cmd_interp_version.to_bytes(2, 'little'))
        response.extend(build_id.to_bytes(2, 'little'))
        response.extend(app_ver.to_bytes(4, 'little'))
        response.extend(plugin_ver.to_bytes(2, 'little'))
        response.extend(bsl_max_buf_size.to_bytes(2, 'little'))
        response.extend(bsl_buf_start_addr.to_bytes(4, 'little'))
        response.extend(bcr_id.to_bytes(4, 'little'))
        response.extend(bsl_id.to_bytes(4, 'little'))
        response.extend(calc_crc(response[4:]))
        program_print(f"Responding with device info: {response.hex()}")
        ser.write(response)
    if (command == 0x21):  # Unlock Bootloader command
        # rx_data example: 80210021ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff02aaf03d
        program_print("Received Unlock Bootloader command (0x21), responding with 'Operation Successful' BSL Core Message")
        response = sim_bsl_core_message(0x00)  # 0x00 is Operation Successful
        program_print(f"Responding with Operation Successful BSL Core Message: {response.hex()}")
        ser.write(response)
    if (command == 0x23):  # Flash Range Erase command
        # rx_data example: 80090023000000005702000042b6d4f7
        program_print("Received Flash Range Erase command (0x23), responding with 'Operation Successful' BSL Core Message")
        # confirm the length is 9, and then retrieve the Start and End addresses (4 bytes each)
        if len(rx_data) != 9 + 3 + 4:
            program_print("****** Error: Flash Range Erase command length is not 9 bytes! *****")
            return
        start_addr = int.from_bytes(rx_data[4:8], 'little')
        end_addr = int.from_bytes(rx_data[8:12], 'little')
        program_print(f"Start Address: {start_addr:#010x}, End Address: {end_addr:#010x}")
        response = sim_bsl_core_message(0x00)  # 0x00 is Operation Successful
        program_print(f"Responding with Operation Successful BSL Core Message: {response.hex()}")
        ser.write(response)
    if (command == 0x20):  # Program Data command
        # rx_data example: 805d02200000000000100020650100006101000061010000000000000000000000000000000000000000000000000000000000006101000000000000000000006101000061010000610100006101000061010000000000006101000000000000000000000000000000000000610100000000000000000000000000006101000000000000610100006101000000000000610100000000000061010000000000000000000000000000610100000000000000000000000000000000000000000000000000006101000010b500f045f880228120064b0649120199500649064a8850942280215201c9049950fee700000a4001000026008042400400001010b5054b054a0649102099500549043a995000f06bf810bd00000a4004080000030000b1010000267047c0460321074a13688b431360064a0c3113688b4300211360044a044bd1507047c04600010b4004010b4000f00a400813000010b5fff7cffffff7e1fffff7e1ff10bdfee7c0461548164b10b5984207d2013b1a1a920801321349920000f063f81248124b984207d2013b1a1a920801321049920000f057f80f480f4b984207d2013b1a1a920801320021920000f01ff800f025f8fff785fffff7d3ff10bd00000020000000205802000000000020000000205802000000000020000000207047c046831e043bc046fcd27047c04603008218934200d1704719700133f9e770b500260c4c0d4d641ba410a64209d10026fff7e5ff0a4c0a4d641ba410a64205d170bdb300eb5898470136eee7b300eb5898470136f2e758020000580200005802000058020000002310b59a4200d110bdcc5cc4540133f8e70000ea80b1e5
        program_print("Received Program Data command (0x20), responding with 'Operation Successful' BSL Core Message")
        # Second and third bytes contain the length in little-endian format
        length_field = int.from_bytes(rx_data[1:3], 'little')
        if (len(rx_data) != length_field + 3 + 4):
            program_print("****** Error: Program Data command length is not valid! *****")
            return
        # after the command, the next 4 bytes are the address, and then the data follows
        addr = int.from_bytes(rx_data[4:8], 'little')
        data = rx_data[8:-4]
        # Store the data in the data_for_verification_calc bytearray for later verification
        data_for_verification_calc.extend(data)  # Append the data to the data_for_verification_calc bytearray
        data_length = len(data)
        # check that the addr is 8 bytes aligned
        if addr % 8 != 0:
            program_print(f"****** Error: Address {addr:#010x} is not 8-byte aligned! *****")
            return
        # check that the data length is a multiple of 8 bytes
        if data_length % 8 != 0:
            program_print(f"****** Error: Data length {data_length} is not a multiple of 8 bytes! *****")
            return
        cksum_bytes = rx_data[-4:]  # Last 4 bytes are the CRC32 checksum
        program_print(f"Address: {addr:#010x}, Data Length: {len(data)}")
        # Verify the CRC32 checksum
        calculated_crc = calc_crc(rx_data[3:-4])
        if calculated_crc != cksum_bytes:
            program_print(f"****** Error: CRC32 checksum mismatch! Expected {calculated_crc.hex()}, got {cksum_bytes.hex()} *****")
            return
        response = sim_bsl_core_message(0x00)  # 0x00 is Operation Successful
        program_print(f"Responding with Operation Successful BSL Core Message: {response.hex()}")
        ser.write(response)
    if (command == 0x40):  # Start Application command
        # rx_data example: 80010040e251215b
        program_print("Received Start Application command (0x40), responding with 0x00")
        ser.write(bytearray([0x00]))
    if (command == 0x26):  # Standalone Verification command
        # rx_data example: 8009002600000000d8040000d229f40c
        # this command should only be received if more than 1 kbyte of data has been programmed
        data_addr_to_verify = int.from_bytes(rx_data[4:8], 'little')
        data_len_to_verify = int.from_bytes(rx_data[8:12], 'little')
        program_print(f"Received Standalone Verification command (0x26)")
        program_print(f"Address: {data_addr_to_verify:#010x}, Length: {data_len_to_verify} bytes")
        response = bytearray([0x00, 0x08, 0x05, 0x00, 0x32])  # BSL Core Message header with 0x32 for Standalone Verification Response
        # data_len_to_verify should be the same size as data_for_verification_calc
        if data_len_to_verify != len(data_for_verification_calc):
            program_print(f"****** Error: Data length to verify {data_len_to_verify} does not match programmed data length {len(data_for_verification_calc)}! *****")
        # calculate the checksum of the data_for_verification_calc, for length of data_len_to_verify
        cksum_bytes_for_data_for_verification_calc = calc_crc(data_for_verification_calc[:data_len_to_verify])
        response.extend(cksum_bytes_for_data_for_verification_calc)  # Append the calculated checksum to the response
        response.extend(calc_crc(response[4:]))  # Calculate and append CRC
        program_print(f"Responding with Operation Successful BSL Core Message: {response.hex()}")
        ser.write(response)

def sim_L1105():
    # wait for a serial command and parse it and respond to the command
    # wait for a serial command, assume it is complete if no data is received for 100 msec
    rx_data = bytearray()

    program_print("Waiting for serial command...")
    while True:
        byte = ser.read(1)  # Read one byte
        if not byte:  # No data received
            if rx_data:  # If we have received some data
                program_print(f"Received command: {rx_data.hex()}")
                # Process the command here
                rx_data.clear()  # Clear the buffer after processing
            continue
        
        rx_data.extend(byte)  # Append the received byte to the buffer
        
        if len(rx_data) >= 3:  # At least header + length + command
            length = int.from_bytes(rx_data[1:3], 'little')
            if len(rx_data) >= length + 4 + 3:  # Check if we have enough bytes (header + length + command + data + CRC)
                if len(rx_data) < 44:
                    program_print(f"Complete command received: {rx_data.hex()}")
                else:
                    program_print(f"Complete command received: {rx_data[:40].hex()} <truncated {len(rx_data) - 44} bytes> {rx_data[-4:].hex()}")
                sim_parse_command(rx_data)
                rx_data.clear()  # Clear the buffer after processing

def mspm0_wait_response(seconds, exp_bytes=0):
    """Wait for a response from the MSPM0 chip."""
    global ser
    response = bytearray()
    if _active_ui is not None:
        _active_ui.message = "Waiting for MSPM0 response"
    # program_print("Waiting for response from MSPM0 chip\n", end='', flush=True)
    unread_seconds = 0
    dot_printed = False
    while True:
        byte = ser.read(1)
        if not byte:
            if len(response) > 0:  # If we have received some data
                if dot_printed:
                    program_print("\n")
                return response
            # No data received, wait for a while
            unread_seconds += 1
            if dot_printed == False:
                program_print("Still waiting for response from MSPM0 chip", end='', flush=True)
            program_print('.', end='', flush=True)
            dot_printed = True
            if unread_seconds > seconds:
                program_print("\nNo response received from MSPM0 chip, giving up.")
                return None
        else:
            response.extend(byte)
            unread_seconds = 0
            if exp_bytes > 0 and len(response) >= exp_bytes:
                if dot_printed:
                    program_print("\n")
                return response
            if exp_bytes == 0 and len(response) > 4:
                length = int.from_bytes(response[2:4], 'little')
                if len(response) >= length + 8:
                    if dot_printed:
                        program_print("\n")
                    return response

def set_rts_high():
    """Set RTS line high (Weak pullup to 5V for CH340K)"""
    global ser
    ser.setRTS(False)
def set_rts_low():
    """Set RTS line low (Pulled to 0V for CH340K)"""
    global ser
    ser.setRTS(True)
def set_dtr_high():
    """Set DTR line high (Hard 5V logic output for CH340K)"""
    global ser
    ser.setDTR(False)  # False set CH340K to 5V (not 3.3V!)
def set_dtr_low():
    """Set DTR line low (Pulled to 0V for CH340K)"""
    global ser
    ser.setDTR(True)

def ser_test():
    global ser
    if serial is None:
        raise RuntimeError("pySerial is not installed; run: pip install pyserial")
    ser = serial.Serial(port, baudrate, rtscts=False, dsrdtr=False, timeout=1)
    ser.rtscts = True
    ser.rtscts = False
    ser.dsrdtr = True
    ser.dsrdtr = False
    while True:
        ser.setRTS(True)
        ser.setDTR(True)
        time.sleep(1)
        ser.setDTR(False)
        ser.setRTS(False)
        time.sleep(1)

def ser_open():
    global ser
    global rts_capability
    global dtr_capability

    if serial is None:
        program_print("pySerial is not installed. Install it with: pip install pyserial")
        if _active_ui is not None:
            stop_programmer_ui(
                "Failed",
                port,
                "pySerial is not installed",
                success=False,
                wait=True
            )
        raise SystemExit(1)

    # Catch errors such as a missing COM port, disconnected USB-UART, or the
    # port already being owned by another application.
    try:
        # open with rtscts and dsrdrt both false, if either rts_capability or dtr_capability are false
        if not rts_capability or not dtr_capability:
            ser = serial.Serial(port, baudrate, rtscts=False, dsrdtr=False, timeout=1)
        else:
            ser = serial.Serial(port, baudrate, rtscts=True, dsrdtr=True, timeout=1)
        program_print(f"Opened serial port successfully {port} at {baudrate} baud.")
    except (serial.SerialException, OSError) as e:
        program_print(f"ERROR: Could not open serial port {port}.")
        program_print(f"Exception: {e}")
        program_print("The port may already be open in another application.")
        program_print("Check that the USB-UART is connected and that the COM port name is correct.")
        program_print("Close any terminal, debugger, IDE serial monitor, or other program using the port and try again.")

        if _active_ui is not None:
            stop_programmer_ui(
                "Failed",
                port,
                f"Could not open {port} — it may already be in use",
                success=False,
                wait=True
            )
        raise SystemExit(1)
    try:
        if rts_capability and dtr_capability:
            # this weird thing is needed to make the CH340K RTS and DTR manual control work
            # set_rts_high()
            # ser.rtscts = True
            ser.rtscts = False
            # ser.dsrdtr = True
            ser.dsrdtr = False
            set_rts_high()  # get out of reset
            set_dtr_high()  # deasserts BOOT (inverted by PNP transistor)
    except serial.SerialException as e:
        program_print(f"Error setting RTS and/or DTR on serial port {port}, disabling both their capability")
        program_print(f"Exception: {e}")
        rts_capability = False
        dtr_capability = False
    set_rts_high()  # get out of reset (needed for boards with auto-bootloader and auto-reset, if those functions are not being used)

def ser_close():
    """Close the serial port."""
    global ser
    if ser and ser.is_open:
        ser.close()
        program_print(f"Closed serial port {port}.")
    else:
        program_print("Serial port is not open or already closed.")


def find_default_firmware():
    """Return a sensible firmware filename from the current working directory."""
    cwd = Path.cwd()

    # Prefer the conventional firmware.hex name if it exists.
    preferred = cwd / "firmware.hex"
    if preferred.is_file():
        return preferred.name

    # Otherwise use the first .hex file in a deterministic, case-insensitive order.
    hex_files = sorted(
        (p for p in cwd.iterdir() if p.is_file() and p.suffix.lower() == ".hex"),
        key=lambda p: p.name.lower()
    )
    if hex_files:
        return hex_files[0].name

    return "firmware.hex"


# main function
def main():
    """MSPM0 BSL programmer."""
    global ser
    global port
    global rts_capability
    global dtr_capability
    noprompt = False
    start_time = time.time()

    # Rich UI demonstration mode intentionally bypasses hardware and sanity output.
    if '--ui-demo' in sys.argv:
        run_rich_ui_demo()
        return

    # handle the command line arguments
    parser = argparse.ArgumentParser(description='MSPM0 BSL Programmer')
    parser.add_argument('--port', type=str, default=None, help='Serial port to use, e.g. COM4')
    parser.add_argument('firmware', type=str, nargs='?', help='Firmware file to program [.hex or .flash] or "sim" to simulate BSL')
    parser.add_argument('--auto', action='store_true', help='Automatic mode, no prompt, requires DTR and RTS capability')
    parser.add_argument('--saveflashfile', action='store_true', help='Save a copy of firmware.flash converted from the .hex file')
    parser.add_argument('--readchip', action='store_true', help='Read the chip contents and save to firmware_readback.hex file')
    parser.add_argument('--ui-demo', action='store_true', help='Show the Rich split-screen UI demo without accessing hardware')
    parser.add_argument('--plain', action='store_true', help='Disable the Rich full-screen UI')
    args = parser.parse_args()
    interactive_mode = args.firmware is None and not args.readchip

    if interactive_mode:
        if Console is None:
            parser.error("firmware is required unless --readchip is used")
        console = Console()
        console.print("[bold cyan]MSPM0 Programmer[/]")
        console.print("usage: [bold]mspm0_prog.py[/] [-h] [--port PORT] [--auto] [--saveflashfile] [--readchip] firmware\n")
        console.print("[bold cyan]Interactive Mode[/]")

        firmware_default = find_default_firmware()
        args.firmware = Prompt.ask(
            "Enter full .hex or .flash filename",
            default=firmware_default
        )

    # If --port was supplied, always honour it. Otherwise prompt in interactive use.
    if args.port is not None:
        port = args.port
    elif interactive_mode:
        port = Prompt.ask("Enter serial port", default=port)

    rich_active = False
    if not args.plain and args.firmware is not None and args.firmware.lower() != 'sim':
        rich_active = start_programmer_ui(args.firmware)
    if rich_active:
        program_print("MSPM0 Programmer")
    else:
        print_banner()
    sanity_check()

    if args.auto:
        noprompt = True
        if not dtr_capability or not rts_capability:
            program_print("***** WARNING: --auto option requires both DTR and RTS capability, disabling. *****")
            noprompt = False
    else:
        if rts_capability or dtr_capability:
            program_print("Disabling rts_capability and dtr_capability since --auto option is not used.")
            rts_capability = False
            dtr_capability = False

    # Read chip option
    if args.readchip:
        program_print("Reading chip contents...")
        ser_open()
        read_chip_contents()
        ser_close()
        return

    # Simulator mode
    if args.firmware.lower() == 'sim':
        program_print("Simulating MSPM0 BSL...")
        ser_open()
        sim_L1105()
        ser_close()
        return
    
    # Convert .hex to .flash if the firmware file is a .hex file
    if args.firmware.lower().endswith('.hex'):
        ui_status("Read", args.firmware, "Parsing Intel HEX firmware")
        program_print(f"Converting {args.firmware} to interim format...")
        hexparse(args.firmware)  # Parse the hex file into lists in memory
        build_interim_array()  # Build an interim format array from the lists
        if args.saveflashfile:
            flash_filename = args.firmware[:-4] + '.flash'
            try:
                with open(flash_filename, 'wb') as f:
                    f.write(interim_file_data)
                program_print(f"Saved interim .flash file as {flash_filename}")
            except IOError as e:
                program_print(f"Error saving interim .flash file: {e}")
            if port=='none':
                stop_programmer_ui("Done", args.firmware, "Flash file saved", success=True, wait=True)
                return
        ui_status("Open", port, "Opening serial port")
        ser_open()  # Open the serial port
        if noprompt:
            program_print(f"Auto mode, no prompt")
        else:
            msg = "Hold BOOT, press RESET, release BOOT, then continue"
            program_print("Hold down the BOOT button and then RESET the chip, then release the BOOT button.")
            if _active_ui is not None:
                ui_wait_for_key("Boot", port, msg, prompt="Press any key to continue")
            else:
                input("Press Enter to continue...")
        if dtr_capability:
            set_dtr_low()  # this asserts BOOT (sets BOOT high, inverted by PNP transistor)
        if rts_capability:
            set_rts_low()  # assert the *RESET line (active low)
            # time.sleep(0.1)  # doesn't seem necessary
            set_rts_high()  # get out of reset
# moved this further down, so that the *DTR line can also be used to 
# route the UART signals using a SN74CBTLV3257PWR analog switch, for the
# duration of the programming, which means no jumpers needed to be switched
# when using the EasyL1105 board
#        if dtr_capability:
#            time.sleep(0.01)
#            set_dtr_high()  #  deasserts BOOT (inverted by PNP transistor)
        programming_ok = bootload_interim_array()  # Convert interim array to bootloader commands
        if dtr_capability:
            time.sleep(0.01)
            set_dtr_high()  #  deasserts BOOT (inverted by PNP transistor)
        ser_close()
        if noprompt:
            stop_time = time.time()
            elapsed_time = stop_time - start_time
            program_print(f"Elapsed time: {elapsed_time:.2f} seconds")
        if programming_ok is False:
            program_print("Programming failed.")
            stop_programmer_ui("Failed", args.firmware, "Programming failed", success=False, wait=True)
            return
        stop_programmer_ui("Done", args.firmware, "Programming complete.", success=True, wait=True)
        if dtr_capability:
            set_dtr_high()  #  deasserts BOOT (inverted by PNP transistor)
        
        args.firmware = args.firmware[:-4] + '.flash'  # Change the extension to .flash

# Run the main function
if __name__ == "__main__":
    main()

