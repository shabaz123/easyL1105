# MSPM0 BSL Programmer
# rev 1 - shabaz - August 2025
# This program can be used to program MSPM0L110x devices via USB Serial Bootloader (BSL).
# Requires:
# pySerial:  pip install pyserial
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
import serial
import binascii
import time

port = 'COM6'  # Adjust this to your serial port
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
    # print(f"Data packet built: {data_packet.hex()}")

def sanity_check():
    # data is a bytearray of 32 0xff bytes
    data = bytearray([0xff] * 32)
    build_packet(0x80, 0x21, data)
    expected = bytearray([0x80, 0x21, 0x00, 0x21]) + data + bytearray([0x02, 0xaa, 0xf0, 0x3d])
    if data_packet != expected:
        print(f"***** ERROR - Sanity check failed: {data_packet.hex()} != {expected.hex()}")
        exit(1)
    else:
        print("Sanity check passed.")

def print_banner():
    print("\n\n\n\n\n\n");
    print("                      _     __ __  ___  _____ ");
    print("                     | |   /_ /_ |/ _ \| ____|");
    print("  ___  __ _ ___ _   _| |    | || | | | | |__  ");
    print(" / _ \/ _` / __| | | | |    | || | | | |___ \ ");
    print("|  __/ (_| \__ \ |_| | |____| || | |_| |___) |");
    print(" \___|\__,_|___/\__, |______|_||_|\___/|____/ ");
    print("                 __/ |                        ");
    print("                |___/                         ");
    print("MSPM0 BSL Programmer - rev 1 - shabaz - August 2025")
    print(" ");


def hexparse(hex_file):
    """Read an Intel HEX file to memory and parse it into address and data lists."""
    # create a list to hold address and length values (32-bit address, 32-bit length, little-endian)
    global addr_len_list
    global data_list
    addr_len_list= []
    # we create a list of bytearrays to hold the data for each address range in addr_len_list
    data_list = []
    cur_data_bytes = bytearray()  # Current data bytes for the current address range
    max_data_len = 1024  # Maximum data length for each address range, divisible by 8 bytes
    tot_data_len = 0  # Total data length across all address ranges
    next_contig_addr = 0xbaadbeef  # Next contiguous address, initialized to an unexpected value
    upper_addr_word = 0x0000  # Upper address word, initialized to 0x0000
    # open the hex file and read each line
    with open(hex_file, 'r') as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line or not line.startswith(':'):
                print(f"line # {line_num}: Skipping content: '{line}'")
                continue  # Skip empty lines and comment lines
            # Parse the Intel HEX line
            print(f"processing line {line_num}: {line}")
            try:
                byte_count = int(line[1:3], 16)  # Byte count is in bytes 1-2 (2 hex digits)
                addr = int(line[3:7], 16)  # 16-bit address is in bytes 3-6 (4 hex digits)
                record_type = int(line[7:9], 16)  # Record type is in bytes 7-8 (2 hex digits)
                data = bytes.fromhex(line[9:-2])  # Data is from byte 9 to the end, excluding CRC
            except ValueError as e:
                print(f"Error parsing line '{line}': {e}")
            if record_type == 4: # Extended Linear Address Record (0x04) - upper address word
                upper_addr_word = int.from_bytes(data, 'big') << 16 # Shift left by 16 bits to get the upper address word
            elif record_type == 0:  # Data Record (0x00)
                addr32 = (upper_addr_word | addr) & 0xFFFFFFFF  # Combine upper and lower address
                if addr32 != next_contig_addr:  
                    # if we have cur_data_bytes, we need to store it in the data_list
                    if cur_data_bytes:
                        # Store the data bytes so far in the data_list
                        data_list.append(cur_data_bytes)
                        # Update the length for the previous address range
                        addr_len_list[-1] = (addr_len_list[-1][0], len(cur_data_bytes))
                        tot_data_len += len(cur_data_bytes)
                        # now handle the new non-contiguous address
                        # append the address, we will update the length later
                        if (len(addr_len_list) > 0) and (addr_len_list[-1][1] == 0):
                            # if the last address length is 0, we can just update it with the new address
                            addr_len_list[-1] = (addr32, 0)
                        else:
                            addr_len_list.append((addr32, 0))
                        cur_data_bytes = bytearray()
                    else:
                        # if we have no cur_data_bytes, we can just append the address and length 0
                        # (we will update the length later)
                        if (len(addr_len_list) > 0) and (addr_len_list[-1][1] == 0):
                            # if the last address length is 0, we can just update it with the new address
                            addr_len_list[-1] = (addr32, 0)
                        else:
                            addr_len_list.append((addr32, 0))
                # append the new data bytes to cur_data_bytes
                cur_data_bytes.extend(data)
                # update the contiguous address
                next_contig_addr = addr32 + len(data)
            else:
                # for all other record types, we save any current data bytes
                if len(cur_data_bytes) > 0:
                    # Store the data bytes so far in the data_list
                    data_list.append(cur_data_bytes)
                    # Update the length for the previous address range
                    addr_len_list[-1] = (addr_len_list[-1][0], len(cur_data_bytes))
                    tot_data_len += len(cur_data_bytes)
                    cur_data_bytes = bytearray()
                if (record_type == 3) | (record_type == 5):
                    # End of file record (0x01), Extended Linear Address Record (0x04) or Extended Linear Address Record (0x05)
                    # We can ignore these for our purposes
                    continue
                elif record_type == 1:
                    print(f"End of file record (0x01) on line {line_num}, finished reading .hex file")
                    break
                else:
                    print(f"**** WARNING Unknown record type {record_type} encountered, skipping line {line_num}: {line} ****")
                    continue
            # if we have reached the maximum data length, we need to store the data bytes
            if len(cur_data_bytes) >= max_data_len:
                # Store the max_data_len of bytes, and retain the remaining bytes for the next address range
                data_list.append(cur_data_bytes[:max_data_len])
                # Update the length for the previous address range
                addr_len_list[-1] = (addr_len_list[-1][0], max_data_len)
                tot_data_len += max_data_len
                cur_data_bytes = cur_data_bytes[max_data_len:]  # Retain the remaining bytes for the next address range
                if len(cur_data_bytes) >= 0:
                    # Append a new address range with the remaining bytes
                    addr_len_list.append((next_contig_addr, 0))
    # Check that there are no remaining data bytes
    if len(cur_data_bytes) > 0:
        print(f"**** ERROR: There are {len(cur_data_bytes)} bytes remaining in cur_data_bytes, aborting! ****")
        return False
    # Check that all addresses in the addr_len_list are divisible by 8 bytes
    print(f"Sanity checking the .hex content...")
    # check that the length of addr_len_list and data_list are the same
    if len(addr_len_list) != len(data_list):
        print(f"**** ERROR: Length of addr_len_list ({len(addr_len_list)}) does not match length of data_list ({len(data_list)}), aborting! ****")
        return False
    # chech that no length in addr_len_list is 0
    for addr, length in addr_len_list:
        if length == 0:
            print(f"**** ERROR: Address {addr:#010x} has length 0, aborting! ****")
            return False
    # check that the addresses are 8-byte aligned
    for addr,length in addr_len_list:
        if addr % 8 != 0:
            print(f"**** ERROR: Address {addr:#010x} is not 8-byte aligned, aborting! ****")
            return False
    # for i in range(len(addr_len_list)):
    #    print(f"info: addr_len_list[{i}] = {addr_len_list[i]}")  # Print the address and length for each entry     
    
    # for any length in addr_len_list, if it is not divisible by 8 bytes, we need to
    # pad the data in data_list with 0xff bytes to make it divisible by 8 bytes, and
    # update the length in addr_len_list to be the padded length
    for i in range(len(addr_len_list)):
        addr, length = addr_len_list[i]
        if length % 8 != 0:
            padding_length = 8 - (length % 8)
            # calculate address that needs padding
            addr_pad_start = addr + length  # Start address for padding
            addr_pad_stop = addr_pad_start + padding_length - 1  # Stop address for padding
            print(f"Data length for Entry {i} is not divisible by 8, padding..")
            print(f"Padding Entry {i} with {padding_length} x '0xff' byte(s) at {addr_pad_start:#010x}-{addr_pad_stop:#010x}")
            data_list[i] += bytearray([0xff] * padding_length)  # Pad with 0xff bytes
            addr_len_list[i] = (addr, length + padding_length)  # Update the length
    # Now we have addr_len_list and data_list ready, let's print all the details
    # print the addr_len_list contents
    i = 0
    for addr, length in addr_len_list:
        print(f"  Addr/Len Entry {i}: Address: {addr:#010x}, Length: {length} bytes")
        i += 1
    # print the data_list contents
    for i, data in enumerate(data_list):
        print(f"  Data Entry {i}: Length: {len(data)} bytes, Content: {data.hex()}")

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
    print("Interim file data:")
    for i in range(0, len(interim_file_data), 16):
        line = interim_file_data[i:i+16]
        hex_data = ' '.join(f'{b:02x}' for b in line)
        ascii_data = ''.join(chr(b) if 32 <= b < 127 else '.' for b in line)
        print(f"{i:04x} : {hex_data:<48} : {ascii_data}")
    return True

def bootload_interim_array():
    """Convert the interim file data to bootloader commands and send them to the MSPM0 chip."""
    global ser
    global data_for_verification_calc
    data_for_verification_calc.clear()  # Clear previous data for verification
    print("Sending Connection Command (0x12) to MSPM0 chip")
    build_packet(0x80, 0x12, bytearray())  # No data for connection command
    ser.write(data_packet)  # Send the data packet
    result = mspm0_wait_response(1, exp_bytes=1)  # wait 1 second
    if result is None or len(result) != 1 or result[0] != 0x00:
        print("***** ERROR: Failed to establish connection with MSPM0 chip, exiting. ******")
        return False
    print("Issuing Get Device Info Command (0x19) to MSPM0 chip")
    build_packet(0x80, 0x19, bytearray())  # No data for Get Device Info command
    ser.write(data_packet)  # Send the data packet
    result = mspm0_wait_response(2)  # wait 2 seconds, expect multiple bytes
    if result is None or len(result) < 5:
        print("***** ERROR: Failed to get device info from MSPM0 chip, exiting. ******")
        return False
    print(f"Received Device Info: {result.hex()}, length ={len(result)} bytes")
    cmd_interp_version = int.from_bytes(result[5:7], 'little')  # Command Interpreter Version
    build_id = int.from_bytes(result[7:9], 'little')  # Build ID
    app_ver = int.from_bytes(result[9:13], 'little')  # Application Version
    plugin_ver = int.from_bytes(result[13:15], 'little')  # Plugin Version
    bsl_max_buf_size = int.from_bytes(result[15:17], 'little')  # BSL Max Buffer Size
    bsl_buf_start_addr = int.from_bytes(result[17:21], 'little')  # BSL Buffer Start Address
    bcr_id = int.from_bytes(result[21:25], 'little')  # BCR ID
    bsl_id = int.from_bytes(result[25:29], 'little')  # BSL ID
    if cmd_interp_version != 0x0100:
        print(f"***** ERROR: Unsupported Command Interpreter Version {cmd_interp_version:#04x}, exiting. ******")
        return False
    if build_id != 0x0100:
        print(f"***** ERROR: Unsupported Build ID {build_id:#04x}, exiting. ******")
        return False
    if app_ver != 0x00000000:
        print(f"***** ERROR: Unsupported Application Version {app_ver:#010x}, exiting. ******")
        return False
    if plugin_ver != 0x0001:
        print(f"***** ERROR: Unsupported Plugin Version {plugin_ver:#04x}, exiting. ******")
        return False
    if bsl_max_buf_size < 1024:
        print(f"***** ERROR: Unsupported BSL Max Buffer Size {bsl_max_buf_size:#04x}, exiting. ******")
        return False
    if bsl_buf_start_addr != 0x20000160:
        print(f"***** ERROR: Unsupported BSL Buffer Start Address {bsl_buf_start_addr:#010x}, exiting. ******")
        return False
    if bcr_id != 0x00000001:
        print(f"***** ERROR: Unsupported BCR ID {bcr_id:#010x}, exiting. ******")
        return False
    if bsl_id != 0x00000001:
        print(f"***** ERROR: Unsupported BSL ID {bsl_id:#010x}, exiting. ******")
        return False
    print("Unlocking Bootloader (0x21)")
    build_packet(0x80, 0x21, bytearray([0xff] * 32))  # Send 32 bytes of 0xff for Unlock Bootloader command
    ser.write(data_packet)  # Send the data packet
    result = mspm0_wait_response(2)  # wait 2 seconds, expect multiple bytes
    succ = False
    if result is not None and len(result) >= 5:
        if result[4] == 0x3b: # BSL Core Message Response
            if result[5] == 0x00:  # Operation Successful
                succ = True
    if not succ:
        print("***** ERROR: Failed to unlock bootloader, exiting. ******")
        return False
    print("Bootloader unlocked successfully")
    print("Performing Flash Range Erase (0x23) operation(s)")
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
        print(f"Entry {i}: Address: {addr:#010x}, Length: {length} bytes, Erase Start Block: {erase_start_block:#010x}, Erase End Block: {erase_end_block:#010x}")
        # add erase_start_block and erase_end_block to the erase_block_list if not already present
        if erase_start_block not in erase_block_list:
            erase_block_list.append(erase_start_block)
        if erase_end_block not in erase_block_list:
            erase_block_list.append(erase_end_block)
    if len(erase_block_list) == 0:
        print("***** ERROR: No Flash Range Erase operations to perform, exiting. ******")
        return False
    for i in range(len(erase_block_list)):
        addr = erase_block_list[i]
        length = 1024  # Length is always 1024 bytes for each erase operation
        print(f"Erasing Flash block: Address {addr:#010x}, length {length} bytes")
        build_packet(0x80, 0x23, addr.to_bytes(4, 'little') + length.to_bytes(4, 'little'))
        # ser.write(data_packet)  # Send the data packet
        # print the packet for debugging
        print(f"Sending Flash Range Erase command: {data_packet.hex()}")
        ser.write(data_packet)
        result = mspm0_wait_response(1)  # wait 1 second, expect multiple bytes
        if result is None or len(result) < 10:
            print(f"***** ERROR: Failed to erase flash range at address {addr:#010x}, exiting. ******")
            return False
        succ = False
        if result[4] == 0x3b:  # BSL Core Message Response
            if result[5] == 0x00:  # Operation Successful
                succ = True
        if not succ:
            print(f"***** ERROR: Failed to erase flash range at address {addr:#010x}, exiting. ******")
            if result[4] == 0x3b:  # BSL Core Message Response
                if result[5] == 0x01:
                    print("BSL Lock Error")
                elif result[5] == 0x02:
                    print("BSL Password Error")
                elif result[5] == 0x05: 
                    print("Invalid Memory Range")
                elif result[5] == 0x0a:
                    print("Invalid Address or Length Alignment")
                else:
                    print(f"BSL Core Message Response MSG: {result[5]:#04x}")
            return False
    print(f"{len(erase_block_list)} Flash Range Erase operation(s) completed successfully")
    print("Programming Data (0x20 operations) to MSPM0 chip")
    idx = 256
    # search for 'DATA' starting at 256 bytes offset
    idx_data_section = interim_file_data.find(b'DATA', idx)
    # first two bytes after 'DATA' are the length of the first data entry
    if idx_data_section == -1:
        print("***** ERROR: 'DATA' section not found in interim file data, exiting. ******")
        return False
    idx_data_section += 4  # Move to the length of the first data entry
    for i in range(len(addr_len_local_list)):
        addr, length = addr_len_local_list[i]
        data_length = int.from_bytes(interim_file_data[idx_data_section:idx_data_section+2], 'little')  # Length of first data entry
        # sanity: check that data_length is equal to the length in addr_len_local_list
        if data_length != length:
            print(f"***** ERROR: interim data internal inconsistency! *****")
            print(f"data length {data_length} for address {addr:#010x} does not match length {length}, exiting. ******")
            return False
        idx_data_section += 2  # Move to the start of the data entry
        data = interim_file_data[idx_data_section:idx_data_section+data_length]  # Data for the first address range
        # check that the data length is a multiple of 8 bytes
        if data_length % 8 != 0:
            print(f"***** ERROR: interim data internal inconsistency! *****")
            print(f"length {data_length} for address {addr:#010x} is not a multiple of 8 bytes, exiting. ******")
            return False
        print(f"Programming Data Entry {i}: Address: {addr:#010x}, Length: {data_length} bytes")
        build_packet(0x80, 0x20, addr.to_bytes(4, 'little') + data)  # Build the packet with address and data
        ser.write(data_packet)  # Send the data packet
        result = mspm0_wait_response(1)  # wait 1 second, expect multiple bytes
        if result is None or len(result) < 10:
            print(f"***** ERROR: Failed to program data at address {addr:#010x}, exiting. ******")
            return False
        succ = False
        if result[4] == 0x3b:  # BSL Core Message Response
            if result[5] == 0x00:  # Operation Successful
                succ = True
        if not succ:
            print(f"***** ERROR: Failed to program data at address {addr:#010x}, exiting. ******")
            if result[4] == 0x3b:  # BSL Core Message Response
                if result[5] == 0x01:
                    print("BSL Lock Error")
                elif result[5] == 0x02:
                    print("BSL Password Error")
                elif result[5] == 0x05: 
                    print("Invalid Memory Range")
                elif result[5] == 0x0a:
                    print("Invalid Address or Length Alignment")
                else:
                    print(f"BSL Core Message Response MSG: {result[5]:#04x}")
            return False
        idx_data_section += data_length
    print(f"{len(addr_len_local_list)} Data Programming operation(s) completed successfully")
    print(f"Sending Start Application Command (0x40) to MSPM0 chip")
    build_packet(0x80, 0x40, bytearray())  # No data for Start Application command
    ser.write(data_packet)  # Send the data packet
    result = mspm0_wait_response(1,exp_bytes=1)  # wait 1 second, expect 1 byte
    if result is None or len(result) != 1 or result[0] != 0x00:
        print("***** ERROR: Failed to start application on MSPM0 chip, exiting. ******")
        return False
    print("Application started on MSPM0 successfully")

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
        print("Received connection command (0x12), responding with 0x00")
        ser.write(bytearray([0x00]))
    if (command == 0x19): # Get Device Info command
        # rx_data example: 80010019b2b89649
        print("Received Get Device Info command (0x19), responding with device info")
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
        print(f"Responding with device info: {response.hex()}")
        ser.write(response)
    if (command == 0x21):  # Unlock Bootloader command
        # rx_data example: 80210021ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff02aaf03d
        print("Received Unlock Bootloader command (0x21), responding with 'Operation Successful' BSL Core Message")
        response = sim_bsl_core_message(0x00)  # 0x00 is Operation Successful
        print(f"Responding with Operation Successful BSL Core Message: {response.hex()}")
        ser.write(response)
    if (command == 0x23):  # Flash Range Erase command
        # rx_data example: 80090023000000005702000042b6d4f7
        print("Received Flash Range Erase command (0x23), responding with 'Operation Successful' BSL Core Message")
        # confirm the length is 9, and then retrieve the Start and End addresses (4 bytes each)
        if len(rx_data) != 9 + 3 + 4:
            print("****** Error: Flash Range Erase command length is not 9 bytes! *****")
            return
        start_addr = int.from_bytes(rx_data[4:8], 'little')
        end_addr = int.from_bytes(rx_data[8:12], 'little')
        print(f"Start Address: {start_addr:#010x}, End Address: {end_addr:#010x}")
        response = sim_bsl_core_message(0x00)  # 0x00 is Operation Successful
        print(f"Responding with Operation Successful BSL Core Message: {response.hex()}")
        ser.write(response)
    if (command == 0x20):  # Program Data command
        # rx_data example: 805d02200000000000100020650100006101000061010000000000000000000000000000000000000000000000000000000000006101000000000000000000006101000061010000610100006101000061010000000000006101000000000000000000000000000000000000610100000000000000000000000000006101000000000000610100006101000000000000610100000000000061010000000000000000000000000000610100000000000000000000000000000000000000000000000000006101000010b500f045f880228120064b0649120199500649064a8850942280215201c9049950fee700000a4001000026008042400400001010b5054b054a0649102099500549043a995000f06bf810bd00000a4004080000030000b1010000267047c0460321074a13688b431360064a0c3113688b4300211360044a044bd1507047c04600010b4004010b4000f00a400813000010b5fff7cffffff7e1fffff7e1ff10bdfee7c0461548164b10b5984207d2013b1a1a920801321349920000f063f81248124b984207d2013b1a1a920801321049920000f057f80f480f4b984207d2013b1a1a920801320021920000f01ff800f025f8fff785fffff7d3ff10bd00000020000000205802000000000020000000205802000000000020000000207047c046831e043bc046fcd27047c04603008218934200d1704719700133f9e770b500260c4c0d4d641ba410a64209d10026fff7e5ff0a4c0a4d641ba410a64205d170bdb300eb5898470136eee7b300eb5898470136f2e758020000580200005802000058020000002310b59a4200d110bdcc5cc4540133f8e70000ea80b1e5
        print("Received Program Data command (0x20), responding with 'Operation Successful' BSL Core Message")
        # Second and third bytes contain the length in little-endian format
        length_field = int.from_bytes(rx_data[1:3], 'little')
        if (len(rx_data) != length_field + 3 + 4):
            print("****** Error: Program Data command length is not valid! *****")
            return
        # after the command, the next 4 bytes are the address, and then the data follows
        addr = int.from_bytes(rx_data[4:8], 'little')
        data = rx_data[8:-4]
        # Store the data in the data_for_verification_calc bytearray for later verification
        data_for_verification_calc.extend(data)  # Append the data to the data_for_verification_calc bytearray
        data_length = len(data)
        # check that the addr is 8 bytes aligned
        if addr % 8 != 0:
            print(f"****** Error: Address {addr:#010x} is not 8-byte aligned! *****")
            return
        # check that the data length is a multiple of 8 bytes
        if data_length % 8 != 0:
            print(f"****** Error: Data length {data_length} is not a multiple of 8 bytes! *****")
            return
        cksum_bytes = rx_data[-4:]  # Last 4 bytes are the CRC32 checksum
        print(f"Address: {addr:#010x}, Data Length: {len(data)}")
        # Verify the CRC32 checksum
        calculated_crc = calc_crc(rx_data[3:-4])
        if calculated_crc != cksum_bytes:
            print(f"****** Error: CRC32 checksum mismatch! Expected {calculated_crc.hex()}, got {cksum_bytes.hex()} *****")
            return
        response = sim_bsl_core_message(0x00)  # 0x00 is Operation Successful
        print(f"Responding with Operation Successful BSL Core Message: {response.hex()}")
        ser.write(response)
    if (command == 0x40):  # Start Application command
        # rx_data example: 80010040e251215b
        print("Received Start Application command (0x40), responding with 0x00")
        ser.write(bytearray([0x00]))
    if (command == 0x26):  # Standalone Verification command
        # rx_data example: 8009002600000000d8040000d229f40c
        # this command should only be received if more than 1 kbyte of data has been programmed
        data_addr_to_verify = int.from_bytes(rx_data[4:8], 'little')
        data_len_to_verify = int.from_bytes(rx_data[8:12], 'little')
        print(f"Received Standalone Verification command (0x26)")
        print(f"Address: {data_addr_to_verify:#010x}, Length: {data_len_to_verify} bytes")
        response = bytearray([0x00, 0x08, 0x05, 0x00, 0x32])  # BSL Core Message header with 0x32 for Standalone Verification Response
        # data_len_to_verify should be the same size as data_for_verification_calc
        if data_len_to_verify != len(data_for_verification_calc):
            print(f"****** Error: Data length to verify {data_len_to_verify} does not match programmed data length {len(data_for_verification_calc)}! *****")
        # calculate the checksum of the data_for_verification_calc, for length of data_len_to_verify
        cksum_bytes_for_data_for_verification_calc = calc_crc(data_for_verification_calc[:data_len_to_verify])
        response.extend(cksum_bytes_for_data_for_verification_calc)  # Append the calculated checksum to the response
        response.extend(calc_crc(response[4:]))  # Calculate and append CRC
        print(f"Responding with Operation Successful BSL Core Message: {response.hex()}")
        ser.write(response)

def sim_L1105():
    # wait for a serial command and parse it and respond to the command
    # wait for a serial command, assume it is complete if no data is received for 100 msec
    rx_data = bytearray()

    print("Waiting for serial command...")
    while True:
        byte = ser.read(1)  # Read one byte
        if not byte:  # No data received
            if rx_data:  # If we have received some data
                print(f"Received command: {rx_data.hex()}")
                # Process the command here
                rx_data.clear()  # Clear the buffer after processing
            continue
        
        rx_data.extend(byte)  # Append the received byte to the buffer
        
        if len(rx_data) >= 3:  # At least header + length + command
            length = int.from_bytes(rx_data[1:3], 'little')
            if len(rx_data) >= length + 4 + 3:  # Check if we have enough bytes (header + length + command + data + CRC)
                if len(rx_data) < 44:
                    print(f"Complete command received: {rx_data.hex()}")
                else:
                    print(f"Complete command received: {rx_data[:40].hex()} <truncated {len(rx_data) - 44} bytes> {rx_data[-4:].hex()}")
                sim_parse_command(rx_data)
                rx_data.clear()  # Clear the buffer after processing

def mspm0_wait_response(seconds, exp_bytes=0):
    """Wait for a response from the MSPM0 chip."""
    global ser
    response = bytearray()
    print("Waiting for response from MSPM0 chip", end='', flush=True)
    unread_seconds = 0
    while True:
        byte = ser.read(1)
        if not byte:
            if len(response) > 0:  # If we have received some data
                print("\n")
                return response
            # No data received, wait for a while
            unread_seconds += 1
            print('.', end='', flush=True)
            if unread_seconds > seconds:
                print("\nNo response received from MSPM0 chip, giving up.")
                return None
        else:
            response.extend(byte)
            unread_seconds = 0
            if exp_bytes > 0 and len(response) >= exp_bytes:
                print("\n")
                return response
            if exp_bytes == 0 and len(response) > 4:
                length = int.from_bytes(response[2:4], 'little')
                if len(response) >= length + 8:
                    print("\n")
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
    # catch error if serial port is not available
    try:
        ser = serial.Serial(port, baudrate, rtscts=False, dsrdtr=False, timeout=1)
        print(f"Opened serial port {port} at {baudrate} baud.")
    except serial.SerialException as e:
        print(f"Error opening serial port {port}")
        print(f"Exception: {e}")
        print("Is the USB-UART connected and is the com port value correct?")
        print("You can specify the port using the --port argument, e.g. --port COM6")
        exit(1)
    try:
        # this weird thing is needed to make the CH340K RTS and DTR manual control work
        set_rts_high()
        ser.rtscts = True
        ser.rtscts = False
        ser.dsrdtr = True
        ser.dsrdtr = False
        set_rts_high()  # get out of reset
        set_dtr_high()  # deasserts BOOT (inverted by PNP transistor)
    except serial.SerialException as e:
        print(f"Error setting RTS and/or DTR on serial port {port}, disabling both their capability")
        print(f"Exception: {e}")
        rts_capability = False
        dtr_capability = False


def ser_close():
    """Close the serial port."""
    global ser
    if ser and ser.is_open:
        ser.close()
        print(f"Closed serial port {port}.")
    else:
        print("Serial port is not open or already closed.")

# main function
def main():
    """MSPM0 BSL programmer."""
    global ser
    global port
    global rts_capability
    global dtr_capability
    noprompt = False
    start_time = time.time()
    print_banner()
    sanity_check()

    # handle the command line arguments
    parser = argparse.ArgumentParser(description='MSPM0 BSL Programmer')
    parser.add_argument('--port', type=str, default=port, help='Serial port to use (default: COM6)')
    parser.add_argument('firmware', type=str, help='Firmware file to program [.hex or .flash] or "sim" to simulate BSL')
    parser.add_argument('--auto', action='store_true', help='Automatic mode, no prompt, requires DTR and RTS capability')
    parser.add_argument('--saveflashfile', action='store_true', help='Save a copy of firmware.flash converted from the .hex file')
    args = parser.parse_args()
    if args.port:
        port = args.port
    if args.auto:
        noprompt = True
        if not dtr_capability or not rts_capability:
            print("***** WARNING: --auto option requires both DTR and RTS capability, disabling. *****")
            noprompt = False
    else:
        if rts_capability or dtr_capability:
            print("Disabling rts_capability and dtr_capability since --auto option is not used.")
            rts_capability = False
            dtr_capability = False

    # Simulator mode
    if args.firmware.lower() == 'sim':
        print("Simulating MSPM0 BSL...")
        ser_open()
        sim_L1105()
        ser_close()
        return
    
    # Convert .hex to .flash if the firmware file is a .hex file
    if args.firmware.lower().endswith('.hex'):
        print(f"Converting {args.firmware} to interim format...")
        hexparse(args.firmware)  # Parse the hex file into lists in memory
        build_interim_array()  # Build an interim format array from the lists
        if args.saveflashfile:
            flash_filename = args.firmware[:-4] + '.flash'
            try:
                with open(flash_filename, 'wb') as f:
                    f.write(interim_file_data)
                print(f"Saved interim .flash file as {flash_filename}")
            except IOError as e:
                print(f"Error saving interim .flash file: {e}")
            if port=='none':
                return
        ser_open()  # Open the serial port
        if noprompt:
            print(f"Auto mode, no prompt")
        else:
            print(f"Hold down the BOOT button and then RESET the chip, then release the BOOT button. Press Enter to continue...")
            input()  # Wait for user to press Enter
        if dtr_capability:
            set_dtr_low()  # this asserts BOOT (sets BOOT high, inverted by PNP transistor)
        if rts_capability:
            set_rts_low()  # assert the *RESET line (active low)
            # time.sleep(0.1)  # doesn't seem necessary
            set_rts_high()  # get out of reset
        if dtr_capability:
            time.sleep(0.01)
            set_dtr_high()  #  deasserts BOOT (inverted by PNP transistor)
        bootload_interim_array()  # Convert the interim array to bootloader commands and send to MSPM0 chip
        ser_close()
        if noprompt:
            stop_time = time.time()
            elapsed_time = stop_time - start_time
            print(f"Elapsed time: {elapsed_time:.2f} seconds")
        print("Programming complete.")
        
        args.firmware = args.firmware[:-4] + '.flash'  # Change the extension to .flash

# Run the main function
if __name__ == "__main__":
    main()

