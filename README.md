# easyL1105
MSPM0L1105 Microcontroller

# Note
The Rev 1 easyL1105 and miniL1105 boards have one known bug; they are missing a 47k pull-up resistor from the *RST pin (pin 6 on the microcontroller) to +3.3V. 

The fix is easy, it is possible to patch a resistor onto the underside of the PCB, using an 0805 sized resistor and a short length of 30 AWG Kynar wire.


