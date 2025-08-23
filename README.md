# easyL1105 - What is it?
This repository contains resources for working with the TI MSPM0L1105 Microcontroller.
KiCad files, and Gerber files are present for building a simple development board, called easyL1105. 

<img width="100%" align="left" src="msp-render-top.jpg">

The repository also contains Gerber files for a smaller miniL1105 board:

<img width="100%" align="left" src="miniL1105-render.jpg">

There is also an example starter project, which can be built with Keil or GCC.

# Note
The Rev 1 easyL1105 and miniL1105 boards have one known bug; they are missing a 47k pull-up resistor from the *RST pin (pin 6 on the microcontroller) to +3.3V. 

The fix is easy, it is possible to patch a resistor onto the underside of the PCB, using an 0805 sized resistor and a short length of 30 AWG Kynar wire.


