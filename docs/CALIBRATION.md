# Ghép camera với ToF 8×8 và kiểm tra hướng dẫn

`assistive/navigation.py` nhận tọa độ YOLO **đã bỏ letterbox**, chuẩn hóa theo ảnh camera gốc: trái trên `(0,0)`, phải dưới `(1,1)`. Mọi timestamp truyền vào policy phải dùng `time.monotonic()` trên Pi. Không truyền thời gian UNIX hoặc bộ đếm frame STM32 thay cho thời gian.

## 1. Phân biệt ba việc

- Cảnh báo khoảng cách phía trước bằng ToF: chạy ngay cả khi chưa ghép camera, trên các zone giữa đã kiểm tra hướng lắp.
- Gắn câu “người bên trái cách khoảng 1,2 mét”: chỉ bật khi đã có phép chiếu ToF → camera và kiểm chứng thực tế.
- Đề xuất hướng có lối đi bộ: dựa trên mask sidewalk, loại vùng road và hộp vật cản mở rộng. Không suy ra lối đi an toàn chỉ vì YOLO không thấy vật.

Khung 8×8 là 64 vùng đo góc, không phải ảnh chiều sâu có một điểm cho mỗi pixel. Một zone có thể chứa nhiều bề mặt. Vật nhỏ, cành cây, kính, ổ gà hoặc bậc xuống vẫn có thể không được phát hiện đầy đủ. Do đó đầu ra hiện tại dùng lời quan sát và yêu cầu kiểm tra trước khi đổi hướng; thiết bị chưa xác nhận một tuyến đường hoặc lần sang đường là an toàn.

## 2. Kiểm tra đúng phần cứng và chiều ma trận

Tài liệu DFRobot cho module [SEN0628](https://www.dfrobot.com/product-2999.html) liệt kê VL53L7CX; trang [hướng dẫn giao tiếp chính hãng](https://wiki.dfrobot.com/sen0628/docs/21555) mô tả I2C/UART và dữ liệu ma trận. Hãy đối chiếu SKU trên module thực tế. Không lấy FOV của VL53L5CX rồi áp vào một module VL53L7CX hoặc phiên bản khác.

Firmware `app.c` mà bạn cung cấp gửi 64 số `uint16` theo thứ tự gốc của module, đơn vị mm, kèm bộ đếm frame. Gói hiện tại không mang `target_status`, độ tin cậy từng zone hoặc timestamp lúc cảm biến đo. Code Pi bỏ giá trị 0, 65535 và ngoài khoảng cấu hình; phép lọc này không thay thế thông tin chất lượng đo mà firmware chưa truyền.

Trước khi dùng:

1. Cố định camera và ToF trên cùng giá đỡ. Không xoay/rung tương đối sau khi hiệu chuẩn.
2. Xem ma trận 8×8 từ công cụ bridge và đưa một tấm bìa lần lượt vào bên trái, phải, trên, dưới của trường nhìn. Ghi nhận index nào thay đổi. Các index 0–7 là hàng đầu của dữ liệu nhận; chưa chắc là phía trên của thế giới thực.
3. Chọn `flip_x`, `flip_y`, `rotate_quarters` để các zone hiển thị đúng chiều camera. Thứ tự biến đổi trong code: lật X, lật Y, rồi xoay theo chiều kim đồng hồ, mỗi lần 90°.
4. Kiểm tra các zone 27, 28, 35, 36 thực sự nhìn vào khu vực trước người dùng. `emergency_zones` là index dữ liệu thô, chưa qua biến đổi.
5. Đặt vật ở 0,4 m, 0,7 m, 1 m, 2 m để kiểm tra đơn vị mm, độ trễ, lỗi đo và ngưỡng cảnh báo. `emergency_mm` mặc định 700 chỉ là ngưỡng thử nghiệm; phải xác định theo tốc độ đi, độ trễ hệ thống và cách lắp.

Việc xác định index bằng đo thực tế quan trọng hơn suy đoán từ mặt chữ của board. Với các cảm biến ST, hình zone trong datasheet và chiều nhìn từ phía ống kính cần được đọc đúng; có thể tham khảo cách mô tả này trong [datasheet VL53L5CX, mục Zone mapping](https://www.st.com/resource/en/datasheet/vl53l5cx.pdf), nhưng không sao chép thông số giữa các model.

## 3. Mặc định: chưa hiệu chuẩn

```json
{
  "calibrated": false,
  "max_age_seconds": 0.35,
  "min_mm": 100,
  "max_mm": 3500,
  "min_support": 2,
  "bbox_inset": 0.10,
  "emergency_mm": 700,
  "emergency_zones": [27, 28, 35, 36],
  "projection": {
    "mode": "homography",
    "matrix": null,
    "validated_range_mm": [500, 2500],
    "rotate_quarters": 0,
    "flip_x": false,
    "flip_y": false
  }
}
```

Đây là giá trị của mục `tof` trong cấu hình ứng dụng. Chỉ đổi `calibrated` thành `true` sau khi có ma trận và kết quả kiểm chứng. Không điền ma trận đơn vị để “bật tính năng”: ma trận đơn vị chỉ đúng trong test tổng hợp, không phải hiệu chuẩn thật.

Khi chưa hiệu chuẩn, một zone trung tâm hợp lệ báo quá gần vẫn phát câu `Dừng lại. Cảm biến báo vật cản phía trước...`. Câu này không gán khoảng cách cho class YOLO cụ thể. Nếu ToF cũ, sai số lượng hoặc timestamp nằm trong tương lai, dữ liệu bị loại.

## 4. Cách A: Homography cho khoảng cách đã kiểm chứng

Homography phù hợp để bắt đầu với camera/ToF gần nhau và tập khoảng cách giới hạn. Phép chiếu phụ thuộc độ sâu khi hai tâm quang học lệch nhau; một homography duy nhất không mô hình hóa đầy đủ parallax ở mọi khoảng cách.

Quy trình:

1. Giữ nguyên độ phân giải, crop, zoom và xoay camera như khi chạy ứng dụng. Chọn một mặt phẳng thử ở khoảng cách làm việc.
2. Đưa một tấm mục tiêu nhỏ nhưng đủ phủ zone đến nhiều vị trí trên mặt phẳng. Với mỗi quan sát đáng tin cậy, ghi tâm zone ToF sau biến đổi chiều và tọa độ tâm mục tiêu trên ảnh camera.
3. Tâm zone gốc có tọa độ `u=(column+0.5)/8`, `v=(row+0.5)/8`. Tọa độ camera là `x=pixel_x/width`, `y=pixel_y/height`.
4. Thu nhiều cặp trải đều trường nhìn, không thẳng hàng. Tối thiểu toán học là bốn cặp; thực tế dùng nhiều hơn và dành một phần dữ liệu riêng để đánh giá.
5. Dùng OpenCV `findHomography` với RANSAC; chép ma trận 3×3 thu được vào `tof.projection.matrix`. Tài liệu phép biến đổi có ở [OpenCV calib3d](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).
6. Kiểm tra các cặp chưa dùng để fit, ở cả gần/xa và bốn góc. Chỉ khai báo `validated_range_mm` trong khoảng mà sai số vẫn chấp nhận được. Ngoài khoảng này code từ chối gắn khoảng cách với đối tượng.

Ví dụ tính ma trận từ dữ liệu đã thu, chạy trong môi trường có NumPy/OpenCV:

```python
import cv2
import numpy as np

# Điền các cặp đo THỰC TẾ; mỗi hàng cùng chỉ số phải cùng mục tiêu.
tof_points = np.asarray(measured_tof_uv, dtype=np.float64)
camera_points = np.asarray(measured_camera_xy, dtype=np.float64)
H, inliers = cv2.findHomography(tof_points, camera_points, cv2.RANSAC, 0.02)
if H is None:
    raise RuntimeError("Không tìm được homography; kiểm tra dữ liệu hiệu chuẩn")
print(H.tolist())
```

Ngưỡng `0.02` trong ví dụ là tọa độ ảnh chuẩn hóa, tương đương 2% kích thước ảnh; phải chọn lại từ độ chính xác yêu cầu. Một zone ToF bao phủ vùng lớn, nên tâm phép chiếu chỉ là xấp xỉ vị trí bề mặt phản xạ.

### Công cụ có sẵn trong dự án

`scripts/calibrate_tof.py` thực hiện fit, kiểm tra dữ liệu và xuất cả sai số từng điểm. Tạo `measurements.json` với các trường sau:

```json
{
  "validated_range_mm": [600, 2000],
  "flip_x": false,
  "flip_y": false,
  "rotate_quarters": 0,
  "pairs": [],
  "validation_pairs": []
}
```

Điền `pairs` bằng **ít nhất sáu** bản ghi đo thực tế; mỗi bản ghi có dạng `{"zone": 27, "camera_xy": [0.42, 0.48]}`. Các số ở đây chỉ minh họa cấu trúc. `camera_xy` phải thay bằng tọa độ bạn đo. Thay `zone` bằng `"tof_uv": [u, v]` nếu đã có tọa độ nguồn liên tục; `tof_uv` cũng là tọa độ **trước khi** xoay/lật. Không khai báo đồng thời hai trường này.

`validation_pairs` cùng định dạng, nhưng phải là các quan sát riêng không dùng để fit, thu ở nhiều vị trí và nhiều độ sâu trong khoảng khai báo. Khoảng `validated_range_mm` là phạm vi bạn cam kết kiểm chứng; công cụ không tự đo được khoảng này.

```bash
python scripts/calibrate_tof.py measurements.json \
  --output tof-candidate.json --report calibration-report.json
```

Công cụ cần OpenCV và NumPy, từ chối tọa độ không hợp lệ, điểm thẳng hàng, ma trận suy biến hoặc tỷ lệ inlier dưới 60%. Báo cáo gồm số điểm, inlier, RMSE, sai số lớn nhất và sai số các điểm kiểm chứng; tất cả sai số ở tọa độ ảnh chuẩn hóa. Đọc cả sai số các điểm bị RANSAC loại để tìm ghép nhầm hoặc parallax, thay vì chỉ nhìn RMSE inlier.

File xuất chỉ là phần cấu hình `tof` cần ghép vào cấu hình ứng dụng, luôn giữ `calibrated: false`. Công cụ không tự sửa cấu hình đang chạy, không tự bật hiệu chuẩn và không lấy sai số fit thấp làm bằng chứng hoạt động đúng ở khoảng cách khác.

## 5. Cách B: Camera pinhole + ngoại chuẩn

Khi có nội chuẩn camera và phép biến đổi cứng đo được từ ToF sang camera, có thể dùng:

```json
{
  "mode": "pinhole",
  "validated_range_mm": [500, 2500],
  "camera_intrinsics_normalized": [0.8, 0.8, 0.5, 0.5],
  "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
  "translation_mm": [0, 0, 0],
  "tof_fov_degrees": [60, 60],
  "distance_mode": "axial",
  "rotate_quarters": 0,
  "flip_x": false,
  "flip_y": false
}
```

**Tất cả số trong ví dụ này chỉ minh họa định dạng.** Không sử dụng chúng làm kết quả hiệu chuẩn. Phải thay bằng số đo đúng board, camera và bố trí của bạn.

- Nội chuẩn chuẩn hóa là `[fx/width, fy/height, cx/width, cy/height]` của đúng ảnh đưa vào model.
- Hệ tọa độ cả hai cảm biến: X sang phải, Y xuống dưới, Z hướng ra trước, sau khi xử lý xoay/lật zone. `P_camera = R × P_tof + translation_mm`.
- `distance_mode="axial"` coi khoảng cách là Z; `"radial"` coi đó là độ dài tia. Bắt buộc khai báo đúng; thiếu trường này thì không chiếu. Đối chiếu tài liệu hoặc thử tường vuông góc: trên một số cảm biến ST, giá trị đã được chuyển về khoảng cách vuông góc, như giải thích của [kỹ sư ST về VL53L5CX](https://community.st.com/mems-sensors-48/for-vl53l5cx-what-does-resultsdata-distance-mm-zonenum-exactly-mean-7106). Chưa thể suy ra chính xác chế độ của module khác chỉ từ tên ToF.
- Projector hiện mô hình hóa zone bằng tâm lưới pinhole và FOV ngang/dọc, chưa dùng bảng tia quang học hiệu chuẩn riêng cho từng zone. Chỉ bật trong khoảng độ sâu đã kiểm chứng.
- Code chưa xử lý méo ống kính trong projector. Nếu dùng ảnh đã undistort, detector và hiệu chuẩn phải cùng dùng ảnh đó. Nếu dùng ảnh thô có méo đáng kể, cần bổ sung mô hình distortion hoặc dùng homography và kiểm chứng trong vùng làm việc hẹp.

## 6. Quy tắc gắn khoảng cách đang chạy

Mỗi zone hợp lệ được chiếu lên ảnh. Điểm phải nằm trong bbox đã thu vào 10% mỗi cạnh; nếu đối tượng có polygon thì điểm phải nằm trong polygon. Zone rơi vào nhiều bbox bị bỏ vì chưa xác định được vật nào sinh phép đo.

Mỗi đối tượng cần tối thiểu hai zone hỗ trợ. Khoảng cách báo là phần tử tại phân vị thấp khoảng 20% của tập hỗ trợ, rồi làm tròn xuống 0,1 m khi đọc. Đây là lựa chọn ưu tiên cảnh báo gần; không phải ước lượng thống kê chính xác của tâm đối tượng. Vật nhỏ có thể vẫn được đọc tên/hướng nhưng không được đọc khoảng cách.

Ứng dụng cần loại cặp camera/ToF quá lệch thời gian trước khi gọi `analyze`. `max_age_seconds` chỉ kiểm tra độ cũ của ToF tại lúc policy chạy. Do firmware không gửi timestamp lúc đo, timestamp Pi là lúc nhận dữ liệu; nghẽn serial có thể làm mẫu đã cũ trông như vừa đến. Hãy đo độ trễ đầu-cuối khi camera/YOLO, TTS và truyền âm thanh đều hoạt động.

## 7. Lối đi, đèn và lớp tiền

Trong `navigation.labels`, tên class được so khớp không phân biệt hoa/thường, dấu cách và `-` chuyển thành `_`. Phải đặt đúng tên class thực tế của checkpoint:

| Nhóm | Vai trò |
|---|---|
| `sidewalk` | Chỉ polygon mới được dùng làm bằng chứng lối đi bộ. |
| `road` | Vùng cấm đề xuất hướng; khi không có polygon, dùng cả bbox để chặn. |
| `zebra` | Chỉ kích hoạt cảnh báo sang đường khi tâm bbox ở một phần ba giữa và cạnh dưới nằm trong nửa dưới ảnh. |
| `pedestrian_red` | Dừng ngay khi nhận diện; thắng khi đồng thời thấy đỏ/xanh. |
| `pedestrian_green` | Phải ổn định theo cả số quan sát và thời gian. Chỉ thông báo trạng thái đèn. |
| `ignore` | Class nền/ngữ nghĩa không được đếm thành vật cản. |

Đèn giao thông chung `traffic_light` không thể suy ra đèn người đi bộ. Model phải học riêng đèn người đi bộ đỏ/xanh; không dùng màu đèn xe thay thế. Trạng thái xanh mặc định cần ít nhất ba quan sát trong tối thiểu 0,6 giây, không có khoảng ngắt quá một giây. Thấy đỏ được cảnh báo ngay. Hệ thống chưa liên kết hình học từng cụm đèn với đúng vạch đang đứng, chưa ước lượng xe rẽ và chưa có thao tác xác nhận ý định sang đường. Vì vậy không phát lệnh “đi qua ngay” hoặc “sang đường an toàn”. Nút xanh STM32 đang dành cho OCR.

Đề xuất trái/giữa/phải yêu cầu ít nhất 70% điểm mẫu của hành lang gần người dùng nằm trong sidewalk, không giao vùng road, không giao bbox vật cản sau mở rộng. Chỉ có nhãn phân loại hoặc bbox sidewalk là chưa đủ. Tọa độ ảnh chưa thay thế được chiều rộng lối đi tính bằng mét; phải thử với vị trí gắn trên người và thay đổi cao độ camera.

`navigation.cash_labels` là ánh xạ tên class tiền sang mệnh giá tiếng Việt. Class đã khai báo ở đây được đọc riêng và không đếm thành vật cản. Không có model nhận diện tiền thì không tự suy ra mệnh giá từ một class vật cản.

Quy ước ranh giới: từ 1 đến **4** vật đọc từng tên/hướng; từ 5 vật dùng “Phía trước có nhiều chướng ngại vật”. User chưa quy định trường hợp đúng 4, nên code chọn đọc chi tiết. Các nhóm semantic và tiền không được tính vào số này.

## 8. Kiểm chứng trước khi thử trên người dùng

Chạy `python -m unittest discover -s tests -p test_navigation.py -v` để kiểm tra logic với dữ liệu tổng hợp. Các test bao phủ ranh giới 4/5, dữ liệu cũ, giá trị lỗi, thiếu hiệu chuẩn, ít zone, bbox chồng nhau, lối đi/road mâu thuẫn, đèn đỏ thắng xanh và độ ổn định xanh. Chúng không đo chất lượng checkpoint hoặc xác nhận độ tin cậy ngoài thực địa.

Sau đó dựng một đường thử có người hỗ trợ, vật mềm và vật cố định ở vị trí đã đo; ghi ảnh/matrix/câu đọc theo thời gian. Kiểm tra trái/phải, vật trên cao, vật thấp, nhiều vật, rút ToF, che camera và thay đổi ánh sáng. Kiểm tra lại sau mỗi lần đổi độ phân giải, crop camera, giá đỡ hoặc model. Thử sang đường bằng ảnh/video và mô hình sa bàn trước; chỉ từ màu đèn và vạch zebra chưa đủ điều kiện quyết định sang đường thực tế.
