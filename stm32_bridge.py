import sys
import struct
import argparse
import serial

def main():
    parser = argparse.ArgumentParser(description="STM32 Bridge Test")
    parser.add_argument("--port", required=True, help="Serial port (e.g. COM3)")
    parser.add_argument("--baud", type=int, default=1000000, help="Baud rate")
    parser.add_argument("--show-matrix", action="store_true", help="Display 8x8 ToF matrix")
    args = parser.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
        print(f"Đã kết nối thành công tới {args.port} ở tốc độ {args.baud} baud.")
    except Exception as e:
        print(f"Không thể mở cổng {args.port}: {e}")
        sys.exit(1)

    buffer = bytearray()
    
    while True:
        try:
            data = ser.read(ser.in_waiting or 1)
            if data:
                buffer.extend(data)
                
            # Tìm header packet magic A5 5A
            while len(buffer) >= 7:
                if buffer[0] != 0xA5 or buffer[1] != 0x5A:
                    buffer.pop(0)
                    continue
                    
                msg_type = buffer[2]
                seq, length = struct.unpack("<HH", buffer[3:7])
                
                if len(buffer) < 7 + length + 2:
                    break  # Đợi đủ gói
                    
                payload = buffer[7:7+length]
                # Bỏ gói đã đọc khỏi buffer
                del buffer[:7+length+2]
                
                # Tín hiệu ToF (type = 0x82)
                if msg_type == 0x82 and len(payload) >= 132:
                    frame_cnt = struct.unpack("<I", payload[:4])[0]
                    distances = struct.unpack("<64H", payload[4:132])
                    
                    if args.show_matrix:
                        # Phóng to khung hiển thị bằng cách thêm khoảng trắng và dòng trống
                        print(f"\033[H\033[JFrame #{frame_cnt} (ToF 8x8):")
                        print("=" * 55)
                        for row in range(8):
                            # Tăng khoảng cách giữa các cột để nhìn to hơn
                            line = "  ".join(f"{distances[row*8 + col]:5d}" for col in range(8))
                            print(line)
                            print() # Thêm dòng trống giữa các hàng để khung cao và to hơn
                        print("=" * 55)
                    else:
                        print(f"Nhận frame ToF #{frame_cnt} thành công.")
                        
        except KeyboardInterrupt:
            print("\nĐã dừng chương trình.")
            ser.close()
            break

if __name__ == "__main__":
    main()