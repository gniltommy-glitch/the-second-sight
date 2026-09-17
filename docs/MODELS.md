# Model, OCR tiếng Việt và bộ dữ liệu

Tài liệu này mô tả các adapter trong `assistive/models.py`. `README.md` hướng dẫn
chạy toàn hệ thống, camera, STM32 và tự khởi động. Không có model OCR hay giọng
Piper được tải ngầm khi bật thiết bị. Chuẩn bị và kiểm tra chúng trong bước cài đặt.

## 1. File `best.pt` hiện có thực sự nhận diện gì?

Đã đọc cấu trúc ZIP và opcode pickle bằng `pickletools`, không thực thi checkpoint
để suy đoán metadata:

| Thuộc tính | Giá trị |
| --- | --- |
| Kiến trúc lưu trong file | `yolo26n-seg.yaml`, head `Segment26` |
| Task | `segment` |
| Ultralytics dùng khi huấn luyện | `8.4.138` |
| Dung lượng file | 25.332.825 byte |
| SHA256 | `d1a850bbc55fe08fb2729d8671ec6267e0619d44a23e2ff53495f00f2a32ea39` |
| Classes | `building`, `cabinet`, `curb_obstacle`, `fence`, `person`, `pole`, `sign`, `street_obstacle`, `tree`, `vehicle` |

**Checkpoint này chưa có `sidewalk`, `road`, `zebra_crossing`, đèn người đi bộ hoặc
mệnh giá VND.** Đổi tên class trong config không tạo thêm khả năng nhận diện. Khi
dùng file hiện tại, có thể chạy cảnh báo vật cản; tính năng cần các class còn thiếu
phải đợi checkpoint được huấn luyện bổ sung. `curb_obstacle` là vật cản mép đường,
không đủ để coi một vùng ảnh là lối đi bộ an toàn.

Kiểm tra lại khi thay model, chạy từ thư mục gốc dự án:

```bash
python scripts/inspect_model.py best.pt
python scripts/inspect_model.py best.pt --datasets "My First Project.yolo26.zip" "Street Obstacle.v1i.yolov11.zip" "clean_dataset_p2.zip"
```

Kết quả đối với các file hiện có:

| Archive | Phát hiện |
| --- | --- |
| `My First Project.yolo26.zip` | 41 class, nhiều tên trùng/viết sai; 3.066 dòng polygon và 27 dòng box trong các label duy nhất |
| `Street Obstacle.v1i.yolov11.zip` | 12 class, có `road` và `sidewalk`; 2.050 dòng polygon; có tên file lặp trong archive |
| `clean_dataset_p2.zip` | Không thấy YAML hoặc dòng label YOLO dạng số; chưa đủ để dùng độc lập làm dataset huấn luyện |

Các con số là kiểm tra định dạng/metadata, không phải đánh giá độ chính xác. Script
chỉ khảo sát tối đa 10.000 dòng label mỗi archive và không giải nén hay chỉnh sửa dữ
liệu. Với ba archive này chưa chạm giới hạn đó.

## 2. Có nên gộp vào một `.pt`?

Nên bắt đầu bằng **một YOLO26n-seg** với bảng class thống nhất. Model segmentation
của Ultralytics xuất cả bounding box và mask; code dùng box để gọi tên/hướng vật
cản, dùng polygon cho lối đi, lòng đường và vạch qua đường. Vì vậy một checkpoint
đủ cho kiến trúc hiện tại khi dữ liệu huấn luyện phù hợp.
[Tài liệu segmentation chính thức](https://docs.ultralytics.com/tasks/segment).

Một checkpoint detection thường không tự có đầu ra segmentation. Cũng không thể
nối hai file `.pt` đã train riêng thành một model dùng chung chỉ bằng Python. Nếu
muốn mỗi nhóm class dùng một loại head khác nhau, cần kiến trúc và vòng train
multitask riêng; adapter hiện tại nhận model `detect` hoặc `segment` tiêu chuẩn.

Chuẩn bị dataset:

1. Thống nhất tên/index class giữa mọi nguồn. Chuẩn hóa `electrical_box`,
   `electrical box`, `eletrical box` thành một nhãn; kiểm tra nhãn vô nghĩa như `4`.
2. Với model seg, mọi đối tượng được train cần polygon đúng hình dạng. Dòng detection
   gồm `class cx cy w h` không phải polygon. Không trộn 5 cột và polygon rồi giả định
   trainer sẽ học đúng. Polygon hình chữ nhật chuyển từ box chỉ là nhãn xấp xỉ, đặc
   biệt không phù hợp để xác định vùng mặt đường có thể đi.
3. Bổ sung `sidewalk`, `road`, `zebra_crossing`, `pedestrian_red`,
   `pedestrian_green`, `pedestrian_unknown`; ghi nhãn đèn người đi bộ riêng với đèn
   cho xe. Giữ các vật thể tối thiểu mà checkpoint hiện có đã học.
4. Bổ sung tiền với class theo mệnh giá, ví dụ `vnd_10000`, `vnd_20000`,
   `vnd_50000`, `vnd_100000`, `vnd_200000`, `vnd_500000`; cần ảnh hai mặt, gấp,
   cũ, ánh sáng khác nhau. Đồng bộ tên với `navigation.money_classes` trong config.
5. Chia train/validation/test theo địa điểm và phiên quay; không để các frame gần
   nhau từ một video xuất hiện ở cả train và test. Đánh giá riêng đèn nhỏ, đường bị
   che và cảnh đêm. Không dùng điểm mAP gộp để kết luận việc qua đường đáng tin cậy.

Mask `zebra_crossing` và đèn xanh chỉ cung cấp bằng chứng thị giác. Code không được
coi chúng là xác nhận đường hết xe; xe rẽ, xe vượt đèn và phần đường khuất vẫn nằm
ngoài khả năng của một camera cùng ToF phạm vi gần.

## 3. YOLO trên Pi 5 RAM 4 GB

Cài trong virtualenv theo README:

```bash
python -m pip install -r requirements-models.txt
```

Pin `ultralytics==8.4.138` khớp phiên bản trong checkpoint. Dùng `.pt` trước để kiểm
tra toàn bộ chức năng. Sau đó thử NCNN, vốn được Ultralytics khuyến nghị cho ARM.
Kết quả benchmark YOLO detection công bố không đại diện cho model segmentation
10 class này hoặc cho tổng độ trễ camera → âm thanh.
[Hướng dẫn Raspberry Pi của Ultralytics](https://docs.ultralytics.com/guides/raspberry-pi).

Export trên máy phát triển hoặc Pi khi không chạy dịch vụ:

```bash
python -c "from ultralytics import YOLO; YOLO('best.pt').export(format='ncnn', imgsz=416, device='cpu')"
python -m pip install ncnn
```

Giữ nguyên toàn bộ thư mục `best_ncnn_model`, gồm `.param`, `.bin` và
`metadata.yaml`. Đặt `yolo.path` trỏ vào thư mục này và `yolo.imgsz=416`. Nếu export
dùng kích thước khác, phải đổi config tương ứng; NCNN export có kích thước cố định.
Adapter giới hạn số luồng CPU theo `yolo.threads`, mặc định 2. Đo với 2 rồi 3 luồng
trên Pi trong lúc camera, UART và TTS đang chạy.

Ảnh camera truyền vào adapter phải là BGR; output `bbox` và `polygon` chuẩn hóa
trong khoảng 0–1 theo ảnh gốc. Không đưa trực tiếp tọa độ ảnh letterbox vào ToF.
Không cần tải model lần đầu khi service chạy: thiếu file thì worker báo lỗi.

## 4. OCR tiếng Việt: chọn backend rõ ràng

| `ocr.backend` | Thành phần | Khi nào dùng |
| --- | --- | --- |
| `tesseract` | Tesseract 5 + `vie+eng` | Bản khởi đầu dễ cài ARM64, văn bản in tương đối rõ |
| `paddle` | PP-OCRv5 mobile detector + Latin mobile recognizer | Thử chất lượng chữ trong cảnh thật; cần Paddle CPU chạy được trên Pi |
| `paddle_vietocr` | Paddle mobile detector + VietOCR `vgg_seq2seq` | Khi mẫu tiếng Việt của nhóm tốt hơn với VietOCR; tải RAM và thời gian khởi tạo cao hơn |

Mặc định Tesseract là lựa chọn triển khai, không phải tuyên bố nó đọc cảnh thực tế
tốt hơn Paddle/VietOCR. Không có chuyển backend tự động: thiếu thư viện, dữ liệu
ngôn ngữ hoặc model sẽ trả lỗi rõ ràng. So sánh bằng cùng ảnh từ camera thật và
ghi lại lỗi chữ/dấu, thời gian, RAM trước khi chọn.

### Tesseract: chạy trước trên Pi

```bash
sudo apt install tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng
python -m pip install -r requirements-ocr.txt
tesseract --list-langs
```

Danh sách phải có `vie` và `eng`. [Danh sách ngôn ngữ chính thức của
Tesseract](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html)
có dữ liệu tiếng Việt. `ocr.tesseract_psm=6` phù hợp một khối văn bản như tờ giấy;
`11` thử cho chữ rời rạc/biển hiệu. Giữ giấy tương đối thẳng và đủ sáng; backend
này không tự hiểu bố cục phức tạp hoặc khôi phục dấu đã bị mờ.

```json
{
  "backend": "tesseract",
  "tesseract_lang": "vie+eng",
  "tesseract_psm": 6,
  "tesseract_timeout_s": 40,
  "confidence": 0.55,
  "region_mode": "main",
  "max_side": 1600,
  "max_lines": 40,
  "max_chars": 1800,
  "threads": 2
}
```

Đây là nội dung object `ocr`, không phải toàn bộ file config. Nếu cài Tesseract
trên Windows ngoài PATH, thêm `tesseract_cmd` là đường dẫn tuyệt đối tới executable.

### PaddleOCR mobile: chuẩn bị khi có mạng, chạy offline

Adapter viết theo API **PaddleOCR 3.3.2**, không dùng `ocr(..., cls=True)` của 2.x.
Chọn đích danh `PP-OCRv5_mobile_det` và `latin_PP-OCRv5_mobile_rec`; recognizer Latin
hỗ trợ tiếng Việt. Chỉ đặt `lang='vi'` có thể chọn detector server tốn tài nguyên
hơn. Tắt xoay tài liệu, unwarp, text orientation; batch nhận dạng bằng 1.
[Mã nguồn API v3.3.2 và ánh xạ tiếng Việt](https://github.com/PaddlePaddle/PaddleOCR/blob/v3.3.2/paddleocr/_pipelines/ocr.py).

**Phải kiểm tra wheel trên chính OS/Python của Pi.** Tài liệu Paddle bản
[tiếng Anh](https://www.paddlepaddle.org.cn/documentation/docs/en/install/index_en.html)
và [tiếng Trung](https://www.paddlepaddle.org.cn/documentation/docs/zh/install/index_cn.html)
chưa đồng nhất về ARM64 khi kiểm tra. Không coi việc pip cài được trên Windows
là bằng chứng chạy được trên Pi. Kiểm tra candidate CPU 3.3.0 mà không biên dịch
hay kéo toàn bộ dependency trước:

```bash
uname -m
python --version
mkdir -p wheelhouse
python -m pip download --only-binary=:all: --no-deps paddlepaddle==3.3.0 --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/ --dest wheelhouse
```

Nếu có wheel phù hợp `aarch64`, cài rồi kiểm tra thực tế:

```bash
python -m pip install paddlepaddle==3.3.0 --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/
python -m pip install paddleocr==3.3.2
python -c "import paddle; paddle.utils.run_check()"
```

Nếu không có wheel phù hợp hoặc `run_check` lỗi, tiếp tục dùng cấu hình Tesseract
đã kiểm tra; việc tự build Paddle/đổi backend ONNX là một nhánh triển khai riêng,
chưa được adapter này kiểm nghiệm trên Pi.

Tải hai inference model được liên kết trong [bảng model chính
thức](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html):

```bash
mkdir -p models/ocr
curl -fL 'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_det_infer.tar' -o models/ocr/det.tar
curl -fL 'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/latin_PP-OCRv5_mobile_rec_infer.tar' -o models/ocr/rec.tar
tar -tf models/ocr/det.tar
tar -tf models/ocr/rec.tar
tar -xf models/ocr/det.tar -C models/ocr
tar -xf models/ocr/rec.tar -C models/ocr
```

Trong config đặt `det_model_dir` và `rec_model_dir` tới hai thư mục thực tế vừa
giải nén. Mỗi thư mục cần `inference.yml`, `inference.pdiparams` và
`inference.json` hoặc `inference.pdmodel`. Ví dụ phần thêm vào object `ocr`:

```json
{
  "backend": "paddle",
  "det_model_name": "PP-OCRv5_mobile_det",
  "det_model_dir": "models/ocr/PP-OCRv5_mobile_det_infer",
  "rec_model_name": "latin_PP-OCRv5_mobile_rec",
  "rec_model_dir": "models/ocr/latin_PP-OCRv5_mobile_rec_infer",
  "detection_max_side": 960,
  "max_side": 1600,
  "confidence": 0.55,
  "threads": 2,
  "region_mode": "main",
  "max_lines": 40,
  "max_chars": 1800
}
```

### Paddle detector + VietOCR

VietOCR nhận dạng **ảnh một dòng chữ**; nó cần detector cắt dòng trước. Adapter
dùng tứ giác của Paddle, warp phối cảnh, đổi BGR sang RGB/PIL rồi gọi VietOCR;
lọc cả độ tin cậy detector và recognizer. Không chạy thêm recognizer Paddle trong
backend này. [VietOCR chính thức](https://github.com/pbcquoc/vietocr).

Sau khi Paddle đã chạy được, cài VietOCR trong môi trường thử nghiệm và kiểm tra
resolver không thay torch/torchvision đang hoạt động:

```bash
python -m pip install --dry-run vietocr==0.3.13
python -m pip install vietocr==0.3.13
```

Chuẩn bị file cấu hình và weights theo repo VietOCR trên máy có mạng. File
`vietocr_config` phải là YAML **đã gộp đầy đủ** `base.yml` + `vgg-seq2seq.yml`,
gồm `vocab`, `dataset`, `backbone`, `cnn`, `transformer`. Có thể chuẩn bị bằng
`Cfg.load_config_from_name('vgg_seq2seq')` rồi lưu bằng `yaml.safe_dump` trong bước
cài đặt có mạng; không gọi hàm này mỗi lần bấm nút. Tải weights chính thức từ URL
trong config, giữ file `.pth` cục bộ. Adapter ép `device='cpu'`,
`cnn.pretrained=False`, `predictor.beamsearch=False`, và thay `weights` bằng đường
dẫn cục bộ đã xác nhận tồn tại.

Thêm/đổi trong object `ocr`:

```json
{
  "backend": "paddle_vietocr",
  "det_model_name": "PP-OCRv5_mobile_det",
  "det_model_dir": "models/ocr/PP-OCRv5_mobile_det_infer",
  "vietocr_config": "models/ocr/vgg_seq2seq_full.yml",
  "vietocr_weights": "models/ocr/vgg_seq2seq.pth",
  "threads": 2,
  "confidence": 0.55,
  "max_lines": 40
}
```

## 5. Văn bản chính và quản lý RAM

Pipeline lọc dòng confidence thấp, chuẩn hóa Unicode NFC, giữ nguyên dấu tiếng
Việt, gộp các dòng gần nhau thành vùng và ưu tiên khối có nhiều chữ, lớn, gần giữa
ảnh. Sau đó sắp theo hàng từ trên xuống, trái sang phải. `region_mode='all'` đọc
mọi dòng đủ tin cậy; `main` chỉ là heuristic hình học và có thể chọn nhầm biển
hiệu phụ. Chưa có mô hình hiểu ý hoặc tóm tắt; code không tự bịa/thêm từ để sửa OCR.
Với trang nhiều cột, bảng biểu, chữ dọc, cần module layout riêng nếu muốn thứ tự
đọc chính xác hơn.

YOLO và OCR chỉ được tạo trong worker process. `.close()` xóa tham chiếu Python;
việc worker kết thúc và parent `join()` mới thu hồi toàn bộ bộ nhớ native của
Torch/Paddle. Không dựa vào `gc.collect()` hoặc gọi `torch.cuda.empty_cache()` để
giải phóng RAM CPU. TTS chạy riêng và phải kết thúc đọc nội dung trước khi trở lại
điều hướng theo state machine chính.

Đo trên Pi ít nhất: độ trễ nạp YOLO, một frame YOLO, nạp OCR, OCR một ảnh, thời gian
TTS, thời gian phát xong, RSS lớn nhất trong 100 lần chuyển chế độ. Không suy ra
RAM thực tế từ dung lượng `.pt`/`.onnx`; không suy ra FPS trên Pi từ smoke test
Windows. Bộ unit test văn bản không thay thế kiểm thử model trên camera thật.
