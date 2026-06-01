# Defaults to a Debug build
# For Release building, user can specify DEBUG=0 when issuing the make command
DEBUG ?= 1

ifeq ($(DEBUG),1)
    OPTFLAGS = -Og -g3 -gstrict-dwarf
    BUILD_TYPE = Debug
else
    OPTFLAGS = -O2 -g -gstrict-dwarf
    BUILD_TYPE = Release
endif

ifeq ("$(SHELL)","sh.exe")
# for Windows/DOS shell
    RM     = del
    RMDIR  = -rmdir /S /Q
    DEVNULL = NUL
    ECHOBLANKLINE = @cmd /c echo.
else
# for Linux-like shells
    RM     = rm -f
    RMDIR  = rm -rf
    DEVNULL = /dev/null
    ECHOBLANKLINE = echo
endif

CC = "$(GCC_ARMCOMPILER_MSP)/bin/arm-none-eabi-gcc"
LNK = "$(GCC_ARMCOMPILER_MSP)/bin/arm-none-eabi-gcc"

OBJECTS = app_L1105.obj ti_msp_dl_config.obj gcc_startup_mspm0l1105_gcc.obj
NAME = app_L1105

OBJCOPY := "$(GCC_ARMCOMPILER_MSP)/bin/arm-none-eabi-objcopy"
ELF     := $(NAME).out
HEX     := $(ELF:.out=.hex)

CFLAGS = -I.. \
    -I. \
    -D__MSPM0L1105__ \
    $(OPTFLAGS) \
    "-I$(MSPM0_SDK_INSTALL_DIR)/source/third_party/CMSIS/Core/Include" \
    "-I$(MSPM0_SDK_INSTALL_DIR)/source" \
    -mcpu=cortex-m0plus \
    -march=armv6-m \
    -mthumb \
    -std=c99 \
    -mfloat-abi=soft \
    -ffunction-sections \
    -fdata-sections \
    -Wall \
    "-I$(GCC_ARMCOMPILER_MSP)/arm-none-eabi/include/newlib-nano" \
    "-I$(GCC_ARMCOMPILER_MSP)/arm-none-eabi/include"

LFLAGS = "-L$(MSPM0_SDK_INSTALL_DIR)/source/ti/driverlib/lib/gcc/m0p/mspm0l11xx_l13xx" \
    -nostartfiles \
    -Wl,-T,$(MSPM0_SDK_INSTALL_DIR)/source/ti/devices/msp/m0p/linker_files/gcc/mspm0l1105.lds \
    "-Wl,-Map,$(NAME).map" \
    "-l:driverlib.a" \
    -march=armv6-m \
    -mthumb \
    -static \
    -Wl,--gc-sections \
    "-L$(GCC_ARMCOMPILER_MSP)/arm-none-eabi/lib/thumb/v6-m/nofp" \
    -lgcc \
    -lc \
    -lm \
    -lnosys \
    --specs=nano.specs
all: banner $(ELF) $(HEX)

banner:
	@ echo ==========================================
	@ echo Building $(BUILD_TYPE) version
	@ echo ==========================================

app_L1105.obj: ../main.c $(SYSCFG_H_FILES)
	@ echo Building $@
	@ $(CC) $(CFLAGS) $< -c -o $@

ti_msp_dl_config.obj: ../ti_msp_dl_config.c
	@ echo Building $@
	@ $(CC) $(CFLAGS) $< -c -o $@

gcc_startup_mspm0l1105_gcc.obj: $(MSPM0_SDK_INSTALL_DIR)/source/ti/devices/msp/m0p/startup_system_files/gcc/startup_mspm0l110x_gcc.c
	@ echo Building $@
	@ $(CC) $(CFLAGS) $< -c -o $@

$(NAME).out: $(OBJECTS)
	@ echo linking $@
	@ $(LNK)  $(OBJECTS)  $(LFLAGS) -o $(NAME).out

$(HEX): $(ELF)
	$(OBJCOPY) -O ihex --gap-fill 0xFF $< $@

clean:
	@ echo Cleaning...
	@ $(RM) $(OBJECTS) > $(DEVNULL) 2>&1
	@ $(RM) $(NAME).out > $(DEVNULL) 2>&1
	@ $(RM) $(NAME).map > $(DEVNULL) 2>&1
	@ $(RM) $(NAME).hex > $(DEVNULL) 2>&1

