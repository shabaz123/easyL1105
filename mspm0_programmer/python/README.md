# MSPM0 BSL Programmer
This program can be used to program MSPM0L110x devices via USB Serial Bootloader (BSL).

# Requirements:
Python

pySerial:  pip install pyserial

# How Does it Work?
Compile/build your MSPM0 application as usual, until you have a .hex (Intel Hex format) file generated.

Next, connect the MSPM0 board to the PC using a USB-UART adapter.

Run the Python program using a command such as:
```
python ./mspm0_prog.py --port COM15 myapp.hex
```

It will prompt you to hold down the BOOT button, press and release RESET, and then release the BOOT button. After that, follow the prompt and the new firmware should be programmed in seconds.

When the Python code is run, in the background it is first internally converting the .hex file into an 'interim' format which I've called a .flash format, and then the Python code converts the .flash format into serial commands that the MSPM0 bootloader understands, sent over a USB UART adapter.

The reason for the interim format is that it is closer to what the MSPM0 requires, plus, it will be useful in future for using with other programmer software.

# Usage
## Program a MSPM0 chip using the .hex file
```
python ./mspm0_prog.py [--port COMx] [--auto] firmware.hex
```
Example:

python ./mspm0_prog.py --port COM15 myapp.hex

## Just generate a .flash file

```
python ./mspm0_prog.py --port none --saveflashfile firmware.hex
```
The above command won't program the MSPM0 chip (since ***port none*** is specified), but will just generate a .flash file with the same name as the source .hex file.

Example:

python ./mspm0_prog.py --port none --saveflashfile myapp.hex

The above will generate a myapp.flash file

## Program a MSPM0 chip from a .flash "interim" file
NOTE: This is todo, it is not currently implemented!

```
python ./mspm0_prog.py [--port COMx] [--auto] firmware.flash
```

By specifying a filename with .flash, the code will program the MSPM0 from the flash file, rather than from the .hex file. Not currently implemented!

## Simulating an MSPM0
NOTE: This is not normally something you'd want to do, but might be helpful for testing programmer software, if a real MSPM0 is not at hand.

```
python ./mspm0_prog.py [--port COMx] sim
```


By specifying ***sim***, the code will simulate the MSPM0 BSL and respond to commands

## Additional flags

The ***--auto*** option is used if you have an MSPM0 device with special connections from the UART chip to the RESET and BOOT connections. If so, the Python code will not prompt the user to perform the BOOT/RESET button sequence, and will instead automatically try to boot/reset the device

Example:

```
python ./mspm0_prog.py --port COM5 --auto myprog.hex
```
