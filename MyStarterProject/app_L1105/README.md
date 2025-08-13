## Summary

MSPM0L1105 starter project with TI DriverLib support.
[DriverLib](https://software-dl.ti.com/msp430/esd/MSPM0-SDK/latest/docs/english/driverlib/mspm0l11xx_l13xx_api_guide/html/modules.html) is a simple low-level API for accessing microcontroller integrated peripherals.

The starter project is called **app_L1105**. If (say) your project is about motor control, you could create a **motor_control** folder, and then drop the app_L1105 folder within it, and simply accept that the app_L1105 name will remain. If you don't want that, you'll have to rename the **app_L1105.uvprojx** file, and also edit the contents of that file.


### Low-Power Recommendations
TI recommends to terminate unused pins by setting the corresponding functions to
GPIO and configure the pins to output low or input with internal
pullup/pulldown resistor.


## Setting up your PC
Download and install the [TI MSPM0 SDK](https://www.ti.com/tool/MSPM0-SDK) in folder **C:\ti** if you're using Windows (if you use a different folder, then you'll need to edit the app_L1105.uvprojx file). It will install itself into a sub-folder, called something like mspm0_sdk_2_05_01_00. 

Next, follow the setting-up steps below depending on whether you're using Keil uVision or GCC.

### Setting up to use Keil
**NOTE:** If the TI MSPM0 SDK folder is *not* precisely called **mspm0_sdk_2_05_01_00** then you'll need to edit the **app_L1105.uvprojx** file (search-and-replace all instances of mspm0_sdk_2_05_01_00 and replace with what you've got).

Download and install [Keil uVision](https://www.keil.arm.com/mdk-community/) (community edition). 

Next, launch uVision and go to **Project->Manage->PackInstaller**, wait for it to refresh over the Internet, and then in the left pane, navigate to **TexasInstruments->MSPM0Series->MSPM0L110X->MSPM0L1105** and click on the name, and then notice that in the right pane, at the top, in the **Pack** column, in the **Device Specific** section, the first entry will be **TexasInstruments:MSPM0L11XX_L13XX_DFP**. In the **Action** column, click on **Install**.

### Setting up to use ARM GCC
The instructions here are for Windows, but in theory with some tweaks, it should be possible to use Linux and Mac too.

Download and install the [ARM GNU Toolchain](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads). If you're using Windows, then file you need is called something like **arm-gnu-toolchain-13.3.rel1-mingw-w64-i686-arm-none-eabi.zip** but use the latest. You may already have it installed on your PC if you've worked with other microcontrollers (such as Pi Pico RP2040). It will have installed into a folder, such as **C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\13.3 rel1**

Open up your Windows Environment Variables, and create two user variables as follows:

```GCC_ARMCOMPILER_MSP   C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\13.3 rel1```


```MSPM0_SDK_INSTALL_DIR     C:\ti\mspm0_sdk_2_05_01_00```

You will need to set those variables with your actual paths.

Next, examine your PATH user variable, and see if there's any location suitable for placing a utility application. In my case, I had a folder called c:\users\myusername\.local\bin but if you cannot find anything suitable, then create a path called (say) c:\tools\bin

Go to [EZWinPorts](https://sourceforge.net/projects/ezwinports/files/) and download the file called **make-4.4.1-without-guile-w32-bin.zip** (the number 4.4.1 may be different, that's OK). Open the zip file, and locate **make.exe** (it will be in the **bin** subfolder in the zip file) and place it in your tools folder (**c:\tools\bin** or anywhere else provided it is a folder in your PATH variable).

## Building with Keil uVision
Launch uVision, and then go to **Project->OpenProject** and then navigate to the **app_L1105** folder, and within that, open the **Keil** folder, and select **app_L1105.uvprojx** in that folder.

The project will open, and you'll see the source code files in the left side Project pane. For instance, the **main.c** file can be accessed by expanding **app_L1105->Source** in that project files tree.

To build the code, click on **Project->BuildTarget**.

## Building with ARM GCC
Launch **PowerShell**, and then navigate to the **app_L1105** folder, and within that, go to the **GCC** folder. You'll see a **makefile** in there. Simply type: **make** and the code should build. To remove the outputs, type: **make clean**

## Modifying Stack Allocations
If you're using Keil, open the startup_mspm0l110x_uvision.s file, and there will be an entry there similar to:

Stack_Size      EQU     0x00000400

0x400 (hex) is 1024 bytes in decimal. Change the 0x00000400 size as desired, but you'll also need to change instances of 0x00000400 in mspm0l1105.sct too. The stack is configured in mspm0l1105.sct to start at 0x20001000 (top of 4kBytes of RAM) and grow down to 0x20000c00 (i.e. 1024 bytes size).

Note that statically allocated variables start at the bottom of the RAM, followed by Heap (used for malloc'd space) and this grows up, and it could possibly hit the stack.

If you're using GCC, the current linker file (C:\ti\mspm0_sdk_2_05_01_00\source\ti\devices\msp\m0p\linker_files\gcc\mspm0l1105.lds) doesn't specify a limit to the stack size; it merely starts at 0x20001000 (top of 4kBytes of RAM) and grows down as much as required.

