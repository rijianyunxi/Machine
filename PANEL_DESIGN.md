# Web 管理面板设计方案

> 版本：v0.3 ｜ **实施状态：M1/M2/M3 核心已实现并端到端验证（2026-08-28），M4 部分完成**
> 日期：2026-08-28
> 目标：为现有检测系统（demo 级）增加一个 Web 面板，**所有功能可查看、所有配置可修改**，
> 并新增：**模型在线导入**、**监控匹配规则在线配置（规则↔模型绑定）**、**上传图片在线检测（测试台）**、
> **告警误报标记**、**快照量级预估与保留策略**。
> 原则：不重写现有检测链路，面板挂在现有代码旁边；依赖轻、无前端构建步骤、可离线运行。

**已确认决策**：界面中文（术语保留英文）；面板端口 **8000**；需要**误报标记**；快照量级预估与保留策略见 §9。
遗留待定：是否支持 ONNX 导入（默认后置，见 §12）。

## 实施状态（相对设计的变化）

- ✅ M1 只读面板：总览/相机状态+预览/告警/快照/日志/统计 —— 完成
- ✅ M2 配置管理 + 测试台 + 误报标记 —— 完成（相机 CRUD 热生效、阈值/冷却/日志级别热更、
  告警 new/confirmed/false_positive/resolved + 备注、图片/相机帧测试台）
- ✅ M3 模型导入 + 规则在线化 —— 完成（.pt 上传/后台校验/注册/热加载；rules.yaml +
  模板引擎 ppe_absence & presence_near_person + 按规则路由模型 + 冷却期跳帧）
- 🔶 M4：趋势图/磁盘水位/待重启徽标已做；**WS 推送改为 2–5 秒轮询**（demo 阶段更稳，
  WS 留作后续）；配置回滚 UI 未做（后端 .bak 备份已具备）
- ✅ UI v2（2026-08-28 晚）：全面改版为现代深色控制台风格——侧边栏图标导航、渐变主按钮、
  统一 chip/徽章、样式化确认弹窗与 toast、相对时间、按钮加载态、表格横向滚动防变形、
  缩略图固定 16:9、窄屏侧边栏自动收起；新增**样式化登录页 + cookie 会话**
  （/api/login，Basic 认证仍兼容 curl）；浏览器逐页截图验收通过
- ✅ 数据闭环（2026-08-28 深夜）：**数据集管理**（datasets/ 下 YOLO 格式：新建/上传/
  从快照导入/AI 批量预标注）→ **在线标注**（画框拖拽编辑 + 快捷键 + 自动保存；
  AI 预标注用本地模型，LLM 建议走配置的视觉大模型、可解析 JSON 框应用）→
  **在线训练**（子进程隔离，results.csv 解析进度/mAP，完成后一键注册 best.pt）→
  模型管理页类别表按 `id:name` 展示；**LLM 配置**在系统设置（OpenAI 兼容，
  测试连接按钮，api_key 明文存储需注意）；告警页筛选栏吸顶
- ✅ AI 助手 v2（2026-08-29）：「LLM 建议」改名 **AI 建议**，点击后从**右侧抽屉**展示
  发送的提示词 + 模型原始返回；结果进入**预览模式**——手动标注临时隐藏、AI 框在画布上
  可拖动/缩放/改类别/删除地微调，抽屉里逐框勾选，应用只并入勾选项（放弃则完整恢复手动
  标注）；预览期间禁止切图/保存防误写；竞态守卫防标注未加载时进入预览；LLM 配置新增
  **获取模型列表**（GET {base_url}/models，下拉选择回填）；修复本地 LLM 被系统
  代理劫持 503 的问题（localhost 绕过环境代理）
- 实现差异：MJPEG 预览每路约 0.5s/帧；路由修复——规则引用的 person_classes 可来自
  其他模型（自动并入推理集合）；RulesStore 按 mtime 热载，面板/主循环共享单例；
  训练 demo 数据集 val=train，mAP 仅作参考

---

## 1. 目标与范围

### 做什么

| 维度 | 内容 |
|------|------|
| 查看 | 系统运行状态、相机连接状态与实时画面、检测结果、告警记录、快照库、日志、模型信息（含导入的）、统计图表、**快照目录磁盘占用/水位** |
| 配置 | 相机增删改/启停/规则分配、检测参数、抓拍参数、告警冷却、日志级别；**规则在线编辑（类型/参数/绑定模型/分配相机）**；**模型文件上传导入/注册/启停**；**快照/数据库保留天数** |
| 操作 | 手动重连相机、**告警确认（真实违规）/误报标记/处理完结**、模型启停与热加载、上传图片跑模型看结果、**手动清理历史快照** |

### 不做什么（明确排除，防止范围膨胀）

- 视频墙 / NVR 录像回放（只做实时预览截图，不做拉流转码）
- 多用户 / 角色权限（只做单管理员密码）
- 告警外推（微信/钉钉/邮件）——列入后续 backlog
- 模型训练/在线标注（训练仍走 `scripts/train_model.py`，面板只负责导入与验证）
- 移动端适配（桌面浏览器优先）

---

## 2. 技术选型

| 项 | 选择 | 理由 |
|----|------|------|
| 后端 | **FastAPI + uvicorn** | 原生 WebSocket、pydantic 校验、`/docs` 自带接口文档；与现有纯 Python 项目无缝集成 |
| 文件上传 | **python-multipart** | 模型 .pt / 测试图片上传 |
| 模板 | **Jinja2 服务端渲染** | 无需前端构建链，部署机离线可用 |
| 前端 | **原生 JS + Chart.js（本地 vendor）** | 不引入 node/npm；界面文案硬编码中文，术语（YOLO/RTSP/FPS）保留英文，不做 i18n 框架 |
| 配置写回 | **ruamel.yaml（round-trip）** | 保留现有 YAML 注释；新增 `config/rules.yaml` 同样注释友好 |

**备选对比（否决理由）**：Flask+SocketIO（WS/校验都要自己拼）；Gradio/Streamlit（整页重跑模型，做不了精细 CRUD）；Vue/React SPA（需要构建链，离线部署麻烦，demo 阶段收益为负）。

---

## 3. 架构

### 3.1 进程模型：面板嵌入主进程

```
┌──────────────────────────────────────────────────────────┐
│ main 进程                                                 │
│                                                          │
│  MachineVisionSystem（现有，小改见 §5.4）                  │
│    ├─ CameraManager ──── N × CameraStream 线程            │
│    ├─ ModelRegistry（原 MultiDetector 扩展）               │
│    │     支持运行时 load / unload / import 校验            │
│    ├─ BehaviorAnalyzer（改造为模板驱动的通用规则引擎）       │
│    ├─ SnapshotManager（增加保留策略清理任务）               │
│    ├─ AlertDatabase (SQLite, WAL)                        │
│    └─ RetentionService（快照/测试结果/DB 过期清理，日任务） │
│                                                          │
│  PanelServer（新增，daemon 线程跑 uvicorn，端口 8000）      │
│    ├─ REST API（状态/告警/快照/配置/规则/模型/测试台/存储）  │
│    ├─ WS 事件推送 + MJPEG 画面预览                         │
│    ├─ ConfigService（YAML 读写 + 热下发 + 备份）           │
│    ├─ ModelService（上传/校验/注册/热加载编排）             │
│    └─ DetectTestService（测试台：单飞锁 + 后台推理）        │
└──────────────────────────────────────────────────────────┘
```

- **同进程嵌入**（推荐）：面板直接读活对象，配置直接下发运行时。uvicorn 在 daemon 线程，handler 异常不影响主循环；面板只通过 `RuntimeState` 门面访问系统。
- `python -m webapp.server --standalone`：**只读模式**独立启动（告警/快照/历史查询 + 测试台），主程序没开也能用。
- 推理互斥：测试台推理与主检测共用进程算力，**单飞锁**（同一时刻一个测试推理）+ 排队，避免拖垮实时检测。

### 3.2 配置变更的两条路径

```
面板修改 → ConfigService
             ├─ 1. 写 YAML 落盘（ruamel 保留注释，写前备份 config/*.bak）
             └─ 2. 热下发到运行时对象（见 §7 热更矩阵）
                    ├─ 可热更 → 立即生效
                    └─ 不可热更 → 标记"待重启生效"，面板显示徽标
```

---

## 4. 功能与 API 设计

### 4.1 覆盖矩阵（查看 × 配置 × 热更）

| 功能 | 查看 | 配置 | 热更 |
|------|:---:|:---:|:---:|
| 相机（增删改/启停/规则分配） | ✅ 状态/分辨率/失败次数/最后帧龄 | ✅ | ✅ |
| 实时画面预览 | ✅ MJPEG | — | — |
| **模型文件管理** | ✅ models/ 文件清单、校验状态、类别 | ✅ 上传导入/注册/删除/替换 | ✅ 注册后热加载 |
| **模型实例（启停/阈值）** | ✅ 已加载/类别/设备/阈值 | ✅ conf/iou/imgsz/enable | ✅ |
| **规则配置** | ✅ 规则/类型/参数/绑定模型/**7天误报率** | ✅ 参数/绑定模型/分配相机/启停 | ✅ |
| 抓拍参数 | ✅ | ✅ | ✅ |
| 告警冷却 | ✅ | ✅ | ✅ |
| **告警反馈** | ✅ 列表/详情/误报率统计 | ✅ 确认违规/误报/完结 + 备注 | — |
| **存储占用** | ✅ 快照目录按天体积、磁盘水位 | ✅ 保留天数 | ✅ 清理任务即时重跑 |
| 抓帧参数 | ✅ | ✅ | ⚠️ 改后重启对应流 |
| 日志 | ✅ tail+过滤 | ✅ level 热更 | ✅ |
| 快照库 | ✅ 树形浏览 | — | — |
| 统计 | ✅ 总量/每路 fps/趋势 | — | — |
| **图片检测测试台** | ✅ 上传图→标注结果+JSON+耗时 | ✅ 选模型/临时阈值 | — |
| 数据库/日志文件路径 | ✅ | ✅ 需重启 | ❌ |

### 4.2 REST API

```
# 系统
GET  /api/system/info                 # 版本/设备/模型/启动时间
GET  /api/system/stats                # 全局+每路帧数、违规数、fps、uptime
GET  /api/system/stats/history        # 按天告警趋势（含确认/误报分类）

# 存储
GET  /api/storage/usage               # 快照目录按天条数+体积、磁盘总/剩余、水位状态

# 相机
GET    /api/cameras                   # 列表+实时状态
POST   /api/cameras                   # 新增（id 唯一、URL、rules 校验）
PUT    /api/cameras/{id}              # 修改（name/url/rules/enabled）
DELETE /api/cameras/{id}              # 删除（停流+YAML 移除）
POST   /api/cameras/{id}/restart      # 强制重连
GET    /api/cameras/{id}/frame.jpg    # 最新单帧
GET    /api/cameras/{id}/stream.mjpg  # MJPEG 预览

# ── 模型导入与管理 ──────────────────────────────
GET    /api/models/files              # 文件清单 + 注册状态 + 校验状态
POST   /api/models/files              # 上传 .pt（multipart，≤200MB，后缀白名单）
DELETE /api/models/files/{name}       # 删除文件（被注册引用时拒绝）
GET    /api/models/files/{name}/validate   # 触发/查询校验（类别数、类别名、imgsz、task）
POST   /api/models                    # 注册为模型实例（默认 enabled:false）
PUT    /api/models/{name}             # 阈值热更 / enabled 切换（后台线程加载）
POST   /api/models/{name}/reload      # 重载（换文件后）
DELETE /api/models/{name}             # 注销（仅卸载，不动文件）

# ── 规则在线配置 ────────────────────────────────
GET    /api/rules                     # 规则列表（含类型/参数/绑定模型/使用中相机/7天误报率）
POST   /api/rules                     # 新建规则实例
PUT    /api/rules/{id}                # 改参数/绑定模型/severity/enabled
DELETE /api/rules/{id}                # 删除（有相机引用时拒绝）
GET    /api/rules/templates           # 规则模板定义（§5.1，前端动态渲染表单）

# ── 图片检测测试台 ──────────────────────────────
POST   /api/detect/test               # multipart: image + models[] + conf/iou 覆盖
                                      # → {detections:[...], latency_ms, result_id}
GET    /api/detect/test/{rid}/annotated.jpg   # 标注结果图
GET    /api/detect/test/camera/{cid}  # 用某相机当前帧跑一次测试
GET    /api/detect/test/history       # 最近 N 次测试记录

# ── 告警（含误报标记）────────────────────────────
GET  /api/alerts?camera=&rule=&status=&feedback=&from=&to=&limit=&offset=
GET  /api/alerts/{id}                 # 详情
POST /api/alerts/{id}/status          # {"status": "...", "note": "可选备注"}
                                     # status ∈ new|confirmed|false_positive|resolved
GET  /api/alerts/summary              # 按规则/相机/天聚合；含每规则误报率

# 快照 / 配置 / 日志
GET  /api/snapshots?date=&rule=&camera=&limit=      # 含每日条数/体积
POST /api/snapshots/cleanup           # {"before_date": "..."} 手动清理
GET/PUT /api/settings/{section}       # 含 snapshot.retention_days、database.retention_days
GET  /api/settings/pending            # 待重启生效项
GET  /api/logs?level=&tail=500
WS   /ws                              # 推送：alert.new / camera.status / model.status /
                                      #        stats.tick / test.done / storage.warning
```

所有写操作：pydantic 校验 → 落盘（先备份）→ 运行时下发 → 返回 `{applied, restart_required}`。

**告警状态机**：

```
new ──┬─→ confirmed（确认违规）──→ resolved（已处理）
      └─→ false_positive（误报）   （误报也可直接完结，不强制走 resolved）
```

DB 无需迁移：现有 `status TEXT DEFAULT 'new'` 直接扩值；备注写入已预留未用的 `extra` 字段。
误报率 = false_positive / (confirmed + false_positive)，按规则/相机/天三个维度统计。

### 4.3 页面

| 页面 | 内容 |
|------|------|
| 总览 | 状态卡片、WS 实时告警流、相机缩略图+状态点、7 天告警趋势；**磁盘水位横幅**（>80% 黄 / >90% 红） |
| 相机管理 | 表格 + 新增/编辑弹窗（规则多选框来自在线规则表）+ 启停/重连；URL 密码脱敏 |
| 模型管理 | 已注册模型卡片（类别/设备/阈值/启停/重载）+ 文件区（上传拖拽、校验徽标、"注册为模型"） |
| 规则配置 | 规则列表（ID/名称/模板/绑定模型/启停/**7天误报率**——误报率高时提示调高阈值）+ 编辑抽屉（按模板动态渲染参数表单） |
| 检测测试台 | 上传图片（或取相机帧）→ 选模型+临时阈值 → 标注结果+JSON+耗时；新导入模型的验收工具 |
| 告警记录 | 筛选（相机/规则/状态/误报/时间段）+ 分页表格 + 快照详情；**操作按钮：确认违规 / 标记误报 / 完结**，可填备注 |
| 快照库 | 日期/规则树 + 网格；**每日条数与体积**；手动清理某天（带确认弹窗） |
| 系统设置 | 按 section 分组表单；新增**保留天数**配置；不可热更项标"需重启" |
| 日志 | tail + 级别过滤 + 自动滚动 |

---

## 5. 规则引擎在线化（本期最大改造）

> 现状：规则硬编码在 `rules/rules_engine.py`（RULES 字典）+ `analyzer.py:73-82`
> 硬编码 `if rule.id == 1/13/14` 分发。要在面板上"在线配置匹配规则"，必须把规则变成**数据**。

### 5.1 规则模板化

分析现有 3 条规则，抽象成 **2 个模板**即可覆盖，未来加模板即可扩展：

| 模板 | 语义 | 覆盖的现有规则 | 参数 |
|------|------|----------------|------|
| `ppe_absence` | 人员缺失必备装备 | R1 未戴安全帽 | person_classes、required_classes、absence_classes（一票否决类）、coverage_ratio |
| `presence_near_person` | 特定目标出现在人员附近/身上 | R13 禁火区吸烟、R14 持烟 | trigger_classes、person_classes、overlap_margin、min_confidence |

（R13 与 R14 本质是同模板不同参数：触发类别不同、margin 不同——正说明模板化是对的。）

### 5.2 规则持久化：新增 `config/rules.yaml`

```yaml
rules:
  - id: 1
    name: no_safety_helmet
    template: ppe_absence
    enabled: true
    severity: 3
    models: [ppe]                      # 绑定模型：该规则只消费此模型的检测结果
    params:
      person_classes: [person]
      required_classes: [hardhat]
      absence_classes: [no-hardhat]    # 检出即违规（一票否决）
      coverage_ratio: 0.5              # 装备框被人员框覆盖率阈值
    description: "Worker without safety helmet"

  - id: 13
    name: smoking_no_fire_zone
    template: presence_near_person
    enabled: true
    severity: 4
    models: [smoking]
    params:
      trigger_classes: [cigarette, smoking]
      person_classes: [person]
      overlap_margin: 0.2
  - id: 14
    name: person_holding_cigarette
    template: presence_near_person
    enabled: true
    severity: 3
    models: [smoking]
    params:
      trigger_classes: [cigarette]
      person_classes: [person]
      overlap_margin: 0.1
```

- 首次启动：从代码内置规则**种子迁移**到 rules.yaml（老 ID 不变，DB 历史兼容）。
- 新建规则：面板自动分配下一个空闲整数 ID（DB 的 rule_id 是整数，历史数据零迁移）。
- 告警表写入时冗余了 rule_name，删除规则不影响历史记录展示。

### 5.3 通用规则引擎

`BehaviorAnalyzer` 改为模板注册表驱动：

```python
RULE_TEMPLATES = {"ppe_absence": PpeAbsenceCheck, "presence_near_person": PresenceNearPersonCheck}

def analyze_frame(...):
    for rule in load_enabled_rules():            # 每帧读共享配置对象（热更生效点）
        check = RULE_TEMPLATES[rule.template]
        v = check(camera_id, rule, detections, timestamp)
```

- 参数（margin、coverage_ratio、类别集）全部来自规则实例 → **在线改参数下一帧生效**。
- 校验：类别名必须在绑定模型的类别表内（面板保存时校验并提示近似拼写）。
- 误报数据反哺调参：规则页展示每条规则 7 天误报率，误报率高 → 面板提示调高该规则绑定模型的置信度阈值或收紧 overlap 参数。

### 5.4 主程序侧需要的改动（与 OPTIMIZATION.md 联动）

| 改动 | 说明 | 顺带收益 |
|------|------|----------|
| 主循环每轮重读相机配置 | `main.py:166` 现在只在启动时读一次 | 相机规则分配热更 |
| `MultiDetector` → `ModelRegistry` | 增加 load/unload/import 后台线程加载 | — |
| **按相机规则路由模型** | 相机的规则 → 规则绑定的模型集合，只跑这些模型 | 直接实现 OPTIMIZATION.md §2.1（推理量减半） |
| 规则冷却检查前置 | 所有规则都在冷却 → 跳过整帧检测 | 同上，进一步省算力 |

---

## 6. 模型导入与检测测试台

### 6.1 模型导入流程

```
上传 .pt ──→ 落盘 models/uploads/{安全文件名}（防覆盖：重名自动加后缀）
        ──→ 后台校验（后台线程 YOLO(path) 试加载）
              ├─ 成功：提取 task/类别表/imgsz → 状态"有效(11 类)"，可预览类别
              └─ 失败：状态"无效"+错误信息（不注册）
        ──→ 用户确认类别表 → "注册为模型"（写入 settings.yaml models，默认 enabled:false）
        ──→ 面板"启用" → 后台线程热加载 → 出现在总览模型列表
```

- 校验完成后可在测试台用样本图试跑，确认效果再启用。
- 文件管理：清单显示文件大小/导入时间/被哪个模型实例引用；被引用的文件禁止删除。
- 替换更新：对已注册模型"上传新版本文件"→ 校验 → reload（老文件备份为 `.bak`）。

### 6.2 检测测试台（在线用模型检测上传图片）

- 请求：`POST /api/detect/test`（multipart：图片 + 模型多选 + 临时 conf/iou 覆盖）
- 处理：后台线程推理（**单飞锁**，同一时刻一个测试任务，排队返回），不干扰主检测循环
- 响应：检测 JSON（类别/置信度/框）+ 服务端 cv2 标注图（存 `storage/test_results/`，保留最近 50 张自动清理）+ 推理耗时
- 快捷入口：`从相机取帧测试`（用 `CameraStream` 缓存帧，免上传）；导入模型后的"去测试"跳转
- 支持批量：一次最多 5 张图逐张跑（CPU 上防止长时间占用）

---

## 7. 配置热更新矩阵

| 配置项 | 热更方式 | 备注 |
|--------|----------|------|
| 相机增删改/启停 | `CameraManager.add/remove_camera` | 已支持 |
| 相机 rules | 每轮重读共享配置（§5.4） | — |
| 规则参数/绑定模型/启停 | 每帧读 rules.yaml 缓存对象 | 下一帧生效 |
| 模型 conf/iou/imgsz | 改 `Detector` 实例属性 | 每次推理读取 |
| 模型启停/新导入 | `ModelRegistry` 后台线程加载/卸载 | 状态经 WS 推送 |
| cooldown | 改 `BehaviorAnalyzer` 属性 | — |
| snapshot 参数 | 改属性 | — |
| 保留天数 retention_days | 改属性 + 手动"立即清理"按钮 | — |
| capture 参数 | 改属性 + 面板触发该流重启 | — |
| logging.level | root logger setLevel | — |
| database.path / log 文件参数 | 需重启 | 徽标提示 |

---

## 8. 目录结构（新增部分）

```
metch-yolo/
├── config/
│   └── rules.yaml            # 新：规则实例持久化（§5.2）
├── infrastructure/
│   └── persistence/
│       └── alert_database.py # SQLite 告警持久化适配器
├── storage/                  # 仅存运行数据，不包含 Python 代码
│   ├── alerts.db             # SQLite 告警数据
│   ├── snapshots/            # 现有：YYYY-MM-DD/规则名/*.jpg（保留策略按天清理）
│   └── test_results/         # 新：测试台结果图（保留 50 张）
├── webapp/
│   ├── server.py             # FastAPI 工厂 + uvicorn 线程 + standalone 入口
│   ├── state.py              # RuntimeState 门面
│   ├── config_service.py     # YAML 读写（ruamel）+ 备份 + 热下发
│   ├── model_service.py      # 上传/校验/注册/热加载编排
│   ├── detect_service.py     # 测试台推理（单飞锁 + 结果管理）
│   ├── retention_service.py  # 快照/测试结果/DB 过期清理（日任务 + 手动触发）
│   ├── api/
│   │   ├── system.py  storage.py  cameras.py  alerts.py  snapshots.py
│   │   ├── models_api.py  rules.py  detect_api.py
│   │   ├── settings_api.py  logs.py
│   ├── static/  (css / js / vendor/chart.umd.js)
│   └── templates/ (base dashboard cameras models rules detect alerts snapshots settings logs)
├── rules/
│   └── rules_engine.py       # 改造：模板注册表 + rules.yaml 加载
└── PANEL_DESIGN.md
```

新增依赖：`fastapi`、`uvicorn`、`jinja2`、`python-multipart`、`ruamel.yaml`。

---

## 9. 快照量级预估与保留策略

### 9.1 单张快照体积（JPEG quality=90）

| 源分辨率 | 单张大小 | 说明 |
|----------|----------|------|
| 1080p | 0.3–0.8 MB（按 0.5 估） | Dahuа subtype=0 常见 |
| 4MP/2K | 0.8–1.5 MB | |
| 4K | 1.5–3 MB | 需实测确认 |

### 9.2 量级情景（按 4 相机 × 3 规则、每天 12 小时工作时间估算）

| 情景 | 告警速率 | 每天 | 每天 0.5MB/张 | 每月（30 天） |
|------|----------|------|----------------|----------------|
| 低频（现场基本合规） | 2 条/相机/小时 | ~96 张 | ~48 MB | **~1.4 GB** |
| 中频 | 10 条/相机/小时 | ~480 张 | ~240 MB | **~7 GB** |
| 高频（误报多/常触发） | 40 条/相机/小时 | ~1,920 张 | ~0.9 GB | **~28 GB** |

- 理论天花板：冷却 30s 下每相机-规则对最多 120 条/小时，12 对 ≈ 1,440 条/小时——真实场景到不了，但**误报失控时"高频"档是真实风险**，这正是要做误报标记 + 保留策略的原因。
- DB 体积可忽略：每条告警含索引 ~0.5 KB，即使 1,920 条/天也仅 ~1 MB/天（~30 MB/月），保留一年无压力。
- 磁盘压力主要来自快照 → 保留策略针对快照设计。

### 9.3 保留策略设计

| 对象 | 默认保留 | 配置项 | 清理方式 |
|------|----------|--------|----------|
| 快照 | **30 天** | `snapshot.retention_days` | 日任务（每天 03:00）+ 面板手动清理指定日期前 |
| 测试台结果 | 最近 50 张 | 固定 | 写入时滚动清理 |
| 告警 DB | **180 天** | `database.retention_days` | 日任务 DELETE（有索引，代价低） |
| 配置备份 *.bak | 最近 10 份 | 固定 | 写盘时滚动 |

- 清理实现利用现有按天分区：目录名即 `YYYY-MM-DD`，按目录前缀整目录删除，O(天数)。
- 启动时先跑一次清理（长时间停机后补删）。
- 面板可视化：总览显示快照目录总占用 + 磁盘剩余 + 水位（>80% 黄 / >90% 红，WS 推送 `storage.warning`）；快照库页按天显示条数与体积；规则页误报率作为"量级失控"的定位入口（哪条规则在刷量一眼可见）。
- 量级超标的治理顺序（面板全可操作）：标记误报定位来源规则 → 调高该规则绑定模型置信度 → 拉长冷却 → 降分辨率（换 subtype=1）→ 缩短保留天数。

---

## 10. 安全与运维

- 监听 `0.0.0.0:8000`（局域网访问），**HTTP Basic Auth 默认开启**（账号密码存 settings 或环境变量）
- ⚠️ **.pt 文件是 pickle，加载即执行任意代码**：上传入口是高危面。对策：
  1) 仅认证管理员可传；2) 后缀白名单 `.pt` + 大小上限（默认 200MB，可配）；
  3) 文档明示"只导入可信来源模型"；4) 可选进阶：校验阶段在子进程加载，崩溃不伤主进程
- 上传图片：后缀白名单 jpg/jpeg/png/webp、单张 ≤20MB、批量 ≤5 张
- RTSP 密码 API/页面一律脱敏；配置写盘前备份 + 面板一键回滚
- SQLite 面板侧只读（除告警 status/extra 字段），WAL 并发读安全
- 快照/测试结果静态服务限制根目录，防路径穿越
- 测试台推理与主检测互斥（单飞锁）
- 磁盘水位监控与 WS 告警（§9.3）

---

## 11. 实施里程碑

| 阶段 | 内容 | 验收 |
|------|------|------|
| **M1 只读面板** | 总览、相机状态+预览、告警列表+快照浏览、日志 tail、统计、存储占用展示 | 本地视频源跑通：4 路"相机"状态、实时画面、告警、快照可见 |
| **M2 配置管理 + 测试台 + 误报标记** | 相机 CRUD 热生效、模型阈值/冷却/日志级别热更；图片检测测试台；**告警确认/误报/完结状态机** | 面板新增本地视频相机不重启即生效；上传图片出标注结果；告警可标记误报且 summary 出误报率 |
| **M3 模型导入 + 规则在线化** | 模型上传/校验/注册/热加载；rules.yaml + 通用规则引擎 + 规则↔模型绑定 + 按规则路由模型；保留策略清理任务 | 上传新 .pt → 校验类别 → 注册 → 测试台试跑 → 启用参与检测；新建规则分配相机即生效；过期快照被自动清理 |
| **M4 完善** | WS 事件推送、趋势图（含误报率曲线）、磁盘水位横幅、待重启徽标、配置回滚 UI | 告警实时弹出；所有配置可备份回滚；水位告警可触发 |

M1 不动现有逻辑；M3 是唯一涉及检测链路改造的阶段（§5.4 的 4 个改动），单独隔离。

---

## 12. 决策记录与遗留问题

**已确认**：
1. ✅ 界面中文，术语保留英文，硬编码文案不做 i18n
2. ✅ 面板端口 8000（`0.0.0.0`，认证默认开启）
3. ✅ 需要误报标记 → 告警状态机 `new/confirmed/false_positive/resolved` + 备注（§4.2）
4. ✅ 快照量级预估与保留策略 → 默认快照 30 天 / DB 180 天，量级情景表见 §9

**遗留（默认按括号内方案执行，有异议再改）**：
1. 模型上传上限默认 200MB（可配）
2. ONNX 导入支持：后置（需加 onnxruntime 依赖，等有真实需求再开）
3. 告警外推（微信/钉钉）：backlog，不进本期
