import cv2
import queue
import threading
import time
import subprocess
import numpy as np
import sounddevice as sd
from ultralytics import YOLO
from rapidocr_onnxruntime import RapidOCR


class WindowsPiperTTS:
    """Phát trực tiếp luồng âm thanh Piper TTS ra LOA MÁY TÍNH qua sounddevice"""
    def __init__(self, model_path: str = "vi_VN-viterbi-medium.onnx", sample_rate: int = 22050):
        self.model_path = model_path
        self.sample_rate = sample_rate
        self.queue = queue.Queue(maxsize=2)
        self.thread = threading.Thread(target=self._run_tts, daemon=True)
        self.running = False

    def start(self):
        self.running = True
        self.thread.start()

    def stop(self):
        self.running = False
        self.queue.put(None)
        if self.thread.is_alive():
            self.thread.join()

    def speak(self, text: str, priority: bool = False):
        if priority:
            with self.queue.mutex:
                self.queue.queue.clear()
        try:
            self.queue.put_nowait(text)
        except queue.Full:
            pass

    def _run_tts(self):
        while self.running:
            text = self.queue.get()
            if text is None:
                break
            
            # Chạy piper.exe xuất luồng raw PCM
            cmd = [
                "piper.exe",
                "--model", self.model_path,
                "--output-raw"
            ]
            
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL
                )
                raw_audio, _ = proc.communicate(input=text.encode('utf-8'))

                if raw_audio:
                    # Chuyển raw bytes thành mảng numpy int16 và phát ra loa máy tính
                    audio_array = np.frombuffer(raw_audio, dtype=np.int16)
                    sd.play(audio_array, samplerate=self.sample_rate)
                    sd.wait() # Đợi phát xong âm thanh
            except Exception as e:
                print(f"[TTS Error] Không thể chạy piper.exe: {e}")

            self.queue.task_done()


class OCRWorker:
    def __init__(self, tts_manager: WindowsPiperTTS):
        self.tts = tts_manager
        self.queue = queue.Queue(maxsize=1)
        self.thread = threading.Thread(target=self._run_ocr, daemon=True)
        self.running = False

    def start(self):
        self.running = True
        self.thread.start()

    def stop(self):
        self.running = False
        self.queue.put(None)
        if self.thread.is_alive():
            self.thread.join()

    def request_ocr(self, frame):
        try:
            self.queue.put_nowait(frame.copy())
            print("\n-> [OCR] Đang xử lý ảnh (bằng phím A)...")
        except queue.Full:
            print("-> [OCR] Đang đọc câu trước, vui lòng đợi...")

    def _run_ocr(self):
        engine_ocr = RapidOCR(language='vi')
        while self.running:
            frame = self.queue.get()
            if frame is None:
                break
            result, _ = engine_ocr(frame)
            if result:
                full_text = " ".join([line[1] for line in result if line[2] > 0.5])
                ocr_msg = f"Đọc chữ: {full_text.strip()}"
            else:
                ocr_msg = "Không phát hiện chữ"

            self.tts.speak(ocr_msg, priority=True)
            self.queue.task_done()


class SmartBlindGlassesWinTest:
    def __init__(self, model_path: str = "yolov8n.pt", cam_id: int = 0):
        # Dùng model COCO mẫu có sẵn (yolov8n.pt)
        self.model = YOLO(model_path)
        self.cam_id = cam_id
        self.width = 640
        self.height = 360

        self.frame_lock = threading.Lock()
        self.current_frame = None
        self.annotated_frame = None
        self.running = False

        self.tof_lock = threading.Lock()
        self.tof_matrix = np.full((8, 8), 2.0)

        self.tts = WindowsPiperTTS(model_path="vi_VN-viterbi-medium.onnx")
        self.ocr = OCRWorker(self.tts)

        self.last_warning_time = 0.0
        self.warning_cooldown = 2.5

    def start(self):
        self.running = True
        self.tts.start()
        self.ocr.start()

        threading.Thread(target=self._stream_camera, daemon=True).start()
        threading.Thread(target=self._stream_tof_mock, daemon=True).start()
        threading.Thread(target=self._inference_yolo_loop, daemon=True).start()

        self._render_window_loop()

    def _stream_camera(self):
        cap = cv2.VideoCapture(self.cam_id)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        while self.running and cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                continue
            with self.frame_lock:
                self.current_frame = frame
            time.sleep(0.01)
        cap.release()

    def _stream_tof_mock(self):
        """Giả lập cảm biến ToF 8x8 bằng dữ liệu ngẫu nhiên"""
        while self.running:
            mock_matrix = np.random.uniform(0.5, 3.5, (8, 8))
            with self.tof_lock:
                self.tof_matrix = mock_matrix
            time.sleep(0.05)

    def _analyze_grid_and_tof(self, boxes):
        grid = {"trái": 0, "giữa": 0, "phải": 0}
        with self.tof_lock:
            local_tof = self.tof_matrix.copy()

        for box in boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            x_center = (x1 + x2) / 2
            
            tof_col = int(np.clip(x_center / (self.width / 8), 0, 7))
            tof_row = int(np.clip((y1 + y2) / 2 / (self.height / 8), 0, 7))
            
            if local_tof[tof_row, tof_col] > 2.5:
                continue 

            if x_center < self.width / 3:
                grid["trái"] += 1
            elif x_center < 2 * self.width / 3:
                grid["giữa"] += 1
            else:
                grid["phải"] += 1

        left_zone_dist = np.mean(local_tof[:, 0:3])
        center_zone_dist = np.mean(local_tof[:, 3:5])
        right_zone_dist = np.mean(local_tof[:, 5:8])

        if center_zone_dist < 1.3:
            if right_zone_dist > left_zone_dist and right_zone_dist > 1.8:
                return "Cảnh báo! Vật cản trực diện, hãy tránh sang bên phải"
            elif left_zone_dist > right_zone_dist and left_zone_dist > 1.8:
                return "Cảnh báo! Vật cản trực diện, hãy tránh sang bên trái"
            else:
                return "Nguy hiểm, phía trước tắc nghẽn"

        active = [k for k, v in grid.items() if v > 0]
        return f"Vật cản phía {' và '.join(active)}" if active else ""

    def _inference_yolo_loop(self):
        while self.running:
            frame = None
            with self.frame_lock:
                if self.current_frame is not None:
                    frame = self.current_frame.copy()

            if frame is None:
                time.sleep(0.01)
                continue

            # Suy luận model YOLOv8 mẫu
            results = self.model(frame, verbose=False)
            obstacle_boxes = []

            if len(results) > 0 and results[0].boxes is not None:
                for box in results[0].boxes:
                    obstacle_boxes.append(box)

            with self.tof_lock:
                min_center_dist = np.min(self.tof_matrix[:, 3:5])

            # Phân cấp ưu tiên cảnh báo
            if min_center_dist < 0.8:
                curr_time = time.time()
                if (curr_time - self.last_warning_time) > 1.5:
                    self.tts.speak("Cảnh báo khẩn cấp! Vật cản quá gần, dừng lại", priority=True)
                    self.last_warning_time = curr_time
            else:
                curr_time = time.time()
                if (curr_time - self.last_warning_time) > self.warning_cooldown:
                    warning_msg = self._analyze_grid_and_tof(obstacle_boxes)
                    if warning_msg:
                        self.tts.speak(warning_msg, priority=False)
                        self.last_warning_time = curr_time

            annotated = results[0].plot() if len(results) > 0 else frame
            with self.frame_lock:
                self.annotated_frame = annotated
            time.sleep(0.01)

    def _render_window_loop(self):
        print("\n=======================================================")
        print("-> ĐÃ KHỞI CHẠY MOCK TEST TRÊN WINDOWS!")
        print("-> Nhấn phím 'A' trên bàn phím để đọc OCR chữ.")
        print("-> Nhấn phím 'Q' để thoát chương trình.")
        print("=======================================================\n")

        cv2.namedWindow("Windows Camera Feed", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Windows Camera Feed", 960, 540)

        while self.running:
            display_frame = None
            raw_frame = None
            with self.frame_lock:
                if self.annotated_frame is not None:
                    display_frame = self.annotated_frame.copy()
                if self.current_frame is not None:
                    raw_frame = self.current_frame.copy()

            if display_frame is not None:
                cv2.imshow("Windows Camera Feed", display_frame)

            key = cv2.waitKey(16) & 0xFF
            if key == ord('q') or key == ord('Q'):
                break
            # Thay nút GPIO bằng phím 'A'
            elif (key == ord('a') or key == ord('A')) and raw_frame is not None:
                self.ocr.request_ocr(raw_frame)

        self.stop()

    def stop(self):
        self.running = False
        self.ocr.stop()
        self.tts.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    app = SmartBlindGlassesWinTest(model_path="yolov8n.pt")
    app.start()