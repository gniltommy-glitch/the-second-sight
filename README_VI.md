# STM32F411E-DISCO + SEN0628 + Pi 5: ToF, nút EXTI và âm thanh

## 1. Phần cứng mà bản này hỗ trợ

Bản sửa được viết theo **STM32F411E-DISCO / MB1115, STM32F411VET6**, thạch anh HSE 8 MHz, codec CS43L22 và jack CN4 có sẵn; cảm biến là **DFRobot SEN0628 có RP2040**. Đây là giả định dựa trên sơ đồ bạn gửi, chưa được xác nhận bằng board thực tế. Không nạp nguyên bản này cho Black Pill F411CE: board đó không có sẵn PD14/CS43L22/jack như MB1115.

Đã đối chiếu cả 9 PDF được cung cấp, tập trung vào các trang liên quan; bản trích xuất nằm trong `research/pdf_text/`, ảnh sơ đồ trong `research/images/`. Các nội dung hướng dẫn trong PDF được dùng làm tài liệu tham khảo, không thay thế yêu cầu của bạn.

**Đã build ARM GCC thành công và kiểm tra vector trong binary. Chưa nạp/đo trên phần cứng.** Những lỗi chắc chắn trong mã cũ được phân biệt với các khả năng cần đo ở phần dưới.

## 2. Vì sao code cũ không chạy

### Clock sai ngay trước khi khởi tạo EXTI

Mã cũ ghi `PLLM=4`, `PLLN=192`, không đặt trường PLLP nên PLLP = 2. Với HSI 16 MHz:

```text
PLL input = 16 / 4 = 4 MHz         (ngoài khoảng cho phép)
VCO       = 4 × 192 = 768 MHz      (ngoài khoảng cho phép)
SYSCLK    = 768 / 2 = 384 MHz      (F411 tối đa 100 MHz)
```

Đây là tần số mà cấu hình đòi hỏi, **không phải khẳng định chip thực sự chạy ổn định ở 384 MHz**. Nó có thể kẹt chờ PLL, lỗi thực thi Flash hoặc HardFault. `SystemClock_Config()` được gọi trước `Button_Interrupt_Init()`, nên chương trình có thể chưa đến chỗ bật ngắt.

Ngoài ra:

- Thiếu cấu hình Flash wait states và chia APB1; APB1 của F411 tối đa 50 MHz.
- I2C ghi `CR2=42`, `CCR=210`, `TRISE=43` như PCLK1 = 42 MHz, nhưng clock không thiết lập như vậy.
- `USART2->BRR=52` không có baud xác định đúng nếu chưa biết PCLK1. Baud thực = PCLK1 / 52 khi oversampling 16.
- F411 có trường **PLLI2SM riêng**. Phép gán `PLLI2SCFGR=(192<<6)|(2<<28)` xóa M về 0, là giá trị không hợp lệ.

Nguồn cục bộ: datasheet STM32F411 trang 1, phần clock/đặc tính điện; RM0383 chương RCC, thanh ghi PLLCFGR và PLLI2SCFGR (trang 96–97), phần Flash latency.

### EXTI: cấu hình cơ bản đúng, cách kiểm tra chưa đúng

`SYSCFG → EXTI0 → NVIC → EXTI0_IRQHandler` trong mã cũ về cơ bản đi đúng hướng. Chưa có dữ liệu debug để kết luận dây/nút/vector thực tế là nguyên nhân.

- Nút USER B1 màu xanh trên MB1115 nối **PA0**, nhấn tạo mức cao/cạnh lên.
- **PD12 là LED xanh; PD14 mới là LED đỏ LD5.** Code cũ toggle PD12, không điều khiển LED đỏ.
- Không chống dội; một lần nhấn có thể toggle chẵn lần, nhìn như không sáng.
- `EXTI->PR |= 1` là read-modify-write trên thanh ghi **ghi 1 để xóa**. Nó có thể xóa cả các pending bit khác đang là 1. Với riêng EXTI0, lệnh này không nhất thiết làm mất ngắt; sửa đúng thành `EXTI->PR = 1`.
- Gửi chuỗi UART bằng vòng chờ ngay trong ISR kéo dài ngắt và có thể xen byte với dữ liệu gửi từ main. ISR mới chỉ xóa pending và tăng bộ đếm; main chống dội rồi bật đỏ/gửi sự kiện.
- Nếu dự án có một `EXTI0_IRQHandler` khác hoặc startup sai chip, phải sửa ở mức project. Định nghĩa macro chip trong một file không tự tạo startup/vector/linker.

Không cần chuyển vector table lên RAM cho bài này. Phần di chuyển vector trong `CO_CHE_INTERRUPT.pdf` phục vụ thay handler lúc runtime; ở đây startup liên kết trực tiếp handler. Xem RM0383 trang 202–210, PM0214 phần NVIC/VTOR (trang 225–232), và ba PDF bài giảng GPIO/EXTI/cơ chế ngắt.

### ToF: nhầm module SEN0628 với chip VL53L7CX trần

Sơ đồ SEN0628 trang 1–4 cho thấy đầu nối Gravity đi vào **RP2040**; RP2040 điều khiển VL53L7CX qua bus riêng. Vì vậy STM32 phải nói giao thức của DFRobot.

- `0x52` là địa chỉ dạng 8-bit của VL53L7CX (`0x29` dạng 7-bit), không phải địa chỉ mặc định của module SEN0628.
- SEN0628 dùng địa chỉ 7-bit **0x30/0x31/0x32/0x33**, tùy công tắc. Bản này chọn **0x33**, truyền HAL bằng `0x33 << 1 = 0x66`.
- Đọc bốn byte ở thanh ghi `0x0000` không trả về ma trận khoảng cách. Với chip trần cũng phải dùng ULD, nạp firmware/configuration và start ranging; không thể thay bằng một lệnh đọc thanh ghi tùy ý.
- Mã I2C cũ còn thiếu xử lý ACK/POS/BTF đúng cho phần cuối gói nhận, không thu hồi bus khi timeout và reset I2C sau NACK mà không khôi phục đầy đủ timing.

Bản sửa gửi lệnh 8×8 và đọc 128 byte khoảng cách theo FIFO/gói phản hồi DFRobot. Có kiểm tra status, command, length, timeout, chia khối nhận tối đa 32 byte. Các byte khoảng cách là little-endian. Tham chiếu [thư viện DFRobot](https://github.com/DFRobot/DFRobot_MatrixLidar) và [mẫu I2C 8×8](https://wiki.dfrobot.com/sen0628/docs/21562). Mã tham chiếu và giấy phép MIT được giữ ở `vendor/references/dfrobot/`.

**PB3 AF9 = I2C2_SDA là hợp lệ trên F411**, xem datasheet bảng 9 trang 48. Cần dùng SWD và tắt SWO/SWV trace vì PB3 nối ST-LINK qua SB13. PB10 còn nối đầu vào clock của microphone trên board; không dùng chức năng thu microphone đồng thời với I2C2 trong cấu hình này.

### Âm thanh: có CS43L22 ngoài chip, không có DAC nội F411

Jack CN4 nối headphone outputs của CS43L22 (sơ đồ MB1115 trang 5). Đường phát:

```text
Pi chạy Piper → PCM qua UART → bộ đệm STM32 → I2S3 + DMA → CS43L22 → jack 3.5 mm
```

Lỗi cũ gồm ghi vào register `0x01` của CS43L22 (đó là ID chỉ đọc), thiếu cấu hình interface I2S `0x06`, clock audio sai, không có timeout khi codec NACK, không giữ luồng I2S bằng DMA. UART cũng không có xác nhận/tránh tràn, và PCM không được kiểm tra sample rate.

Bản mới đọc ID, reset PD4, thực hiện trình tự khởi tạo bắt buộc, cấu hình I2S 16-bit và headphone; khởi động I2S bằng silence rồi power up codec. Xem [CS43L22 datasheet, mục 4.9–4.11 và chương 7](https://statics.cirrus.com/pubs/proDatasheet/CS43L22_F2.pdf), [driver ST](https://github.com/STMicroelectronics/stm32-cs43l22).

## 3. Chức năng bản sửa

| Phần | Cách hoạt động |
|---|---|
| Clock | HSE 8 MHz; CPU/AHB/APB2 96 MHz; APB1 48 MHz; Flash 3 wait states |
| ToF | Máy trạng thái chạy trong `while (1)`; I2C2 100 kHz bằng ngắt; đọc đủ 64 điểm |
| ToF startup | Chờ nguồn 3 giây; lệnh 8×8; chờ 5 giây ổn định theo thư viện; vẫn xử lý nút/audio |
| ToF chu kỳ | Sau mỗi phản hồi thành công chờ 67 ms rồi đọc tiếp; tốc độ thực phụ thuộc RP2040/giao tiếp, không cam kết 15 frame mới/giây |
| ToF lỗi | Timeout từng giao dịch, reset peripheral và thử cấu hình lại sau 1 giây; không treo vô hạn |
| Audio | PCM signed 16-bit little-endian, mono 16000 Hz; nhân sang hai kênh I2S |
| DMA | DMA1 Stream5 Channel0, circular; nửa buffer 256 stereo frame ≈ 16 ms |
| Audio buffer | 8192 mẫu mono ≈ 512 ms; prebuffer 1024 mẫu; thiếu dữ liệu thì phát silence và đếm underrun |
| UART Pi | USART2 1,000,000 baud, 8N1; ring RX/TX; CRC, sequence, ACK và retry |
| Nút | EXTI0 cạnh lên; ổn định 25 ms; một sự kiện mỗi lần nhấn-thả |
| LED đỏ PD14 | Sáng sau nhấn hợp lệ, giữ sáng đến reset |
| LED xanh PD12 | Đảo trạng thái mỗi giây khi main còn chạy |
| LED cam PD13 | Sáng khi codec init thất bại; nháy nhanh nếu clock init thất bại |

PLLI2S M=8, N=213, R=2 → I2S clock 106.5 MHz. I2SDIV=13, ODD=0, MCKOE=1 → sample rate thực khoảng **16000.601 Hz**, sai lệch khoảng **+37.6 ppm**, chưa kể sai số thạch anh. Dự kiến MCLK ≈ 4.096154 MHz; BCLK ≈ 512.019 kHz; LRCLK ≈ 16.000601 kHz.

UART RX có ưu tiên 1, DMA refill 2, EXTI0 3, I2C2 4. UART cần ưu tiên cao hơn refill vì ở 1 Mbaud cứ 10 µs lại có một byte. DMA tự phát dữ liệu; callback chỉ điền nửa buffer vừa rảnh. Đợi ToF không làm ngừng I2S. Các API HAL vẫn có một số đoạn kiểm tra BUSY ngắn có giới hạn, nên đây không phải hệ thống hard real-time được chứng nhận.

## 4. Nối dây

### SEN0628 → STM32

| SEN0628, theo nhãn chân | STM32 |
|---|---|
| SCL/RX | PB10 — I2C2_SCL AF4 |
| SDA/TX | PB3 — I2C2_SDA AF9 |
| GND | GND chung STM32 và Pi |
| VIN/+ | Nguồn ổn định **3.3 V**, đủ dòng cho module |

Chọn I2C, địa chỉ 0x33 theo hình trong mẫu DFRobot; **tắt/bật nguồn module sau khi đổi công tắc**. Nếu chọn địa chỉ khác, sửa `TOF_ADDR`.

Sơ đồ SEN0628 trang 4 có R2/R3 10 kΩ kéo đường ngoài lên **VIN**, cùng mạch chuyển mức MOSFET. Cấp VIN=3.3 V giúp bus ngoài ở 3.3 V. Nếu cấp 5 V, không được mặc định SDA/SCL chỉ lên 3.3 V. Dùng dây ngắn; đo mức idle/rise time trước khi tăng clock. Không dựa vào pull-up nội yếu của STM32. Pull-up có sẵn có thể cần bổ sung nếu dây dài/tải lớn, nhưng phải tính với các điện trở đang mắc song song.

Chú ý chân nguồn ghi `3V` trên MB1115: sơ đồ dùng regulator 3.3 V **qua diode D3**, nên điện áp đầu ra có thể thấp hơn 3.3 V. SEN0628 được công bố nguồn 3.3–5 V; hãy đo và dùng nguồn đáp ứng mức này, không mặc định OUT_3V luôn đủ. Nếu lấy 3.3 V từ Pi, kiểm tra ngân sách dòng chung. Không nối hai nguồn 3.3 V khác nhau với nhau.

### Pi 5 → STM32

| Pi 5, số chân vật lý | STM32 |
|---|---|
| Pin 8 — GPIO14/TXD | PA3 — USART2_RX |
| Pin 10 — GPIO15/RXD | PA2 — USART2_TX |
| Pin 6 — GND | GND |

Tín hiệu UART TTL 3.3 V; không nối RS-232 hay tín hiệu 5 V vào Pi. Nguồn Pi và board có thể riêng, nhưng GND phải chung. Không cần dây audio analog từ Pi sang STM32.

Các dây CS43L22 đã nằm trên board: PB6=SCL, PB9=SDA, PD4=RESET; PA4=WS, PC7=MCLK, PC10=CK, PC12=SD. Cắm tai nghe/loa có ampli vào CN4. Jack headphone không thay ampli công suất cho loa thụ động lớn. Volume khởi tạo -20 dB, tone thử có biên độ nhỏ.

## 5. Build và nạp

```powershell
python tools/build.py
python tools/verify_binary.py
python -m unittest discover -s tests -v
```

Script đã tìm thấy ARM GCC 13.3.1 trong STM32CubeIDE 1.19.0 trên máy này. Máy khác có thể truyền `--gcc "đường_dẫn/arm-none-eabi-gcc"`. HAL/CMSIS cần thiết đã nằm trong `vendor/`; không cần tải lại để build. Phiên bản nguồn và SHA-256 nằm trong `vendor/manifest.json`.

File nạp:

- `build/stm32_tof_audio.elf`: dùng để debug.
- `build/stm32_tof_audio.hex`: dùng STM32CubeProgrammer qua ST-LINK/SWD, địa chỉ có trong HEX.
- `build/stm32_tof_audio.bin`: nếu dùng BIN, địa chỉ nạp là **0x08000000**.

Đặt BOOT0=0; kết nối ST-LINK, mở HEX, download rồi reset/run. Nếu firmware cũ làm mất kết nối, thử **Connect under reset**, dùng NRST, giảm SWD clock. Chưa thực hiện nạp thiết bị nào trong phiên làm việc này.

File đang mở `#define STM32F411xE.c` là entry wrapper, include mã chính `firmware/app.c`. Chỉ compile wrapper **một lần**, không đồng thời compile `firmware/app.c`. Bản gốc còn nguyên tại `original_STM32F411xE.c.bak`.

### Đưa vào project CubeIDE/Keil đang có

Có thể dùng nội dung `firmware/app.c` làm `main.c`, nhưng phải tích hợp các file hỗ trợ, không chỉ dán một đoạn vào project cũ:

1. Target **STM32F411VETx**, định nghĩa `STM32F411xE`, `USE_HAL_DRIVER`, HSE_VALUE=8000000.
2. Thêm include paths `firmware`, `vendor/hal/Inc`, `vendor/device/Include`, `vendor/core`; dùng HAL config đã cung cấp.
3. Thêm các HAL `.c` trong `vendor/hal/Src` và **một** `system_stm32f4xx.c`.
4. Chỉ một startup đúng chip, một main, một SysTick và một bản của mỗi IRQ handler. Bỏ handler trùng trong `stm32f4xx_it.c` hoặc chuyển handler sang file đó và điều chỉnh scope tương ứng.
5. GCC dùng startup/linker trong bộ này. Keil dùng startup/scatter của target Keil; không thêm GCC `runtime.c`/`.ld`/startup `.s` của bộ standalone vào Keil.
6. Không để code CubeMX tự cấu hình lại PA0, PB3, PB10, UART2 hay I2S3 sau init của chương trình này. Tắt SWV/SWO trace.

## 6. Chạy bên Pi 5

### Bật đúng UART trên header 40 chân

Với Raspberry Pi OS hỗ trợ overlay tương ứng, thêm vào `/boot/firmware/config.txt`:

```ini
[pi5]
dtoverlay=uart0-pi5
```

Tắt serial login console trên UART dùng cho STM32, rồi reboot. Kiểm tra `/boot/firmware/cmdline.txt` và serial-getty nếu cổng vẫn bị chiếm; giữ các tham số boot khác. Xác minh pin bằng `pinctrl get 14 15`, liệt kê `ls -l /dev/ttyAMA* /dev/serial*`. Cổng ví dụ ở đây là `/dev/ttyAMA0`; thay `--port` theo máy thực tế. **Không mặc định `/dev/serial0` trên Pi 5 là pin 8/10**, vì có cấu hình trỏ tới UART debug riêng. Tham chiếu [overlay chính thức uart0-pi5](https://github.com/raspberrypi/linux/blob/rpi-6.12.y/arch/arm/boot/dts/overlays/README) và [tài liệu UART Raspberry Pi](https://www.raspberrypi.com/documentation/computers/configuration.html).

USB–UART 3.3 V hỗ trợ 1 Mbaud cũng dùng được: TX/RX/GND như trên và chọn `/dev/ttyUSB0`. ST-LINK trên MB1115 không mặc định nối USART2 thành USB serial; sơ đồ SB10/SB11 là DNF, nên chỉ cắm USB ST-LINK không tự tạo đường truyền Pi–STM32 của bản này.

### Thử nút và ToF trước

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install pyserial
python pi/stm32_bridge.py --port /dev/ttyAMA0 --show-matrix
```

Nếu user chưa có quyền serial, thêm user vào nhóm `dialout` rồi đăng nhập lại. Đợi khoảng 8 giây sau bật nguồn STM32 để ToF hoàn tất startup.

Nhấn B1:

- LED đỏ sáng; `exti_edges` và `button_count` tăng.
- Pi in JSON `{"event": "capture", "count": 1, ...}`.
- STM32 retry sự kiện mỗi 100 ms tới khi Pi ACK. Gói nút chỉ 13 byte trên UART, khoảng 130 µs truyền vật lý; tổng trễ còn gồm debounce và lịch xử lý.

Pi ACK có nghĩa là **đã nhận/đưa yêu cầu vào queue**, không phải đã lưu ảnh. Queue giữ tối đa 128 yêu cầu; khi đầy, Pi ngừng ACK để STM32 giữ sự kiện. Reset nguồn STM32 xóa các sự kiện chưa ACK; queue không phải nhật ký lưu bền qua mất điện.

### Thử âm thanh, rồi dùng Piper

```bash
python pi/stm32_bridge.py --port /dev/ttyAMA0 --tone
```

Sau tone, chương trình tiếp tục nghe nút/ToF. Đóng bằng Ctrl+C trước khi chạy một process khác mở cùng cổng.

Piper chạy trên Pi, không chạy model trên STM32. Với Piper hiện tại và model tiếng Việt bạn đã có:

```bash
python -m piper -m /path/to/voice.onnx -f speech.wav -- 'Xin chào, hệ thống đã sẵn sàng.'
ffmpeg -i speech.wav -ac 1 -ar 16000 -c:a pcm_s16le speech16.wav
python pi/stm32_bridge.py --port /dev/ttyAMA0 --wav speech16.wav
```

Cần cài Piper và FFmpeg trên Pi nếu chưa có; cú pháp Piper theo [CLI chính thức](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/CLI.md). Bản Piper cũ có thể dùng tên option khác. Model và file cấu hình model không có trong workspace này.

Bridge kiểm tra WAV PCM16 mono 16 kHz, bỏ header WAV rồi mới gửi mẫu. Không gửi nguyên header RIFF, MP3 hay WAV 22.05 kHz vào cổng. PCM cần 32000 byte/s, tương đương tối thiểu 320000 bit/s ở UART 8N1; 115200 baud không đủ. 1 Mbaud có dư cho framing và ACK. Đường TX/RX độc lập nên ToF/nút vẫn về Pi khi PCM đang đi xuống STM32.

### Ghép vào YOLO để lưu đúng mảng pixel đang hiển thị

Không có mã YOLO hiện tại trong workspace, nên đã cung cấp hook có thể import. Trong chương trình YOLO của bạn, thêm đường dẫn thư mục `pi` vào import path, rồi:

```python
from stm32_bridge import Bridge
from yolo_capture import save_requested_frames

bridge = Bridge('/dev/ttyAMA0')  # tạo một lần, chỉ một chủ sở hữu cổng
bridge.wait_ready()

# Trong vòng xử lý YOLO, sau khi tạo annotated_frame để hiển thị:
save_requested_frames(bridge, annotated_frame, 'captures')

# Phát TTS từ worker thread riêng để vòng YOLO vẫn chạy:
# bridge.play_wav('speech16.wav')

# Khi chương trình kết thúc: bridge.close()
```

Truyền vào **đúng mảng ảnh BGR đã vẽ kết quả và đưa lên màn hình**. Hook lưu frame có sẵn khi YOLO xử lý yêu cầu, không chụp lại toàn desktop và không bảo đảm khớp thời điểm vật lý nhấn nút từng millisecond. Nếu màn pixel được tạo từ một buffer khác `annotated_frame`, truyền buffer đó sau khi đổi sang định dạng ảnh phù hợp. Không mở thêm bridge CLI đồng thời với YOLO.

## 7. Giao thức STM32 ↔ Pi

```text
A5 5A | type:u8 | seq:u16 LE | length:u16 LE | payload | CRC:u16 LE
```

CRC-16/CCITT-FALSE: poly=0x1021, init=0xFFFF, xorout=0, không phản chiếu; tính từ `type` tới hết payload, không gồm magic. Payload tối đa 1024 byte.

| Type | Chiều | Payload |
|---|---|---|
| 0x10 START | Pi → STM32 | Tổng số mẫu mono, u32 LE |
| 0x11 PCM | Pi → STM32 | 1–512 mẫu int16 LE, số byte phải chẵn |
| 0x12 STOP | Pi → STM32 | Rỗng; bỏ queue, phần DMA đang phát hết trong ≤32 ms |
| 0x20 BUTTON_ACK | Pi → STM32 | Bộ đếm nút đã nhận, u32 LE |
| 0x80 ACK | STM32 → Pi | u8: 0 OK, 1 busy/retry, 2 invalid, 3 audio unavailable; echo seq |
| 0x81 BUTTON | STM32 → Pi | Bộ đếm nút u32 LE; retry giữ cùng số |
| 0x82 TOF | STM32 → Pi | Frame counter u32 LE + 64 khoảng cách u16 LE |
| 0x83 STATUS | STM32 → Pi | 48 byte: 4 u8 + 11 u32, giải mã trong Bridge |
| 0x84 DONE | STM32 → Pi | Số mẫu đã phát u32 LE, sau chờ phần DMA cuối |
| 0x85 BOOT | STM32 → Pi | Rỗng; báo reset và xóa trạng thái dedup phía Pi |

Pi gửi một lệnh rồi đợi ACK. Khi mất ACK hoặc busy, gửi lại cùng sequence. STM32 nhớ lệnh thành công gần nhất để không thêm lại PCM trùng. CRC sai bị bỏ, bên gửi timeout rồi retry. Telemetry ToF có thể bị bỏ khi queue TX thiếu chỗ; sự kiện nút vẫn được giữ tới ACK. Thứ tự hàng/cột giữ theo DFRobot; thử che từng góc để xác định hướng lắp thực tế. Giá trị bất thường/không có mục tiêu cần xử lý theo firmware module; không diễn giải mọi số u16 thành vật cản hợp lệ.

## 8. Kiểm tra trên bàn

1. **Clock/boot:** xanh nháy mỗi giây, có STATUS. Cam nháy nhanh → kiểm tra HSE 8 MHz/board đúng mã. Cam sáng liên tục, `audio_ok=0` → kiểm tra codec I2C1/reset/ID.
2. **EXTI:** watch `exti_edges`, `button_count`, `GPIOA->IDR`, `EXTI->PR`, `NVIC->ISER[0]`, `SCB->VTOR`, PRIMASK. Nhấn PA0 phải lên 1. EXTI0 là IRQ6, vector offset **0x58**. Binary đã xác minh vector trỏ handler thật, có Thumb bit.
3. **Tách lỗi EXTI:** thử `EXTI->SWIER = 1` trong debugger để xác minh `exti_edges` tăng. Lệnh này không tự bật LED vì logic debounce còn kiểm tra mức PA0 thật. Nếu vector chạy mà nút không tạo cạnh, kiểm tra B1/PA0. Không cần ghi đè flash tại 0x58.
4. **I2C2:** idle SCL/SDA phải cao; logic analyzer phải thấy address 0x33 (byte write 0x66/read 0x67). Lệnh mode: `55 00 05 01 00 00 00 08`; lệnh đọc: `55 00 01 02`. Phản hồi thành công đọc ma trận bắt đầu `53 02 80 00` rồi 128 byte. Nếu NACK: công tắc/nguồn/địa chỉ/GND/dây/SWO. Không tìm 0x29 trên Gravity để kết luận module chết.
5. **I2S:** đo các clock đã nêu ở mục 3, đọc ID CS43L22 tại 0x01 với mask 0xF8 = 0xE0, kiểm tra PCM/I2S có dữ liệu khi phát tone. `audio_ok=1` chỉ xác nhận init driver, không thay phép đo jack.
6. **Đồng thời:** phát tiếng 30–60 giây, nhấn nhiều lần, che các vùng ToF. `tof_frames` tăng; `button_count` bằng số nhấn-thả; `underruns`, `uart_errors`, `rx_overflows`, `crc_errors` không tăng. UART lỗi nhiều → đo baud/ground/dây trước.
7. **Lỗi nguồn cảm biến:** rút module vẫn phải còn heartbeat/nút/audio; nối lại thử phục hồi. Nếu slave giữ SDA thấp, reset peripheral không đảm bảo nhả bus: cần sửa dây/nguồn hoặc power-cycle module. Chưa triển khai điều khiển nguồn cảm biến hay 9 xung bus-clear.

Không đặt breakpoint lâu khi đánh giá âm thanh liên tục: dừng CPU làm callback không điền buffer đúng hạn. Binary và mô phỏng host không kiểm chứng được tín hiệu điện, thời gian IRQ thực, khả năng nguồn, firmware RP2040 hay chất lượng tiếng. Những bước bàn thử ở trên vẫn cần thực hiện.

## 9. Kết quả kiểm tra phần mềm

- ARM GCC 13.3.1: build và link thành công; mã ứng dụng bật `-Wall -Wextra -Werror`.
- Vector Reset, SysTick, EXTI0, DMA1 Stream5, I2C2 EV/ER, USART2 đã so với symbol trong ELF; IRQ đều là handler mạnh, không rơi vào weak default.
- Kiểm tra DMA/PCM/UART buffers nằm trong SRAM, stack ban đầu 0x20020000, đủ vùng dự phòng stack 4 KiB.
- 11 unittest phía Pi qua: CRC, phân mảnh/gộp gói, timeout/mất đồng bộ, giới hạn payload, endian ma trận, retry/busy/mất ACK không nhân đôi PCM, nút khi truyền audio, reset và queue đầy.
- Mô phỏng serial kiểm tra phía Pi; không thay kiểm thử phần cứng STM32 hay firmware RP2040.
