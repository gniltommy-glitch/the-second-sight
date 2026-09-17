#define STM32F411xE
#include "stm32f4xx.h"
#include <string.h>
#include <stdint.h>
#include <stdio.h>

/* ============================================================================
 * MACROS & DEFINITIONS
 * ============================================================================ */
#define BUFFER_SIZE 2048

#define SEN0628_I2C_ADDR      0x52
#define CS43L22_I2C_ADDR      0x94

/* ============================================================================
 * GLOBAL VARIABLES
 * ============================================================================ */
volatile uint8_t audio_buffer[BUFFER_SIZE];
volatile uint16_t head = 0;
volatile uint16_t tail = 0;

/* ============================================================================
 * FUNCTION PROTOTYPES
 * ============================================================================ */
void SystemClock_Config(void);
void GPIO_Init_All(void);
void USART2_Init(void);
void USART2_SendChar(char c);
void USART2_SendString(const char *str);

void I2C1_Init(void);
void I2C1_Write(uint8_t slave_addr, uint8_t reg_addr, uint8_t data);
void CS43L22_Init(void);
void I2S3_Init(void);	

void I2C2_Init(void);
void I2C2_Scan_Bus(void);
int8_t I2C2_Write_Reg16(uint8_t slave_addr, uint16_t reg_addr, uint8_t data);
int8_t I2C2_Read_Reg16(uint8_t slave_addr, uint16_t reg_addr, uint8_t *p_data, uint16_t size);

void Button_Interrupt_Init(void);

/* ============================================================================
 * MAIN APPLICATION ROUTINE
 * ============================================================================ */
int main(void) {
    for (volatile int i = 0; i < 500000; i++);

    SystemClock_Config();
    GPIO_Init_All();
    USART2_Init();
    Button_Interrupt_Init(); /* Khởi tạo ngắt nút nhấn PA0 */

    USART2_SendString("STM32F411_BOOT_OK\r\n");

    I2C1_Init();	
    I2C2_Init();
    CS43L22_Init();
    I2S3_Init();

    USART2_SendString("STM32F411_AUDIO_AND_SEN0628_READY\r\n");
    I2C2_Scan_Bus();

    int16_t sample_to_send = 0;
    uint8_t raw_distance[4] = {0};
    uint16_t distance_p0 = 0;
    char uart_msg[64];

    while (1) {
        /* 1. AUDIO STREAM PROCESSING */
        uint16_t current_head = head;
        uint16_t available = (current_head >= tail) ? (current_head - tail) : (BUFFER_SIZE - tail + current_head);

        if (available >= 2) {
            uint8_t low_byte = audio_buffer[tail];
            tail = (tail + 1) % BUFFER_SIZE;
            uint8_t high_byte = audio_buffer[tail];
            tail = (tail + 1) % BUFFER_SIZE;

            sample_to_send = (int16_t)((high_byte << 8) | low_byte);

            while (!(SPI3->SR & SPI_SR_TXE));
            SPI3->DR = sample_to_send;

            while (!(SPI3->SR & SPI_SR_TXE));
            SPI3->DR = sample_to_send;
        }

        /* 2. TOF SENSOR READING */
        static uint32_t loop_count = 0;
        if (loop_count++ > 400000) {
            loop_count = 0;

            if (I2C2_Read_Reg16(SEN0628_I2C_ADDR, 0x0000, raw_distance, 4) == 0) {
                distance_p0 = (raw_distance[0] << 8) | raw_distance[1];
                snprintf(uart_msg, sizeof(uart_msg), "ToF Point 0 Distance: %d mm\r\n", distance_p0);
                USART2_SendString(uart_msg);
            } else {
                USART2_SendString("SEN0628 Error: Bus NACK / Communication Failed!\r\n");
            }
        }
    }
}

/* ============================================================================
 * SYSTEM CLOCK & PERIPHERAL INITIALIZATION
 * ============================================================================ */
void SystemClock_Config(void) {
    RCC->CR |= RCC_CR_HSION;
    while (!(RCC->CR & RCC_CR_HSIRDY));

    RCC->PLLI2SCFGR = (192 << 6) | (2 << 28);
    RCC->CR |= RCC_CR_PLLI2SON;

    RCC->PLLCFGR = RCC_PLLCFGR_PLLSRC_HSI | (192 << 6) | (4 << 0) | (4 << 24);
    RCC->CR |= RCC_CR_PLLON;
    while (!(RCC->CR & RCC_CR_PLLRDY));

    RCC->CFGR |= RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL);
    while (!(RCC->CR & RCC_CR_PLLI2SRDY));
}

void GPIO_Init_All(void) {
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN | RCC_AHB1ENR_GPIODEN;

    /* DAC Reset: PD4 */
    GPIOD->MODER &= ~(3U << (4 * 2));
    GPIOD->MODER |= (1U << (4 * 2));
    GPIOD->BSRR = GPIO_BSRR_BR4;
    for (volatile int i = 0; i < 50000; i++);
    GPIOD->BSRR = GPIO_BSRR_BS4;

    /* USART2: PA2, PA3 -> AF7 */
    GPIOA->MODER &= ~((3U << (2 * 2)) | (3U << (3 * 2)));
    GPIOA->MODER |= (2U << (2 * 2)) | (2U << (3 * 2));
    GPIOA->AFR[0] &= ~((15U << (2 * 4)) | (15U << (3 * 4)));
    GPIOA->AFR[0] |= (7U << (2 * 4)) | (7U << (3 * 4));

    /* I2C1: PB6, PB9 -> AF4 */
    GPIOB->MODER &= ~((3U << (6 * 2)) | (3U << (9 * 2)));
    GPIOB->MODER |= (2U << (6 * 2)) | (2U << (9 * 2));
    GPIOB->OTYPER |= GPIO_OTYPER_OT_6 | GPIO_OTYPER_OT_9;
    GPIOB->AFR[0] &= ~(15U << (6 * 4));
    GPIOB->AFR[0] |= (4U << (6 * 4));
    GPIOB->AFR[1] &= ~(15U << ((9 - 8) * 4));
    GPIOB->AFR[1] |= (4U << ((9 - 8) * 4));

    /* I2C2: PB10, PB3 -> AF4 / AF9 */
    GPIOB->MODER &= ~((3U << (10 * 2)) | (3U << (3 * 2)));
    GPIOB->MODER |= (2U << (10 * 2)) | (2U << (3 * 2));
    GPIOB->OTYPER |= GPIO_OTYPER_OT_10 | GPIO_OTYPER_OT_3;
    GPIOB->PUPDR &= ~((3U << (10 * 2)) | (3U << (3 * 2)));
    GPIOB->PUPDR |= (1U << (10 * 2)) | (1U << (3 * 2));
    GPIOB->AFR[1] &= ~(15U << ((10 - 8) * 4));
    GPIOB->AFR[1] |= (4U << ((10 - 8) * 4));
    GPIOB->AFR[0] &= ~(15U << (3 * 4));
    GPIOB->AFR[0] |= (9U << (3 * 4));

    /* I2S3: PA4, PC7, PC10, PC12 -> AF6 */
    GPIOA->MODER &= ~(3U << (4 * 2));
    GPIOA->MODER |= (2U << (4 * 2));
    GPIOA->AFR[0] &= ~(15U << (4 * 4));
    GPIOA->AFR[0] |= (6U << (4 * 4));

    GPIOC->MODER &= ~((3U << (7 * 2)) | (3U << (10 * 2)) | (3U << (12 * 2)));
    GPIOC->MODER |= (2U << (7 * 2)) | (2U << (10 * 2)) | (2U << (12 * 2));
    GPIOC->AFR[0] &= ~(15U << (7 * 4));
    GPIOC->AFR[0] |= (6U << (7 * 4));
    GPIOC->AFR[1] &= ~((15U << ((10 - 8) * 4)) | (15U << ((12 - 8) * 4)));
    GPIOC->AFR[1] |= (6U << ((10 - 8) * 4)) | (6U << ((12 - 8) * 4));

    /* LED PD12 Output */
    GPIOD->MODER &= ~(3U << (12 * 2));
    GPIOD->MODER |= (1U << (12 * 2));
}

/* ============================================================================
 * UART FUNCTIONS
 * ============================================================================ */
void USART2_Init(void) {
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;
    USART2->BRR = 52;
    USART2->CR1 |= USART_CR1_TE | USART_CR1_RE | USART_CR1_UE | USART_CR1_RXNEIE;

    NVIC_SetPriority(USART2_IRQn, 1);
    NVIC_EnableIRQ(USART2_IRQn);
}

void USART2_IRQHandler(void) {
    if (USART2->SR & USART_SR_RXNE) {
        uint8_t received_byte = (uint8_t)USART2->DR;
        uint16_t next_head = (head + 1) % BUFFER_SIZE;
        if (next_head != tail) {
            audio_buffer[head] = received_byte;
            head = next_head;
        }
    }
}

void USART2_SendChar(char c) {
    while (!(USART2->SR & USART_SR_TXE));
    USART2->DR = c;
}

void USART2_SendString(const char *str) {
    while (*str) USART2_SendChar(*str++);
}

/* ============================================================================
 * I2C1 & AUDIO CODEC (CS43L22) FUNCTIONS
 * ============================================================================ */
void I2C1_Init(void) {
    RCC->APB1ENR |= RCC_APB1ENR_I2C1EN;
    I2C1->CR1 |= I2C_CR1_SWRST;
    I2C1->CR1 &= ~I2C_CR1_SWRST;

    I2C1->CR2 = 42;
    I2C1->CCR = 210;
    I2C1->TRISE = 43;
    I2C1->CR1 |= I2C_CR1_PE;
}

void I2C1_Write(uint8_t slave_addr, uint8_t reg_addr, uint8_t data) {
    I2C1->CR1 |= I2C_CR1_START;
    while (!(I2C1->SR1 & I2C_SR1_SB));

    I2C1->DR = slave_addr;
    while (!(I2C1->SR1 & I2C_SR1_ADDR));
    (void)I2C1->SR2;

    I2C1->DR = reg_addr;
    while (!(I2C1->SR1 & I2C_SR1_TXE));

    I2C1->DR = data;
    while (!(I2C1->SR1 & I2C_SR1_BTF));

    I2C1->CR1 |= I2C_CR1_STOP;
}

void CS43L22_Init(void) {
    I2C1_Write(CS43L22_I2C_ADDR, 0x01, 0x01);
    I2C1_Write(CS43L22_I2C_ADDR, 0x02, 0x9E);
    I2C1_Write(CS43L22_I2C_ADDR, 0x04, 0xAF);
    I2C1_Write(CS43L22_I2C_ADDR, 0x05, 0x81);
    I2C1_Write(CS43L22_I2C_ADDR, 0x22, 0x00);
    I2C1_Write(CS43L22_I2C_ADDR, 0x23, 0x00);
    I2C1_Write(CS43L22_I2C_ADDR, 0x01, 0x02);
}

void I2S3_Init(void) {
    RCC->APB1ENR |= RCC_APB1ENR_SPI3EN;
    SPI3->I2SCFGR = SPI_I2SCFGR_I2SMOD | (2U << SPI_I2SCFGR_I2SCFG_Pos);
    SPI3->I2SPR = 2 | SPI_I2SPR_MCKOE;
    SPI3->I2SCFGR |= SPI_I2SCFGR_I2SE;
}

/* ============================================================================
 * I2C2 DRIVER FUNCTIONS FOR SEN0628
 * ============================================================================ */
void I2C2_Init(void) {
    RCC->APB1ENR |= RCC_APB1ENR_I2C2EN;
    I2C2->CR1 |= I2C_CR1_SWRST;
    I2C2->CR1 &= ~I2C_CR1_SWRST;

    I2C2->CR2 = 42;
    I2C2->CCR = 210;
    I2C2->TRISE = 43;
    I2C2->CR1 |= I2C_CR1_PE;
}

void I2C2_Scan_Bus(void) {
    char msg[64];
    USART2_SendString("\r\n--- Scanning I2C2 Bus ---\r\n");
    for (uint8_t addr = 1; addr < 128; addr++) {
        I2C2->CR1 |= I2C_CR1_START;
        uint32_t timeout = 2000;
        while (!(I2C2->SR1 & I2C_SR1_SB) && --timeout);

        I2C2->DR = (addr << 1);
        timeout = 2000;
        while (!(I2C2->SR1 & I2C_SR1_ADDR) && --timeout);

        if (timeout > 0) {
            (void)I2C2->SR1;
            (void)I2C2->SR2;
            I2C2->CR1 |= I2C_CR1_STOP;
            snprintf(msg, sizeof(msg), "Found Device at 7-bit: 0x%02X (8-bit: 0x%02X)\r\n", addr, addr << 1);
            USART2_SendString(msg);
        } else {
            I2C2->CR1 |= I2C_CR1_STOP;
            I2C2->CR1 |= I2C_CR1_SWRST;
            I2C2->CR1 &= ~I2C_CR1_SWRST;
            I2C2->CR1 |= I2C_CR1_PE;
        }
        for (volatile int i = 0; i < 1000; i++);
    }
    USART2_SendString("--- Scan Complete ---\r\n\r\n");
}

int8_t I2C2_Write_Reg16(uint8_t slave_addr, uint16_t reg_addr, uint8_t data) {
    volatile uint32_t timeout = 10000;

    I2C2->CR1 |= I2C_CR1_START;
    while (!(I2C2->SR1 & I2C_SR1_SB)) { if(--timeout == 0) return -1; }

    I2C2->DR = slave_addr & 0xFE;
    timeout = 10000;
    while (!(I2C2->SR1 & I2C_SR1_ADDR)) { if(--timeout == 0) return -1; }
    (void)I2C2->SR2;

    I2C2->DR = (uint8_t)(reg_addr >> 8);
    timeout = 10000;
    while (!(I2C2->SR1 & I2C_SR1_TXE)) { if(--timeout == 0) return -1; }

    I2C2->DR = (uint8_t)(reg_addr & 0xFF);
    timeout = 10000;
    while (!(I2C2->SR1 & I2C_SR1_TXE)) { if(--timeout == 0) return -1; }

    I2C2->DR = data;
    timeout = 10000;
    while (!(I2C2->SR1 & I2C_SR1_BTF)) { if(--timeout == 0) return -1; }

    I2C2->CR1 |= I2C_CR1_STOP;
    return 0;
}

int8_t I2C2_Read_Reg16(uint8_t slave_addr, uint16_t reg_addr, uint8_t *p_data, uint16_t size) {
    volatile uint32_t timeout = 10000;

    I2C2->CR1 |= I2C_CR1_START;
    while (!(I2C2->SR1 & I2C_SR1_SB)) { if(--timeout == 0) return -1; }

    I2C2->DR = slave_addr & 0xFE;
    timeout = 10000;
    while (!(I2C2->SR1 & I2C_SR1_ADDR)) { if(--timeout == 0) return -1; }
    (void)I2C2->SR2;

    I2C2->DR = (uint8_t)(reg_addr >> 8);
    timeout = 10000;
    while (!(I2C2->SR1 & I2C_SR1_TXE)) { if(--timeout == 0) return -1; }

    I2C2->DR = (uint8_t)(reg_addr & 0xFF);
    timeout = 10000;
    while (!(I2C2->SR1 & I2C_SR1_TXE)) { if(--timeout == 0) return -1; }

    I2C2->CR1 |= I2C_CR1_START;
    timeout = 10000;
    while (!(I2C2->SR1 & I2C_SR1_SB)) { if(--timeout == 0) return -1; }

    I2C2->DR = slave_addr | 0x01;
    timeout = 10000;
    while (!(I2C2->SR1 & I2C_SR1_ADDR)) { if(--timeout == 0) return -1; }
    (void)I2C2->SR2;

    I2C2->CR1 |= I2C_CR1_ACK;
    for (uint16_t i = 0; i < size; i++) {
        if (i == size - 1) {
            I2C2->CR1 &= ~I2C_CR1_ACK;
            I2C2->CR1 |= I2C_CR1_STOP;
        }
        timeout = 10000;
        while (!(I2C2->SR1 & I2C_SR1_RXNE)) { if(--timeout == 0) return -1; }
        p_data[i] = I2C2->DR;
    }
    return 0;
}

/* ============================================================================
 * EXTI & GPIO INTERRUPT CONFIGURATION FOR BUTTON (PA0)
 * ============================================================================ */
void Button_Interrupt_Init(void) {
    RCC->APB2ENR |= RCC_APB2ENR_SYSCFGEN;

    GPIOA->MODER &= ~(3U << (0 * 2));
    GPIOA->PUPDR &= ~(3U << (0 * 2));

    SYSCFG->EXTICR[0] &= ~(0x0F << 0);

    EXTI->IMR |= (1U << 0);
    EXTI->RTSR |= (1U << 0);

    NVIC_SetPriority(EXTI0_IRQn, 2);
    NVIC_EnableIRQ(EXTI0_IRQn);
}

void EXTI0_IRQHandler(void) {
    if (EXTI->PR & (1U << 0)) {
        EXTI->PR |= (1U << 0);

        GPIOD->ODR ^= (1U << 12);

        USART2_SendString("Button Pressed! LED Toggled.\r\n");
    }
}
