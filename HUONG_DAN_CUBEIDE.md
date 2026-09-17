# Nạp và debug bằng STM32CubeIDE 1.19

## Ý chính

Bạn thường chỉ sửa `main.c`, nhưng IDE còn biên dịch thư viện HAL/CMSIS và ghép startup, vector ngắt, linker script thành một chương trình hoàn chỉnh. Bấm Debug sẽ nạp ELF mà IDE vừa build; không cần thao tác HEX thủ công.

Bộ này dành cho **STM32F411E-DISCO/MB1115, chip STM32F411VET6, HSE 8 MHz, CS43L22 và jack có sẵn**. Chưa kiểm thử board thực tế.

Đã chuẩn bị thư mục `cubeide_import` gồm:

- `Src/main.c`: bản sao đầy đủ của `firmware/app.c`.
- `Src/*.c`: HAL và SystemInit cần thiết.
- `Inc`: header HAL/CMSIS và HAL config.
- `licenses`: giấy phép thư viện.

Đây là bộ file để **chép vào project mới**, không phải project Eclipse hoàn chỉnh để Import Existing Projects.

## Bước 1 — Tạo project mới

1. Mở STM32CubeIDE.
2. `File > New > STM32 Project`.
3. Tab **MCU/MPU Selector**, tìm `STM32F411VET6`, chọn đúng chip rồi **Next**.
4. Project Name: `F411_TOF_AUDIO`.
5. Targeted Language: **C**.
6. Targeted Binary Type: **Executable**.
7. Targeted Project Type: **Empty**.
8. **Finish**.

Chọn Empty vì mã đã cấu hình clock, GPIO, I2C, UART và I2S bằng C. Quy trình này không cần tạo `.ioc` hay cấu hình chân lại trong CubeMX.

## Bước 2 — Chép file đã chuẩn bị

1. Trong Project Explorer, chuột phải project mới > **Properties > Resource**, xem **Location** để biết thư mục thật trên Windows.
2. Mở File Explorer tới `C:\Users\Nguyen\Downloads\smt32_sum\cubeide_import`.
3. Chép các thư mục **Inc**, **Src**, **licenses** vào ngay thư mục gốc của project `F411_TOF_AUDIO`.
4. Gộp thư mục nếu Windows hỏi; thay `Src/main.c` của project mới bằng bản đã chuẩn bị. Đây là project Empty vừa tạo, không phải project cũ của bạn.
5. Quay lại IDE, chọn project rồi **F5/Refresh**.

Cấu trúc cần thấy:

```text
F411_TOF_AUDIO/
  Inc/
    stm32f4xx_hal_conf.h
    stm32f4xx_hal.h
    stm32f4xx.h
    stm32f411xe.h
    core_cm4.h
    ...
    Legacy/
  Src/
    main.c
    system_stm32f4xx.c
    stm32f4xx_hal.c
    stm32f4xx_hal_i2c.c
    ...
    syscalls.c                 <- giữ nếu IDE đã tạo
    sysmem.c                   <- giữ nếu IDE đã tạo
  Startup/
    startup_stm32f411vetx.s    <- tên có thể khác; phải đúng F411VE
  STM32F411VETX_FLASH.ld       <- giữ file IDE tạo
  ...
```

Vị trí startup có thể khác tùy phiên bản. Giữ startup và linker của project Empty đúng MCU. Không chép thêm `#define STM32F411xE.c`, `firmware/app.c`, `firmware/runtime.c`, startup thứ hai, hay toàn bộ `vendor` vào project này. `Src/main.c` đã chứa cả main và IRQ handler; thêm bản khác sẽ trùng symbol.

Nếu tạo nhầm project có `Core/Src`, `.ioc`, `stm32f4xx_it.c`, nên tạo lại project **Empty** theo hướng dẫn để tránh phải xử lý hai bộ mã khởi tạo.

## Bước 3 — Thêm đường dẫn header

Chuột phải project > **Properties > C/C++ Build > Settings**.

Chọn Configuration **All configurations** nếu có, rồi vào:

`Tool Settings > MCU GCC Compiler > Include paths`

Đảm bảo có:

```text
../Inc
```

Hoặc dùng nút **Workspace...** chọn `F411_TOF_AUDIO/Inc`. Nếu đường dẫn Inc đã có thì không thêm trùng. Cấu trúc thư mục Legacy được giữ nguyên nên không cần tự gom từng header.

## Bước 4 — Định nghĩa chip và HAL

Trong cùng cửa sổ, vào:

`MCU GCC Compiler > Preprocessor > Defined symbols (-D)`

Đảm bảo có:

```text
STM32F411xE
USE_HAL_DRIVER
HSE_VALUE=8000000U
```

Không gõ thêm `#define` hoặc `-D` vào ô symbol. `STM32F411xE` thường đã có khi chọn đúng chip. Xóa/sửa giá trị HSE khác nếu có; không để hai HSE_VALUE khác nhau.

Kiểm tra MCU Settings: Cortex-M4, FPU **FPv4-SP-D16**, floating-point ABI **hard**. Chọn đúng MCU từ đầu thường đã thiết lập các mục này.

Trong `MCU GCC Compiler > Optimization`, chọn **Optimize for size (-Os)** để khớp bản nguồn đã kiểm tra. Debug vẫn có thể dùng breakpoint, nhưng biến có thể bị tối ưu.

Giữ linker script **FLASH** của MCU, không chọn script RAM. Nếu muốn khớp vùng stack dự phòng của bản standalone, đặt `_Min_Stack_Size = 0x1000;` trong linker FLASH do IDE tạo. F411VE có Flash 512 KiB và RAM 128 KiB.

Nhấn **Apply and Close**.

## Bước 5 — Build

1. Chọn project `F411_TOF_AUDIO`.
2. `Project > Clean...` > clean project này.
3. `Project > Build Project` hoặc biểu tượng búa.
4. Xem tab **Console**, build phải kết thúc không có error. File thường nằm ở `Debug/F411_TOF_AUDIO.elf`.

Nếu IDE chỉ biên dịch main mà không biên dịch HAL trong Src, kiểm tra Src không bị **Exclude from Build**, và có trong `Properties > C/C++ General > Paths and Symbols > Source Location`. Với project Empty, Src thường đã là thư mục nguồn.

| Lỗi | Cách xử lý |
|---|---|
| `stm32f4xx_hal.h: No such file` | Kiểm tra đã chép Inc và thêm đúng include path |
| `Please select first the target STM32F4xx device` | Thiếu symbol STM32F411xE |
| `undefined reference to HAL_...` | Thiếu HAL `.c`, file bị exclude, hoặc thiếu USE_HAL_DRIVER/config HAL |
| `multiple definition of main` | Có hai file ứng dụng được compile |
| `multiple definition of SysTick_Handler/EXTI0_IRQHandler` | Có handler khác ngoài main.c, thường từ project CubeMX |
| `undefined reference to SystemInit` | Chưa compile Src/system_stm32f4xx.c |
| `multiple definition of _init` | Đã chép nhầm runtime.c standalone vào project IDE |

Xem lỗi đầu tiên trong Console để tìm nguyên nhân, đừng chỉ nhìn dòng `make: Error` cuối cùng.

## Bước 6 — Nạp bằng Debug

1. Cắm cáp USB có dữ liệu vào **cổng ST-LINK** của board.
2. BOOT0=0; nếu dùng ST-LINK tích hợp MB1115, các jumper CN3 cần ở vị trí nối ST-LINK với MCU trên board.
3. Chuột phải project > **Debug As > STM32 C/C++ Application**.
4. Nếu có cửa sổ cấu hình: chọn ST-LINK, giao tiếp **SWD**; để **SWV/SWO trace tắt** vì PB3 đang là SDA của ToF.
5. Kiểm tra `C/C++ Application` trỏ ELF vừa build trong project mới, không trỏ ELF cũ ở workspace khác.
6. Nhấn **Debug**, chấp nhận mở Debug perspective nếu IDE hỏi.
7. IDE thường nạp xong rồi **dừng ở đầu main**. Bấm **Resume (F8)** để chương trình chạy.

Khi đã chạy: LED xanh đảo trạng thái mỗi giây; nhấn-thả B1 làm LED đỏ sáng. Cam sáng báo audio init thất bại; cam nháy nhanh báo clock init thất bại. Đèn đỏ không tự tắt sau nhả nút trong bản này.

Nếu `No ST-LINK detected`: kiểm tra đúng cổng, cáp dữ liệu và driver ST-LINK. Nếu thấy ST-LINK nhưng không nối được target: kiểm tra nguồn/jumper, thử reset mode **Connect under reset** và giảm SWD clock.

Muốn chạy độc lập sau khi đã nạp: kết thúc phiên debug rồi reset hoặc tắt/bật nguồn board. Flash giữ chương trình; không phải nạp lại mỗi lần bật nguồn. Khi đang test audio, không giữ CPU ở breakpoint.

## Bước 7 — Các lần sửa code sau

Trong project CubeIDE mới, sửa **Src/main.c** rồi Save > Build > Debug > Resume.

`Src/main.c` trong project mới là **bản sao**, không tự đồng bộ với `firmware/app.c` đang mở ở thư mục gốc. Nếu tiếp tục sửa app.c ở thư mục gốc, chạy `python tools/prepare_cubeide.py` để cập nhật bộ nhập, rồi chép lại main.c sang project IDE. Không ghi đè nếu đã sửa độc lập cả hai bản mà chưa đối chiếu.

Để nạp từ IDE không cần mở file HEX. Pi vẫn cần chạy `pi/stm32_bridge.py` để nhận ma trận/sự kiện và gửi âm thanh; xem README_VI.md mục 4 và 6.

## Nguồn và kiểm chứng

Các tên menu tạo project và cấu hình compiler theo [STM32CubeIDE User Guide UM2609](https://www.st.com/resource/en/user_manual/um2609-stm32cubeide-user-guide-stmicroelectronics.pdf). Một số nhãn có thể khác nhẹ theo phiên bản.

Bộ Inc/Src được sao từ nguồn đã build standalone, đã kiểm tra main.c bằng byte với app.c và biên dịch riêng toàn bộ các file C qua ARM GCC. Chưa thực hiện tạo project/nạp/debug bằng giao diện CubeIDE trên board thật.
