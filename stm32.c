#define STM32F411xE
#include "stm32f4xx.h"
#include <string.h>
#include <stdint.h>
#include <stdio.h>

#define BUFFER_SIZE 2048
volatile uint8_t audio_buffer[BUFFER_SIZE];
volatile uint16_t head = 0;
volatile uint16_t tail = 0;

uint32_t ram_vector_table[102] __attribute__((aligned(512)));

// Địa chỉ I2C dạng 8-bit chính thức của DFRobot SEN0628 (0x33 << 1)
#define SEN0628_I2C_ADDR   0x66

// Nguyên mẫu hàm
void SystemClock_Config(void);
void GPIO_Init_All(void);
void I2C1_Init(void);
void I2C1_Write(uint8_t slave_addr, uint8_t reg_addr, uint8_t data);
void CS43L22_Init(void);
void I2S3_Init(void);
void USART2_Init(void);
void USART2_SendChar(char c);
void USART2_SendString(const char *str);
void Custom_UART_Handler(void);

// Các hàm cho I2C2 giao tiếp với module SEN0628 (Địa chỉ thanh ghi 8-bit)
void I2C2_Init(void);
int8_t I2C2_Read_Reg(uint8_t slave_addr, uint8_t reg_addr, uint8_t *p_data, uint16_t size);

int main(void) {
    // 1. Hệ thống & Vector Table trên RAM
    SystemClock_Config();
    memcpy(ram_vector_table, (uint32_t*)(0x08000000), sizeof(ram_vector_table));
    SCB->VTOR = (uint32_t)ram_vector_table;
    ram_vector_table[(16 + USART2_IRQn)] = (uint32_t)(uintptr_t)Custom_UART_Handler | 1;

    // 2. Khởi tạo ngoại vi
    GPIO_Init_All();
    I2C1_Init();      // Cho Audio DAC
    I2C2_Init();      // Cho ToF Sensor

    CS43L22_Init();   // Bật chip âm thanh
    I2S3_Init();
    USART2_Init();    // Bật UART nhận nhạc từ PC

    USART2_SendString("STM32F411_AUDIO_AND_SEN0628_READY\n");

    int16_t sample_to_send = 0;

    // Mảng nhận dữ liệu khoảng cách thô (Mỗi điểm chiếm 2 byte, đọc thử 2 điểm đầu tiên = 4 byte)
    uint8_t raw_distance[4] = {0};
    uint16_t distance_p0 = 0;
    char uart_msg[64];

    while (1) {
        // --- 🟩 LUỒNG AUDIO (ƯU TIÊN TUYỆT ĐỐI) ---
        uint16_t current_head = head;
        uint16_t available = (current_head >= tail) ? (current_head - tail) : (BUFFER_SIZE - tail + current_head);

        if (available >= 2) {
            uint8_t low_byte = audio_buffer[tail];
            tail = (tail + 1) % BUFFER_SIZE;
            uint8_t high_byte = audio_buffer[tail];
            tail = (tail + 1) % BUFFER_SIZE;

            sample_to_send = (int16_t)((high_byte << 8) | low_byte);

            while (!(SPI3->SR & SPI_SR_TXE));
            SPI3->DR = sample_to_send; // Kênh trái

            while (!(SPI3->SR & SPI_SR_TXE));
            SPI3->DR = sample_to_send; // Kênh phải
        }

        // --- 🟨 LUỒNG TOF SENSOR (CHẠY ĐỊNH KỲ KHI RẢNH) ---
        static uint32_t loop_count = 0;
        if (loop_count++ > 400000) {
            loop_count = 0;

            // Đọc từ thanh ghi dữ liệu khoảng cách đầu tiên (0x00) của SEN0628
            if(I2C2_Read_Reg(SEN0628_I2C_ADDR, 0x00, raw_distance, 4) == 0) {
                // Ghép 2 byte (High trước, Low sau) để ra giá trị khoảng cách mm của điểm số 0
                distance_p0 = (raw_distance[0] << 8) | raw_distance[1];

                // Gửi dữ liệu đo được lên cổng UART Terminal trên PC
                sprintf(uart_msg, "ToF Point 0 Distance: %d mm\n", distance_p0);
                USART2_SendString(uart_msg);
            } else {
                USART2_SendString("SEN0628 Error: Check wires or Address Switch!\n");
            }
        }
    }
}

// Cấu hình hệ thống chạy ở tần số 96 MHz từ thạch anh nội HSI
void SystemClock_Config(void) {
    RCC->CR |= RCC_CR_HSION;
    while (!(RCC->CR & RCC_CR_HSIRDY));

    // Bộ chia PLLI2S cho âm thanh (Đầu ra ~48kHz)
    RCC->PLLI2SCFGR = (192 << 6) | (2 << 28);
    RCC->CR |= RCC_CR_PLLI2SON;

    // Cấu hình SYSCLK lên 96MHz cho CPU xử lý kịp UART tốc độ cao
    RCC->PLLCFGR = RCC_PLLCFGR_PLLSRC_HSI | (192 << 6) | (4 << 0) | (4 << 24);
    RCC->CR |= RCC_CR_PLLON;
    while (!(RCC->CR & RCC_CR_PLLRDY));

    RCC->CFGR |= RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL);
    while (!(RCC->CR & RCC_CR_PLLI2SRDY));
}

void GPIO_Init_All(void) {
    // 1. Bật Clock cho các Port GPIO A, B, C, D
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN | RCC_AHB1ENR_GPIODEN;

    // 2. Chân RESET của CS43L22 (PD4) -> Output
    GPIOD->MODER &= ~(3U << (4 * 2));
    GPIOD->MODER |= (1U << (4 * 2));
    GPIOD->BSRR = GPIO_BSRR_BR4;
    for (volatile int i = 0; i < 50000; i++);
    GPIOD->BSRR = GPIO_BSRR_BS4;

    // 3. Chân UART2: PA2 (TX), PA3 (RX) -> Alternate Function 7
    GPIOA->MODER &= ~((3U << (2 * 2)) | (3U << (3 * 2)));
    GPIOA->MODER |= (2U << (2 * 2)) | (2U << (3 * 2));
    GPIOA->AFR[0] &= ~((15U << (2 * 4)) | (15U << (3 * 4)));
    GPIOA->AFR[0] |= (7U << (2 * 4)) | (7U << (3 * 4));

    // 4. Chân I2C1 (Cho Audio): PB6 (SCL), PB9 (SDA) -> Alternate Function 4
    GPIOB->MODER &= ~((3U << (6 * 2)) | (3U << (9 * 2)));
    GPIOB->MODER |= (2U << (6 * 2)) | (2U << (9 * 2));
    GPIOB->OTYPER |= GPIO_OTYPER_OT_6 | GPIO_OTYPER_OT_9;
    GPIOB->AFR[0] &= ~(15U << (6 * 4));
    GPIOB->AFR[0] |= (4U << (6 * 4));
    GPIOB->AFR[1] &= ~(15U << ((9 - 8) * 4));
    GPIOB->AFR[1] |= (4U << ((9 - 8) * 4));

    // CHÂN I2C2 (ĐÃ ĐỔI: PB10 làm SCL, PB3 làm SDA) -> Alternate Function 4
    GPIOB->MODER &= ~((3U << (10 * 2)) | (3U << (3 * 2)));
    GPIOB->MODER |= (2U << (10 * 2)) | (2U << (3 * 2));
    GPIOB->OTYPER |= GPIO_OTYPER_OT_10 | GPIO_OTYPER_OT_3;
    GPIOB->PUPDR &= ~((3U << (10 * 2)) | (3U << (3 * 2)));
    GPIOB->PUPDR |= (1U << (10 * 2)) | (1U << (3 * 2));

    GPIOB->AFR[1] &= ~(15U << ((10 - 8) * 4));
    GPIOB->AFR[1] |= (4U << ((10 - 8) * 4));
    GPIOB->AFR[0] &= ~(15U << (3 * 4));
    GPIOB->AFR[0] |= (4U << (3 * 4));

    // 6. Chân I2S3: PA4 (WS), PC7 (MCLK), PC10 (CLK), PC12 (SD) -> AF6
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
}

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
    I2C1_Write(0x94, 0x01, 0x01);
    I2C1_Write(0x94, 0x02, 0x9E);
    I2C1_Write(0x94, 0x04, 0xAF);
    I2C1_Write(0x94, 0x05, 0x81);
    I2C1_Write(0x94, 0x22, 0x00);
    I2C1_Write(0x94, 0x23, 0x00);
    I2C1_Write(0x94, 0x01, 0x02);
}

void I2S3_Init(void) {
    RCC->APB1ENR |= RCC_APB1ENR_SPI3EN;
    SPI3->I2SCFGR = SPI_I2SCFGR_I2SMOD | (2U << SPI_I2SCFGR_I2SCFG_Pos);
    SPI3->I2SPR = 2 | SPI_I2SPR_MCKOE;
    SPI3->I2SCFGR |= SPI_I2SCFGR_I2SE;
}

void USART2_Init(void) {
    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;
    USART2->BRR = 52;
    USART2->CR1 |= USART_CR1_TE | USART_CR1_RE | USART_CR1_UE | USART_CR1_RXNEIE;

    NVIC_SetPriority(USART2_IRQn, 1);
    NVIC_EnableIRQ(USART2_IRQn);
}

void Custom_UART_Handler(void) {
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

// ==================== CẤU HÌNH NGOẠI VI I2C2 ====================
void I2C2_Init(void) {
    RCC->APB1ENR |= RCC_APB1ENR_I2C2EN;
    I2C2->CR1 |= I2C_CR1_SWRST;
    I2C2->CR1 &= ~I2C_CR1_SWRST;

    I2C2->CR2 = 42;
    I2C2->CCR = 210;
    I2C2->TRISE = 43;
    I2C2->CR1 |= I2C_CR1_PE;
}

// Hàm đọc thanh ghi I2C 8-bit chuẩn hóa cho module thông minh DFRobot
int8_t I2C2_Read_Reg(uint8_t slave_addr, uint8_t reg_addr, uint8_t *p_data, uint16_t size) {
    volatile uint32_t timeout = 10000;

    I2C2->CR1 |= I2C_CR1_START;
    while (!(I2C2->SR1 & I2C_SR1_SB)) { if(--timeout == 0) return -1; }

    I2C2->DR = slave_addr & 0xFE;
    timeout = 10000;
    while (!(I2C2->SR1 & I2C_SR1_ADDR)) { if(--timeout == 0) return -1; }
    (void)I2C2->SR2;

    I2C2->DR = reg_addr;
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
