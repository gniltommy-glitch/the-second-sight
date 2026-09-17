#include <iostream>
#include <vector>
#include <string>
#include <queue>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <chrono>
#include <random>
#include <algorithm>
#include <unordered_map>
#include <opencv2/opencv.hpp>
#include <onnxruntime_cxx_api.h>

using namespace std;
using namespace cv;

// Struct định nghĩa đối tượng AI phát hiện được
struct DetectedObject {
    Rect2f bbox;
    int class_id;
    float confidence;
    string class_name;
};

// ============================================================================
// 1. CLASS: TTSManager (Quản lý âm thanh giọng nói)
// ============================================================================
class TTSManager {
private:
    string model_path;
    queue<string> msg_queue;
    mutex queue_mutex;
    condition_variable cv;
    thread tts_thread;
    atomic<bool> running{false};

    void run_loop() {
        while (running) {
            string text;
            {
                unique_lock<mutex> lock(queue_mutex);
                cv.wait(lock, [this] { return !msg_queue.empty() || !running; });
                if (!running && msg_queue.empty()) break;
                
                text = msg_queue.front();
                msg_queue.pop();
            }

            if (!text.empty()) {
                string cmd = "echo \"" + text + "\" | piper --model " + model_path + 
                             " --output-raw | aplay -r 22050 -f S16_LE -t raw -q > /dev/null 2>&1";
                system(cmd.c_str());
            }
        }
    }

public:
    explicit TTSManager(string model = "vi_VN-viterbi-medium.onnx") 
        : model_path(move(model)) {}

    ~TTSManager() { stop(); }

    void start() {
        running = true;
        tts_thread = thread(&TTSManager::run_loop, this);
    }

    void stop() {
        if (running) {
            running = false;
            cv.notify_all();
            if (tts_thread.joinable()) tts_thread.join();
        }
    }

    void speak(const string& text, bool priority = false) {
        lock_guard<mutex> lock(queue_mutex);
        if (priority) {
            queue<string> empty;
            swap(msg_queue, empty);
        }
        if (msg_queue.size() < 2) {
            msg_queue.push(text);
            cv.notify_one();
        }
    }
};

// ============================================================================
// 2. CLASS: OCRWorker (Quản lý luồng đọc chữ RapidOCR)
// ============================================================================
class OCRWorker {
private:
    TTSManager& tts;
    queue<Mat> frame_queue;
    mutex queue_mutex;
    condition_variable cv;
    thread ocr_thread;
    atomic<bool> running{false};

    void run_loop() {
        while (running) {
            Mat frame;
            {
                unique_lock<mutex> lock(queue_mutex);
                cv.wait(lock, [this] { return !frame_queue.empty() || !running; });
                if (!running && frame_queue.empty()) break;

                frame = frame_queue.front();
                frame_queue.pop();
            }

            if (!frame.empty()) {
                // Tích hợp RapidOCR-CPP C++ Native Engine tại đây
                bool found_text = false; 
                string ocr_msg = found_text ? "Đọc chữ: Văn bản đã xử lý" : "Không phát hiện chữ";
                tts.speak(ocr_msg, true);
            }
        }
    }

public:
    explicit OCRWorker(TTSManager& tts_mgr) : tts(tts_mgr) {}
    ~OCRWorker() { stop(); }

    void start() {
        running = true;
        ocr_thread = thread(&OCRWorker::run_loop, this);
    }

    void stop() {
        if (running) {
            running = false;
            cv.notify_all();
            if (ocr_thread.joinable()) ocr_thread.join();
        }
    }

    void request_ocr(const Mat& frame) {
        lock_guard<mutex> lock(queue_mutex);
        if (frame_queue.empty()) {
            frame_queue.push(frame.clone());
            cout << "-> [OCR] Đã chụp ảnh, đang xử lý RapidOCR C++..." << endl;
            cv.notify_one();
        } else {
            cout << "-> [OCR] Hệ thống đang ưu tiên đọc văn bản trước" << endl;
        }
    }
};

// ============================================================================
// 3. CLASS: ToFSensor (Quản lý phần cứng cảm biến khoảng cách 8x8)
// ============================================================================
class ToFSensor {
private:
    vector<vector<float>> matrix;
    mutex mtx;
    thread stream_thread;
    atomic<bool> running{false};

    void update_loop() {
        default_random_engine generator;
        uniform_real_distribution<float> dist(0.5f, 3.5f);

        while (running) {
            vector<vector<float>> mock(8, vector<float>(8));
            for (int r = 0; r < 8; ++r) {
                for (int c = 0; c < 8; ++c) {
                    mock[r][c] = dist(generator);
                }
            }
            {
                lock_guard<mutex> lock(mtx);
                matrix = mock;
            }
            this_thread::sleep_for(chrono::milliseconds(50));
        }
    }

public:
    ToFSensor() {
        matrix = vector<vector<float>>(8, vector<float>(8, 2.0f));
    }
    ~ToFSensor() { stop(); }

    void start() {
        running = true;
        stream_thread = thread(&ToFSensor::update_loop, this);
    }

    void stop() {
        if (running) {
            running = false;
            if (stream_thread.joinable()) stream_thread.join();
        }
    }

    vector<vector<float>> get_matrix() {
        lock_guard<mutex> lock(mtx);
        return matrix;
    }
};

// ============================================================================
// 4. CLASS: YOLOInference (Wrapper đóng gói ONNX Runtime C++ SDK)
// ============================================================================
class YOLOInference {
private:
    Ort::Env env{ORT_LOGGING_LEVEL_WARNING, "YOLO"};
    Ort::SessionOptions session_options;
    unique_ptr<Ort::Session> session;
    int img_size = 320;

public:
    explicit YOLOInference(const string& model_path) {
        session_options.SetIntraOpNumThreads(4); // Tối ưu 4 nhân CPU trên Pi 5
        session_options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
        session = make_unique<Ort::Session>(env, model_path.c_str(), session_options);
    }

    vector<DetectedObject> detect(const Mat& frame) {
        Mat resized, blob;
        cv::resize(frame, resized, Size(img_size, img_size));
        resized.convertTo(blob, CV_32F, 1.0 / 255.0);

        vector<float> input_tensor_values(1 * 3 * img_size * img_size);
        vector<Mat> channels(3);
        for (int i = 0; i < 3; ++i) {
            channels[i] = Mat(img_size, img_size, CV_32FC1, input_tensor_values.data() + i * img_size * img_size);
        }
        split(blob, channels);

        vector<int64_t> input_shape = {1, 3, img_size, img_size};
        auto memory_info = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
        Ort::Value input_tensor = Ort::Value::CreateTensor<float>(
            memory_info, input_tensor_values.data(), input_tensor_values.size(),
            input_shape.data(), input_shape.size()
        );

        Ort::AllocatorWithDefaultOptions allocator;
        auto input_name = session->GetInputNameAllocated(0, allocator);
        auto output_name = session->GetOutputNameAllocated(0, allocator);

        const char* input_names[] = { input_name.get() };
        const char* output_names[] = { output_name.get() };

        auto output_tensors = session->Run(Ort::RunOptions{nullptr}, input_names, &input_tensor, 1, output_names, 1);
        
        vector<DetectedObject> results;
        return results;
    }
};

// ============================================================================
// 5. CLASS: SmartGlassesPipeline (Controller Trung Tâm)
// ============================================================================
class SmartGlassesPipeline {
private:
    int width = 640;
    int height = 360;

    unordered_map<string, string> CURRENCY_MAP = {
        {"10k", "Mười nghìn"}, {"20k", "Hai mươi nghìn"}, {"50k", "Năm mươi nghìn"},
        {"100k", "Một trăm nghìn"}, {"200k", "Hai trăm nghìn"}, {"500k", "Năm trăm nghìn"}
    };

    TTSManager tts;
    OCRWorker ocr;
    ToFSensor tof;
    YOLOInference yolo;

    Mat current_frame;
    Mat annotated_frame;
    mutex frame_mutex;
    atomic<bool> running{false};

    double last_warning_time = 0.0;
    double warning_cooldown = 2.5;
    string last_money = "";

    double get_sec() {
        using namespace std::chrono;
        return duration_cast<duration<double>>(steady_clock::now().time_since_epoch()).count();
    }

    string analyze_grid_and_tof(const vector<DetectedObject>& boxes) {
        unordered_map<string, int> grid = {
            {"trái trên", 0}, {"phải trên", 0}, {"trái giữa", 0},
            {"phải giữa", 0}, {"trái dưới", 0}, {"phải dưới", 0}
        };
        int total_obstacles = 0;
        auto local_tof = tof.get_matrix();

        for (const auto& obj : boxes) {
            float x_center = obj.bbox.x + obj.bbox.width / 2.0f;
            float y_center = obj.bbox.y + obj.bbox.height / 2.0f;

            int col = clamp(static_cast<int>(x_center / (width / 8.0f)), 0, 7);
            int row = clamp(static_cast<int>(y_center / (height / 8.0f)), 0, 7);

            if (local_tof[row][col] > 2.5f) continue;

            string pos_y = (y_center < height / 3.0f) ? "trên" : (y_center < 2.0f * height / 3.0f) ? "giữa" : "dưới";
            string pos_x = (x_center < width / 2.0f) ? "trái" : "phải";

            grid[pos_x + " " + pos_y]++;
            total_obstacles++;
        }

        if (total_obstacles == 0) return "";

        float left_sum = 0, center_sum = 0, right_sum = 0;
        for (int r = 0; r < 8; ++r) {
            for (int c = 0; c < 3; ++c) left_sum += local_tof[r][c];
            for (int c = 3; c < 5; ++c) center_sum += local_tof[r][c];
            for (int c = 5; c < 8; ++c) right_sum += local_tof[r][c];
        }

        if ((center_sum / 16.0f) < 1.3f) {
            if ((right_sum / 24.0f) > (left_sum / 24.0f) && (right_sum / 24.0f) > 1.8f) {
                return "Cảnh báo! Vật cản trực diện, hãy tránh sang bên phải";
            } else if ((left_sum / 24.0f) > (right_sum / 24.0f) && (left_sum / 24.0f) > 1.8f) {
                return "Cảnh báo! Vật cản trực diện, hãy tránh sang bên trái";
            } else {
                return "Nguy hiểm, phía trước tắc nghẽn, hãy đi chậm lại";
            }
        }

        if (total_obstacles >= 4) return "Phía trước nhiều vật cản phức tạp, hãy đi chậm";

        vector<string> regions;
        for (const auto& [k, v] : grid) if (v >= 1) regions.push_back(k);

        if (!regions.empty()) {
            string msg = "Vật cản phía ";
            for (size_t i = 0; i < regions.size(); ++i) {
                msg += regions[i] + (i == regions.size() - 1 ? "" : " và ");
            }
            return msg;
        }
        return "";
    }

    void stream_camera() {
        VideoCapture cap(0, CAP_V4L2);
        cap.set(CAP_PROP_FRAME_WIDTH, width);
        cap.set(CAP_PROP_FRAME_HEIGHT, height);
        cap.set(CAP_PROP_FOURCC, VideoWriter::fourcc('M', 'J', 'P', 'G'));

        Mat raw, fixed;
        while (running) {
            cap >> raw;
            if (raw.empty()) continue;
            cv::flip(raw, fixed, -1);
            {
                lock_guard<mutex> lock(frame_mutex);
                current_frame = fixed.clone();
            }
            this_thread::sleep_for(chrono::milliseconds(10));
        }
    }

    void inference_loop() {
        while (running) {
            Mat frame;
            {
                lock_guard<mutex> lock(frame_mutex);
                if (!current_frame.empty()) frame = current_frame.clone();
            }

            if (frame.empty()) {
                this_thread::sleep_for(chrono::milliseconds(10));
                continue;
            }

            vector<DetectedObject> objects = yolo.detect(frame);
            vector<string> detected_money;
            vector<DetectedObject> obstacles;

            for (const auto& obj : objects) {
                if (CURRENCY_MAP.count(obj.class_name)) {
                    detected_money.push_back(CURRENCY_MAP[obj.class_name]);
                } else {
                    obstacles.push_back(obj);
                }
            }

            if (!detected_money.empty()) {
                if (detected_money[0] != last_money) {
                    tts.speak("Tờ tiền " + detected_money[0], true);
                    last_money = detected_money[0];
                }
            } else {
                last_money = "";
                double now = get_sec();
                if ((now - last_warning_time) > warning_cooldown) {
                    string warn = analyze_grid_and_tof(obstacles);
                    if (!warn.empty()) {
                        tts.speak(warn, false);
                        last_warning_time = now;
                    }
                }
            }

            {
                lock_guard<mutex> lock(frame_mutex);
                annotated_frame = frame.clone();
            }
            this_thread::sleep_for(chrono::milliseconds(10));
        }
    }

public:
    explicit SmartGlassesPipeline(const string& model_path)
        : tts("vi_VN-viterbi-medium.onnx"), ocr(tts), yolo(model_path) {}

    ~SmartGlassesPipeline() { stop(); }

    void start() {
        running = true;
        tts.start();
        ocr.start();
        tof.start();

        thread(&SmartGlassesPipeline::stream_camera, this).detach();
        thread(&SmartGlassesPipeline::inference_loop, this).detach();

        cout << "-> [System] Kính thông minh C++ OOP đã sẵn sàng! Bấm 'A' để đọc chữ, 'Q' để thoát." << endl;
        namedWindow("Smart Glasses OOP", WINDOW_NORMAL);
        resizeWindow("Smart Glasses OOP", 1280, 720);

        while (running) {
            Mat display, raw;
            {
                lock_guard<mutex> lock(frame_mutex);
                if (!annotated_frame.empty()) display = annotated_frame.clone();
                if (!current_frame.empty()) raw = current_frame.clone();
            }

            if (!display.empty()) imshow("Smart Glasses OOP", display);

            int key = waitKey(16) & 0xFF;
            if (key == 'q' || key == 'Q') break;
            if ((key == 'a' || key == 'A') && !raw.empty()) ocr.request_ocr(raw);
        }
        stop();
    }

    void stop() {
        if (running) {
            running = false;
            tof.stop();
            ocr.stop();
            tts.stop();
            destroyAllWindows();
            cout << "-> Đã đóng toàn bộ ứng dụng C++ OOP an toàn." << endl;
        }
    }
};

// ============================================================================
// MAIN ENTRY POINT
// ============================================================================
int main() {
    SmartGlassesPipeline app("yolo26n.onnx");
    app.start();
    return 0;
}