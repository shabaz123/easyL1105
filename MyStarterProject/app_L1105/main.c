/***********************************************
 * main.c
 * rev 1 - shabaz - August 2025
 * TI DriverLib documentation:
 * https://software-dl.ti.com/msp430/esd/MSPM0-SDK/latest/docs/english/driverlib/mspm0l11xx_l13xx_api_guide/html/index.html
 ***********************************************/

#include <stdint.h>
#include "ti_msp_dl_config.h"

// output pins
#define GREEN_LED 27
#define BLUE_LED 26
#define BIT(n) (1U << (n))

// sets a GPIO pin as output. Use pin_num from 0 to 27, for PA0-PA27
void set_output(uint32_t pin_num) {
    DL_GPIO_initDigitalOutput(pin_num);
    DL_GPIO_clearPins(GPIOA, BIT(pin_num));
    DL_GPIO_enableOutput(GPIOA, BIT(pin_num));
}

// delay function, chews up CPU time
void delay_ms(uint32_t ms)
{
    for (uint32_t j = 0; j < ms; j++) {
        // might need the value adjusting
        for (volatile uint32_t i = 0; i < 2000; i++) {
            __asm volatile ("nop");
        }
    }
}

/********************************
 * main function
 ********************************/
int main(void)
{
    // power up GPIO feature
    DL_GPIO_reset(GPIOA);
    DL_GPIO_enablePower(GPIOA);
    delay_cycles(16);
    
    // set LED pins as outputs
    set_output(GREEN_LED);
    set_output(BLUE_LED);
    
    // forever loop
    while (1) {
        DL_GPIO_writePins(GPIOA, BIT(GREEN_LED));
        DL_GPIO_clearPins(GPIOA, BIT(BLUE_LED));
        delay_ms(200);
        DL_GPIO_writePins(GPIOA, BIT(BLUE_LED));
        DL_GPIO_clearPins(GPIOA, BIT(GREEN_LED));
        delay_ms(200);
    }
    
    return(0); // warning on this line is OK
}
