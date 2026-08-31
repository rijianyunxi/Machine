# 代码优化项清单

> 分析日期：2026-08-28 ｜ 状态更新：2026-08-28（面板开发时顺带修复了部分项）
> 范围：`main.py`、`core/`、`rules/`、`storage/`、`utils/`、`scripts/`、`config/`
> 说明：开发环境为 macOS（仅本地开发，不在 macOS 上部署/运行），RTSP 摄像头当前网络不可达。
> 因此清单按「部署环境相关（暂缓）」和「代码逻辑问题」分类。✅ = 已修复，⬜ = 待处理。

---

## 状态总览

| 项 | 状态 |
|----|------|
| 1. OpenCV 超时无效 + 强制 TCP | ⬜ 部署时修（代码已备注） |
| 2. 多路解码性能 | ⬜ 部署时定 |
| 2.1 model.rules 未生效/推理翻倍 | ✅ 已修：规则绑定模型 + 按相机路由（webapp/rules 联动） |
| 2.2 陈旧帧重复检测 | ✅ 已修：断流清空缓存帧 + 主循环帧龄校验 |
| 2.3 fd2 dup2 竞态 | ⬜ 待修 |
| 2.4 相机串行/无异常隔离 | ⬜ 待修（并发改造） |
| 2.5 快照/DB 阻塞热路径 | ⬜ 待修（异步队列） |
| 2.6 明文密码 | ⬜ 待修（面板展示已脱敏，YAML 仍明文） |
| 2.7 逐框 .cpu() | ✅ 已修：一次性转换 |
| — device auto 不试 MPS | ✅ 已修：CUDA → MPS → CPU |
| — 冷却期跳帧 | ✅ 已修：全冷却时跳过整帧检测 |
| — 规则引擎硬编码 | ✅ 已修：rules.yaml + 模板注册表 |
| — 相对路径依赖 cwd | ✅ 部分修：模型路径按项目根解析；config/logs 待统一 |

---

## 一、部署环境相关（到现场再验证，暂缓）

以下问题与运行环境绑定，本地无法验证，部署时优先处理：

### 1. OpenCV 超时设置无效 + 未强制 RTSP over TCP ⚠️ 已有日志证据

- 位置：`core/capture.py:179-189`
- 问题：`cv2.VideoCapture(url, CAP_FFMPEG)` 构造函数内就完成了打开动作，
  之后再 `set(CAP_PROP_OPEN_TIMEOUT_MSEC / READ_TIMEOUT_MSEC)` **不会生效**。
  日志证据：每次连接挂 25s+ 才报失败，而配置 `read_timeout: 10`。
- 修复方式：先创建空对象 → set 属性 → 再 open：

  ```python
  cap = cv2.VideoCapture()
  cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self.read_timeout * 1000))
  cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.read_timeout * 1000))
  cap.open(self.config.rtsp_url, cv2.CAP_FFMPEG)
  ```

- 同时 FFmpeg 默认先走 RTP/UDP，建议设置环境变量强制 TCP：

  ```python
  os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
  ```

  （Dahua NVR 多路并发时 UDP 更容易丢会话；走 frp 公网代理时也必须 TCP。）
- `CAP_PROP_BUFFERSIZE` 对 FFMPEG 后端基本是 no-op（`core/capture.py:182`），
  实际靠的是持续排水逻辑，可删除。

### 2. 多路解码性能（部署机的事）

- 网络流按源帧率（25fps）全速解码，只为取 2fps 用，4 路 1080p 解码 CPU 开销大。
- 可选优化：部署机有 N 卡就用 `device: cuda:0`；否则考虑
  `hwaccel;videotoolbox`（macOS）/ `hwaccel;cuvid`（N 卡）硬解，
  或直接用 `subtype=1` 子码流做检测（带宽和算力都省）。

### 3. 部署清单

- NVR 通道是否在线、密码是否正确（可先用 `scripts/test_camera.py` 单路验证）
- 走 frp 时必须强制 RTSP over TCP（UDP 端口动态协商，隧道走不了）
- 建议用 systemd/launchd 托管进程 + 自动重启

---

## 二、代码逻辑问题（随时可改，不依赖真实摄像头）

### P1 — 影响正确性

#### 2.1 所有模型跑所有相机的每一帧，`model.rules` 配置未生效

- 位置：`main.py:191`（`MultiDetector.detect_all`）
- 问题：`settings.yaml` 中 ppe 模型标了 `rules: [1]`、smoking 模型标了 `rules: [13]`，
  但 `detect_all` 不看这个映射，每帧两个模型全跑。
  只启用规则 1 的相机也白白跑一遍 smoking 模型，**推理量翻倍**。
- 修复：按相机 `active_rules` 过滤需要跑的模型；进一步可利用冷却期——
  某相机所有规则都在冷却中时，整帧检测都可以跳过。

#### 2.2 断流期间反复对陈旧冻结帧做检测

- 位置：`core/capture.py:282-285`（写入）+ `main.py:184`（消费）
- 问题：重连期间 `_latest_frame` 不会被清空，主循环每 0.5s 拿到的是同一帧旧画面：
  - `frames_processed` 虚高
  - 冻结帧若含违规，每个冷却周期（30s）重复告警 + 重复存快照
- 修复：主循环校验 `frame_data.timestamp` 新鲜度（超过 3× 帧间隔即跳过），
  或断流/释放时清空 `_latest_frame`。

#### 2.3 stderr 抑制的 fd2 dup2 多线程竞态

- 位置：`core/capture.py:260-261`（`_suppress_ffmpeg_stderr`）
- 问题：每个相机线程每次 read 都进出该上下文，而 `dup2` 重定向的是**进程级** fd 2。
  多线程并发进出时，一个线程退出时可能把「别的线程的 devnull」恢复成 fd 2，
  导致真正的 stderr 被永久吞掉，后续所有报错都看不到。
- 修复：启动时全局设置一次（OpenCV 5 支持 `OPENCV_FFMPEG_LOGLEVEL=-8` 环境变量），
  不要每次 read 都 dup2。

### P2 — 性能与安全

#### 2.4 主循环串行处理相机，无异常隔离

- 位置：`main.py:171-222`
- 问题：
  - 4 路相机在单线程里依次各跑 2 个模型，CPU 上总耗时很可能超过
    `frame_interval`（0.5s），`target_fps` 形同虚设
  - 循环体无 try/except，任一路处理抛异常会终止**整个系统**
- 修复：每相机独立检测线程（或线程池）并行；按相机隔离异常。

#### 2.5 快照 JPEG 编码 + DB 写入阻塞检测热路径

- 位置：`main.py:206-214`
- 问题：告警时同步做整帧 JPEG 编码（可达数百 ms）和 SQLite 写入，
  期间其他相机的处理全部停摆。
- 修复：快照保存与入库放入后台队列异步执行。

#### 2.6 相机明文密码硬编码

- 位置：`config/cameras.yaml:38-57`
- 问题：4 路 RTSP URL 内嵌 弱密码（已在入库前脱敏），注释示例中还留有其他设备密码。
- 修复：改为环境变量占位（加载 YAML 后展开，如 `rtsp://admin:${CAM_PWD}@...`），
  并更换 NVR 默认密码。

#### 2.7 逐框 `.cpu()` 同步开销

- 位置：`core/detector.py:89-93`
- 问题：每个框分别调用 `boxes.xyxy[i].cpu().numpy()`，每框一次设备同步。
- 修复：一次性转换后再循环：

  ```python
  xy = boxes.xyxy.cpu().numpy()
  conf = boxes.conf.cpu().numpy()
  cls = boxes.cls.cpu().numpy()
  ```

### P3 — 可维护性 / 运维

| 项目 | 位置 | 说明 |
|------|------|------|
| 规则引擎字段未使用 | `rules/rules_engine.py:35-37` | `required_ppe`/`absence_ppe` 从未被读取，而 `analyzer.py:73-82` 硬编码 `rule.id == 1/13/14` 分发，两套定义重复易漂移。建议 dispatch 表或把规则逻辑挂到 `RuleDefinition` |
| Rule 1 每帧只报第一个人 | `core/analyzer.py:118-147` | 画面里 5 人未戴帽只记 1 条告警，统计偏少，可返回每人的违规 |
| 快照目录无限增长 | `core/snapshot.py:72-76` | 按日期/规则分目录但无保留策略，建议加清理任务（保留 N 天） |
| requirements 与注释矛盾 | `requirements.txt:1` | 注释说「pinned」但 opencv/numpy/Pillow/scipy/PyYAML 未锁版本；建议 `uv pip compile` 生成锁文件 |
| 相对路径依赖 cwd | `main.py:50` 等 | `--config` 默认 `config`，settings 里 `models/`、`logs/`、`storage/` 全是相对路径，从别的目录启动会找不到文件。建议统一相对 `PROJECT_ROOT` 解析 |
| 无 README / 测试 / pyproject | 项目根目录 | 换人接手或部署没有文档；`scripts/test_camera.py` 可作为冒烟工具写进 README |
| `time.sleep(3)` 魔法数 | `main.py:145` | 等相机连接改为「等待首帧或超时」 |
| 告警日志重复 | `main.py:216` + `infrastructure/persistence/alert_database.py:145` | insert_alert 成功后 main 里又 log 一遍 warning |
| DB 每次插入新建连接 | `infrastructure/persistence/alert_database.py:40-48` | 告警量大时可复用长连接（`check_same_thread=False`） |

---

## 三、建议处理顺序

1. **先修 2.1 / 2.2 / 2.3**（纯逻辑，本地即可改）
2. 本地用视频文件跑通全链路验证（见下节）
3. 部署环境再修第一节（超时 + TCP），确认流能拉通
4. 吞吐优化：2.4 + 2.5（并行 + 异步 IO）
5. 其余 P2/P3 按需排

## 四、本地开发技巧：不需要 RTSP 也能跑

`core/capture.py` 本身支持本地文件源（URL 不带网络前缀时走采样逻辑，
按 `target_fps` 逐帧取样），所以本地调试整条
「取帧 → 检测 → 规则 → 快照 → 入库」链路不需要摄像头：

1. 准备几个含安全帽 / 吸烟场景的测试视频（如 `test_videos/helmet.mp4`）
2. `config/cameras.yaml` 里把 `rtsp_url` 直接指到本地文件：

   ```yaml
   - id: "CAM_TEST_1"
     name: "本地测试视频"
     rtsp_url: "test_videos/helmet.mp4"
     enabled: true
     rules: [1, 13, 14]
   ```

3. `python main.py` 即可本地验证检测、告警、快照、入库全流程。

> 注意：本地文件路径是相对启动目录的，修复「相对路径依赖 cwd」前
> 请在项目根目录启动。
