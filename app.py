# coding=utf-8
from flask import Flask, render_template, Response, jsonify, request, send_from_directory
import cv2
import numpy as np
import mvsdk
import platform
import threading
import time
import requests
import base64
import sqlite3
import os
from datetime import datetime
from ultralytics import YOLO
from typing import Optional

# ====================== 初始化Flask应用 ======================
app = Flask(__name__)

# ====================== 项目核心配置 ======================
# 塔石物联云API配置
USERNAME = "2735280119@qq.com"
PASSWORD = "uizgzw123.."
API_KEY = "fed68af28ea1465cbcd42cc33dfd5e3b"
USER_ID = "123381"
DEVICE_NO = "172XA9U5JXA418G4"
SENSOR_ID_DO1 = "6262596"
SENSOR_ID_DO2 = "6262597"
CLIENT_ID = "fed68af28ea1465cbcd42cc33dfd5e3b"
CLIENT_SECRET = "151a0abac8b64a19b11cde8ffca8d243"
TRIGGER_ON_VALUE = 1
TRIGGER_OFF_VALUE = 0
TRIGGER_COOLDOWN = 30
API_RETRY_COUNT = 2
API_TIMEOUT = 5

# YOLO识别配置
MODEL_PATH = "yolov8m.pt"          # 修改：使用yolov8m模型
SEABIRD_CLASS_ID = 14
CONF_THRESHOLD = 0.3
FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080
DETECT_INTERVAL = 20               # 修改：每20帧识别一次

# ====================== 路径配置 ======================
DB_PATH = "haiyan_data.db"
CAPTURE_FOLDER = r"E:\海眼系统\captures"  # 修改为绝对路径
# 确保文件夹存在
if not os.path.exists(CAPTURE_FOLDER):
    os.makedirs(CAPTURE_FOLDER)

# ====================== 数据库初始化 ======================
def init_db():
    """初始化数据库，创建表"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 驱鸟记录表
    c.execute('''CREATE TABLE IF NOT EXISTS repellent_records
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL,
                  bird_count INTEGER, max_conf REAL, success INTEGER)''')

    # 设备状态日志表
    c.execute('''CREATE TABLE IF NOT EXISTS device_status_log
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL,
                  total_switch INTEGER, do1_status INTEGER, do2_status INTEGER)''')

    # 【新增】系统异常预警表
    c.execute('''CREATE TABLE IF NOT EXISTS system_alerts
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL,
                  alert_type TEXT, alert_level TEXT, alert_content TEXT, is_read INTEGER DEFAULT 0)''')

    # 【新增】海鸟识别坐标明细表
    c.execute('''CREATE TABLE IF NOT EXISTS bird_detections
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, repellent_id INTEGER,
                  time TEXT, bird_index INTEGER, x1 REAL, y1 REAL, x2 REAL, y2 REAL,
                  confidence REAL, frame_width INTEGER, frame_height INTEGER,
                  FOREIGN KEY (repellent_id) REFERENCES repellent_records(id))''')

    conn.commit()
    conn.close()
    print("✅ 数据库初始化成功")

# 启动时初始化数据库
init_db()

# ====================== 全局状态&线程锁 ======================
# 硬件状态
global_total_switch = False  # 总开关：True=开启相机/识别/驱鸟，False=全部停止
global_do1_status = False    # DO1自传巡航
global_do2_status = False    # DO2驱鸟执行

# 驱鸟防重复触发
LAST_TRIGGER_TIME = 0
IS_EXECUTING = False

# 驱鸟记录统计
repellent_count = 0          # 驱鸟累计次数
repellent_history = []       # 驱鸟历史记录列表

# 【新增】预警相关
alert_history = []          # 内存中的预警记录
LAST_ALERT_TIME = {}         # 上次预警时间，用于去重冷却

# 【新增】最新一次识别信息，用于前端提示
last_detection_info = {
    "time": "",
    "bird_count": 0,
    "max_conf": 0.0
}

# 相机&模型全局变量
hCamera = 0
cap = None
pFrameBuffer = 0
monoCamera = False
model = None

# 最新帧缓存（用于Web视频流推送）
latest_frame = None

# 线程锁
status_lock = threading.Lock()
frame_lock = threading.Lock()

# 全局认证凭证
GLOBAL_ACCESS_TOKEN: Optional[str] = None
TOKEN_EXPIRE_TIME: int = 0

# ====================== 塔石云API核心类 ======================
class TashiCloudAPI:
    def __init__(self):
        self.token_url = "https://app.dtuip.com/oauth/token"
        self.switch_url = "https://app.dtuip.com/api/device/switcherController"
        self.username = USERNAME
        self.password = PASSWORD
        self.api_key = API_KEY
        self.user_id = USER_ID
        self.device_no = DEVICE_NO

    def get_access_token(self) -> bool:
        global GLOBAL_ACCESS_TOKEN, TOKEN_EXPIRE_TIME
        try:
            print("🔐 正在获取塔石云Token...")
            auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
            auth_b64 = base64.b64encode(auth_str.encode()).decode()
            headers = {"authorization": f"Basic {auth_b64}"}
            params = {"grant_type": "password", "username": self.username, "password": self.password}
            resp_token = requests.post(self.token_url, headers=headers, params=params, timeout=API_TIMEOUT)
            token_result = resp_token.json()
            access_token = token_result.get("access_token")
            if not access_token:
                print("❌ 获取Token失败，请检查账号密码", token_result)
                return False
            GLOBAL_ACCESS_TOKEN = access_token
            expire_in = token_result.get("expires_in", 7200)
            TOKEN_EXPIRE_TIME = int(time.time()) + expire_in - 600
            print("✅ Token获取成功")
            return True
        except Exception as e:
            print(f"❌ [塔石云] Token接口异常：{str(e)}")
            return False

    def _check_token_valid(self) -> bool:
        global GLOBAL_ACCESS_TOKEN
        if not GLOBAL_ACCESS_TOKEN or int(time.time()) >= TOKEN_EXPIRE_TIME:
            return self.get_access_token()
        return True

    def control_switch(self, sensor_id: str, switch_value: int) -> bool:
        if not self._check_token_valid():
            return False
        for retry in range(API_RETRY_COUNT + 1):
            try:
                status = "开" if switch_value == 1 else "关"
                sensor_name = "DO1" if sensor_id == SENSOR_ID_DO1 else "DO2"
                print(f"[塔石云] 正在给{sensor_name}发送{status}指令，第{retry + 1}次尝试...")
                headers = {
                    "Authorization": f"Bearer {GLOBAL_ACCESS_TOKEN}",
                    "tlinkAppId": self.api_key,
                    "Content-Type": "application/json"
                }
                data = {
                    "userId": self.user_id,
                    "deviceNo": self.device_no,
                    "sensorId": sensor_id,
                    "switcher": switch_value
                }
                resp = requests.post(self.switch_url, headers=headers, json=data, timeout=API_TIMEOUT)
                result = resp.json()
                if result.get("flag") == "00":
                    print(f"✅ 传感器{sensor_id}（{sensor_name}） 已{status}")
                    return True
                else:
                    print(f"❌ 控制失败 {sensor_id}:", result)
            except Exception as e:
                print(f"❌ [塔石云] 指令接口异常：{str(e)}")
                time.sleep(0.2)
        print(f"❌ [塔石云] 指令发送失败，已重试{API_RETRY_COUNT}次")
        return False

# 初始化塔石云实例
tashi_api = TashiCloudAPI()

# ====================== 【新增】图片保存函数 ======================
def save_detect_frame(frame, record_id):
    """保存带识别框的画面"""
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_capture_folder = os.path.join(CAPTURE_FOLDER, today_str)
        if not os.path.exists(today_capture_folder):
            os.makedirs(today_capture_folder)

        time_str = datetime.now().strftime("%H%M%S")
        file_name = f"repellent_{record_id}_{time_str}.jpg"
        relative_path = os.path.join(today_str, file_name)
        full_save_path = os.path.join(CAPTURE_FOLDER, relative_path)

        cv2.imwrite(full_save_path, frame)
        print(f"📸 识别画面已保存：{full_save_path}")
        return relative_path
    except Exception as e:
        print(f"❌ 画面保存失败：{str(e)}")
        return None

# ====================== 【新增】预警相关函数 ======================
ALERT_COOLDOWN = 300  # 5分钟内同类型预警不重复

def check_and_trigger_alert(alert_type, alert_level, alert_content):
    """检查是否触发预警，需冷却时间"""
    global LAST_ALERT_TIME, alert_history
    current_time = time.time()
    alert_key = f"{alert_type}"

    # 检查冷却时间
    if alert_key in LAST_ALERT_TIME:
        if current_time - LAST_ALERT_TIME[alert_key] < ALERT_COOLDOWN:
            return  # 冷却中，不重复预警

    # 记录预警时间
    LAST_ALERT_TIME[alert_key] = current_time
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 写入数据库
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO system_alerts (time, alert_type, alert_level, alert_content, is_read) VALUES (?, ?, ?, ?, 0)",
                  (current_time_str, alert_type, alert_level, alert_content))
        alert_id = c.lastrowid
        conn.commit()
        conn.close()

        # 加入内存列表
        alert_history.insert(0, {
            "id": alert_id,
            "time": current_time_str,
            "alert_type": alert_type,
            "alert_level": alert_level,
            "alert_content": alert_content,
            "is_read": 0
        })
        if len(alert_history) > 100:
            alert_history = alert_history[:100]

        # 打印预警（同时也是小凯通知你的方式）
        level_icon = "🔴" if alert_level == "emergency" else "⚠️"
        print(f"{level_icon} 【预警触发】{alert_type} - {alert_content}")

    except Exception as e:
        print(f"❌ 预警记录失败：{str(e)}")

# ====================== 驱鸟触发逻辑（自动识别触发） ======================
def trigger_bird_repellent(bird_count, max_conf, frame_with_detect, detections_info):
    global LAST_TRIGGER_TIME, IS_EXECUTING, repellent_count, repellent_history, last_detection_info
    current_time = time.time()

    # 冷却时间+执行锁
    if current_time - LAST_TRIGGER_TIME < TRIGGER_COOLDOWN:
        print(f"[DO2冷却中] 距离上次触发不足{TRIGGER_COOLDOWN}秒，暂不重复触发")
        return
    if IS_EXECUTING:
        print(f"[执行中] 驱鸟动作正在执行中，暂不重复触发")
        return

    # 锁定触发
    LAST_TRIGGER_TIME = current_time
    IS_EXECUTING = True
    print(f"🔒 已锁定触发，冷却{TRIGGER_COOLDOWN}秒")

    # 记录驱鸟事件到数据库
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO repellent_records (time, bird_count, max_conf, success) VALUES (?, ?, ?, 1)",
                  (current_time_str, bird_count, max_conf))
        record_id = c.lastrowid
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ 数据库写入失败：{str(e)}")
        record_id = None

    # 【新增】更新最新识别信息（用于前端提示）
    with status_lock:
        last_detection_info["time"] = current_time_str
        last_detection_info["bird_count"] = bird_count
        last_detection_info["max_conf"] = max_conf

    # 【新增】保存带识别框的画面
    if record_id and frame_with_detect is not None:
        image_path = save_detect_frame(frame_with_detect, record_id)

    # 【新增】记录每只海鸟的坐标
    if record_id and detections_info:
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            for det in detections_info:
                c.execute("""INSERT INTO bird_detections
                             (repellent_id, time, bird_index, x1, y1, x2, y2, confidence, frame_width, frame_height)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (record_id, current_time_str, det['index'], det['x1'], det['y1'],
                           det['x2'], det['y2'], det['conf'], FRAME_WIDTH, FRAME_HEIGHT))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ 海鸟坐标记录失败：{str(e)}")

    # 【新增】检查是否触发预警
    # 1. 海鸟集群预警
    if bird_count >= 5:
        check_and_trigger_alert("海鸟集群", "warning", f"检测到{bird_count}只海鸟集群入侵！")

    # 2. 高频驱鸟预警（10分钟内>=5次）
    recent_count = sum(1 for t, _ in repellent_history if current_time - time.mktime(time.strptime(t, "%Y-%m-%d %H:%M:%S")) < 600)
    if recent_count >= 5:
        check_and_trigger_alert("高频驱鸟", "warning", f"10分钟内已触发{recent_count}次驱鸟，可能有鸟群持续入侵")

    # 更新内存统计
    with status_lock:
        repellent_count += 1
        repellent_history.insert(0, (current_time_str, repellent_count))
        if len(repellent_history) > 1000:
            repellent_history = repellent_history[:1000]
        print(f"📊 驱鸟记录已更新：累计 {repellent_count} 次")

    # 异步执行驱鸟动作
    def _async_trigger():
        global IS_EXECUTING
        try:
            def control_do1():
                with status_lock:
                    global global_do1_status
                    print("[DO1控制] 发送关闭指令...")
                    tashi_api.control_switch(SENSOR_ID_DO1, TRIGGER_OFF_VALUE)
                    global_do1_status = False
                    print("[DO1控制] 已关闭，等待10秒...")
                time.sleep(10)
                with status_lock:
                    print("[DO1控制] 10秒到，发送启动指令...")
                    tashi_api.control_switch(SENSOR_ID_DO1, TRIGGER_ON_VALUE)
                    global_do1_status = True
                    print("[DO1控制] 已恢复启动")

            def control_do2():
                with status_lock:
                    global global_do2_status
                    print("[DO2控制] 发送启动指令...")
                    tashi_api.control_switch(SENSOR_ID_DO2, TRIGGER_ON_VALUE)
                    global_do2_status = True
                    print("[DO2控制] 已启动，等待15秒...")
                time.sleep(15)
                with status_lock:
                    print("[DO2控制] 15秒到，发送关闭指令...")
                    tashi_api.control_switch(SENSOR_ID_DO2, TRIGGER_OFF_VALUE)
                    global_do2_status = False
                    print("[DO2控制] 已关闭")

            thread_do1 = threading.Thread(target=control_do1, daemon=True)
            thread_do2 = threading.Thread(target=control_do2, daemon=True)
            thread_do1.start()
            thread_do2.start()
            thread_do1.join()
            thread_do2.join()
        finally:
            IS_EXECUTING = False
            print(f"🔓 执行标志位已释放")
    threading.Thread(target=_async_trigger, daemon=True).start()

# ====================== 相机&识别核心线程 ======================
def camera_capture_thread():
    global hCamera, cap, pFrameBuffer, monoCamera, latest_frame, global_total_switch
    frame_count = 0
    last_detect_results = None
    camera_error_count = 0

    while True:
        with status_lock:
            if not global_total_switch:
                if hCamera != 0:
                    mvsdk.CameraUnInit(hCamera)
                    mvsdk.CameraAlignFree(pFrameBuffer)
                    hCamera = 0
                    pFrameBuffer = 0
                    print("✅ 相机资源已释放")
                with frame_lock:
                    latest_frame = None
                time.sleep(0.2)
                continue

        if hCamera == 0:
            try:
                DevList = mvsdk.CameraEnumerateDevice()
                if len(DevList) < 1:
                    print("错误：未找到迈德威视相机！")
                    with status_lock:
                        global_total_switch = False
                    time.sleep(1)
                    continue
                DevInfo = DevList[0]
                print(f"已选择相机：{DevInfo.GetFriendlyName()}")
                hCamera = mvsdk.CameraInit(DevInfo, -1, -1)
                cap = mvsdk.CameraGetCapability(hCamera)
                monoCamera = (cap.sIspCapacity.bMonoSensor != 0)
                if monoCamera:
                    mvsdk.CameraSetIspOutFormat(hCamera, mvsdk.CAMERA_MEDIA_TYPE_MONO8)
                else:
                    mvsdk.CameraSetIspOutFormat(hCamera, mvsdk.CAMERA_MEDIA_TYPE_BGR8)
                mvsdk.CameraSetTriggerMode(hCamera, 0)
                mvsdk.CameraSetAeState(hCamera, 0)
                mvsdk.CameraSetExposureTime(hCamera, 30 * 1000)
                mvsdk.CameraPlay(hCamera)
                FrameBufferSize = cap.sResolutionRange.iWidthMax * cap.sResolutionRange.iHeightMax * (1 if monoCamera else 3)
                pFrameBuffer = mvsdk.CameraAlignMalloc(FrameBufferSize, 16)
                print("✅ 迈德威视相机初始化成功")

                global model
                if model is None:
                    print(f"正在加载YOLO模型 {MODEL_PATH}...")
                    model = YOLO(MODEL_PATH)
                    print("✅ YOLO模型加载成功")

                tashi_api.get_access_token()
                camera_error_count = 0

            except Exception as e:
                print(f"❌ 相机/模型初始化失败：{str(e)}")
                with status_lock:
                    global_total_switch = False
                if hCamera != 0:
                    mvsdk.CameraUnInit(hCamera)
                    mvsdk.CameraAlignFree(pFrameBuffer)
                    hCamera = 0
                    pFrameBuffer = 0
                time.sleep(1)
                continue

        try:
            pRawData, FrameHead = mvsdk.CameraGetImageBuffer(hCamera, 200)
            mvsdk.CameraImageProcess(hCamera, pRawData, pFrameBuffer, FrameHead)
            mvsdk.CameraReleaseImageBuffer(hCamera, pRawData)
            if platform.system() == "Windows":
                mvsdk.CameraFlipFrameBuffer(pFrameBuffer, FrameHead, 1)
            frame_data = (mvsdk.c_ubyte * FrameHead.uBytes).from_address(pFrameBuffer)
            frame = np.frombuffer(frame_data, dtype=np.uint8)
            frame = frame.reshape((FrameHead.iHeight, FrameHead.iWidth,
                                   1 if FrameHead.uiMediaType == mvsdk.CAMERA_MEDIA_TYPE_MONO8 else 3))
            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT), interpolation=cv2.INTER_LINEAR)

            frame_count += 1
            if frame_count % DETECT_INTERVAL == 0:
                results = model(frame, classes=[SEABIRD_CLASS_ID], conf=CONF_THRESHOLD, verbose=False)
                last_detect_results = results

                if len(results[0].boxes) > 0:
                    bird_count = len(results[0].boxes)
                    max_conf = max([box.conf.item() for box in results[0].boxes])
                    print(f"🐦 识别到海鸟！数量：{bird_count}，最高置信度：{max_conf:.2f}")

                    # 收集每只海鸟的坐标信息
                    detections_info = []
                    for i, box in enumerate(results[0].boxes):
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        conf = box.conf.item()
                        detections_info.append({
                            'index': i + 1,
                            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'conf': conf
                        })

                    # 获取带识别框的画面
                    frame_with_detect = results[0].plot()

                    # 触发驱鸟，传入画面和坐标
                    trigger_bird_repellent(bird_count, max_conf, frame_with_detect, detections_info)

            if last_detect_results is not None:
                frame_with_detect = last_detect_results[0].plot()
            else:
                frame_with_detect = frame

            with frame_lock:
                latest_frame = frame_with_detect.copy()

            camera_error_count = 0

        except mvsdk.CameraException as e:
            if e.error_code != mvsdk.CAMERA_STATUS_TIME_OUT:
                print(f"相机取流失败({e.error_code}): {e.message}")
                camera_error_count += 1
                if camera_error_count >= 3:
                    check_and_trigger_alert("相机断开", "emergency", "相机连续取流失败，请检查设备连接！")
                    camera_error_count = 0
            time.sleep(0.1)
        except Exception as e:
            print(f"取流/识别异常：{str(e)}")
            time.sleep(0.1)

# ====================== 视频流生成函数 ======================
def generate_frames():
    while True:
        with frame_lock:
            if latest_frame is None:
                blank_frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
                cv2.putText(blank_frame, "Waiting for camera...", (FRAME_WIDTH//2 - 200, FRAME_HEIGHT//2),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
                ret, buffer = cv2.imencode('.jpg', blank_frame)
            else:
                ret, buffer = cv2.imencode('.jpg', latest_frame)
        if not ret:
            time.sleep(0.1)
            continue
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.03)

# ====================== Flask路由定义 ======================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/history')
def history():
    return render_template('history.html')

@app.route('/debug')
def debug():
    return render_template('debug.html')

@app.route('/gallery')
def gallery():
    return render_template('gallery.html')

@app.route('/get_repellent_stats', methods=['GET'])
def get_repellent_stats():
    with status_lock:
        return jsonify({
            'count': repellent_count,
            'history': repellent_history
        })

@app.route('/api/last_detection', methods=['GET'])
def get_last_detection():
    """获取最新一次识别信息"""
    with status_lock:
        return jsonify(last_detection_info)

@app.route('/api/images', methods=['GET'])
def get_image_list():
    """获取captures目录下的所有图片文件列表"""
    images = []
    base_path = CAPTURE_FOLDER
    if os.path.exists(base_path):
        for root, dirs, files in os.walk(base_path):
            for file in files:
                if file.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')):
                    rel_path = os.path.relpath(os.path.join(root, file), base_path)
                    # 转换为URL路径，使用正斜杠
                    url_path = '/captures/' + rel_path.replace('\\', '/')
                    images.append({
                        'name': file,
                        'url': url_path,
                        'folder': os.path.dirname(rel_path),
                        'mtime': os.path.getmtime(os.path.join(root, file))
                    })
    # 按修改时间倒序排列
    images.sort(key=lambda x: x['mtime'], reverse=True)
    return jsonify({'success': True, 'data': images})

@app.route('/captures/<path:filename>')
def serve_capture(filename):
    """提供captures目录下的图片文件"""
    return send_from_directory(CAPTURE_FOLDER, filename)

@app.route('/api/get_alerts', methods=['GET'])
def get_alerts():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM system_alerts ORDER BY id DESC LIMIT 50")
        rows = c.fetchall()
        alerts = []
        for row in rows:
            alerts.append({
                'id': row['id'],
                'time': row['time'],
                'alert_type': row['alert_type'],
                'alert_level': row['alert_level'],
                'alert_content': row['alert_content'],
                'is_read': row['is_read']
            })
        conn.close()
        return jsonify({'success': True, 'data': alerts})
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)})

@app.route('/api/get_unread_alert_count', methods=['GET'])
def get_unread_alert_count():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM system_alerts WHERE is_read = 0")
        count = c.fetchone()[0]
        conn.close()
        return jsonify({'success': True, 'count': count})
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)})

@app.route('/api/mark_alert_read', methods=['POST'])
def mark_alert_read():
    try:
        alert_id = request.json.get('alert_id')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE system_alerts SET is_read = 1 WHERE id = ?", (alert_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)})

@app.route('/api/get_bird_detections/<int:repellent_id>', methods=['GET'])
def get_bird_detections(repellent_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM bird_detections WHERE repellent_id = ?", (repellent_id,))
        rows = c.fetchall()
        detections = []
        for row in rows:
            detections.append({
                'bird_index': row['bird_index'],
                'x1': round(row['x1'], 1),
                'y1': round(row['y1'], 1),
                'x2': round(row['x2'], 1),
                'y2': round(row['y2'], 1),
                'confidence': round(row['confidence'], 2)
            })
        conn.close()
        return jsonify({'success': True, 'data': detections})
    except Exception as e:
        return jsonify({'success': False, 'msg': str(e)})

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/get_status', methods=['GET'])
def get_status():
    with status_lock:
        return jsonify({
            'total_switch': global_total_switch,
            'do1_status': global_do1_status,
            'do2_status': global_do2_status,
            'repellent_count': repellent_count
        })

@app.route('/control_total_switch', methods=['POST'])
def control_total_switch():
    global global_total_switch
    status = request.json.get('status', False)
    with status_lock:
        global_total_switch = status
        if not status:
            global global_do1_status, global_do2_status
            tashi_api.control_switch(SENSOR_ID_DO1, TRIGGER_OFF_VALUE)
            tashi_api.control_switch(SENSOR_ID_DO2, TRIGGER_OFF_VALUE)
            global_do1_status = False
            global_do2_status = False
    return jsonify({'success': True, 'total_switch': global_total_switch})

# 【关键修改】手动控制DO接口，增加DO2自动延时关闭功能
@app.route('/control_do', methods=['POST'])
def control_do_route():
    do_num = request.json.get('do_num', 1)
    status = request.json.get('status', False)
    with status_lock:
        if not global_total_switch:
            return jsonify({'success': False, 'msg': '总开关已关闭，无法控制设备'})
        sensor_id = SENSOR_ID_DO1 if do_num == 1 else SENSOR_ID_DO2
        success = tashi_api.control_switch(sensor_id, TRIGGER_ON_VALUE if status else TRIGGER_OFF_VALUE)
        if success:
            global global_do1_status, global_do2_status
            if do_num == 1:
                global_do1_status = status
                return jsonify({'success': True, 'do1_status': status})
            else:  # do_num == 2
                global_do2_status = status
                # 如果是开启DO2，则启动一个异步线程，15秒后自动关闭
                if status:
                    def auto_off_do2():
                        time.sleep(15)
                        with status_lock:
                            # 再次调用API关闭DO2，并更新全局状态
                            print("[手动驱鸟] 15秒到，自动关闭DO2")
                            tashi_api.control_switch(SENSOR_ID_DO2, TRIGGER_OFF_VALUE)
                            global_do2_status = False
                    threading.Thread(target=auto_off_do2, daemon=True).start()
                return jsonify({'success': True, 'do2_status': status})
        else:
            return jsonify({'success': False, 'msg': '设备控制失败，请检查网络连接'})

# ====================== 程序入口 ======================
if __name__ == '__main__':
    threading.Thread(target=camera_capture_thread, daemon=True).start()
    print("🚀 海眼系统Web后台启动中...")
    print("📱 手机端访问地址：http://[Windows主机局域网IP]:5000")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)