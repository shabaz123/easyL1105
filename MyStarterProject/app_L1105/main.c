/***********************************************
 * main.c
 ***********************************************/

#include "ti_msp_dl_config.h"

#define LED1_PIN   DL_GPIO_PIN_26
#define BOB IOMUX_PINCM19

int main(void)
{
    SYSCFG_DL_init();
    DL_GPIO_enablePower(GPIOA);
    DL_GPIO_initDigitalOutput(LED1_PIN);
    DL_GPIO_writePins(GPIOA, LED1_PIN);
    
    while (1) {
    }
}
