# Bộ nhận dạng câu gọi cục bộ

- Vosk Android 0.3.75: https://github.com/alphacep/vosk-api — Apache License 2.0.
- JNA 5.18.1: https://github.com/java-native-access/jna — LGPL 2.1 hoặc Apache License 2.0; sử dụng lựa chọn Apache 2.0.
- Mô hình `vosk-model-small-en-us-0.15`: https://alphacephei.com/vosk/models — Apache License 2.0, tải từ https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip.
- Mô hình được đóng gói dưới `android/app/src/main/assets/wake-en`; không có API key riêng, không tải khi chạy, không lưu dữ liệu micro.

Bản sao Apache License 2.0: `APACHE-2.0.txt` trong thư mục này. Tên “aurelia” có trong bảng từ của mô hình. Ngữ pháp nhận “hey aurelia” và nhánh từ không rõ; chỉ kích hoạt khi cả hai từ có độ tin cậy ít nhất 0,75. Cần đo trên giọng nói và môi trường thật.
