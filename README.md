# Machine · 机器视觉不安全行为检测系统

接入 RTSP 监控摄像头，用 YOLOv8 模型自动识别不安全行为并告警。
中文 Web 管理面板：上传模型 → 配置规则 → 绑定监控，全程在浏览器里完成，不需要改代码。

## 配置和运行原则

运行时只有一个配置与告警数据库：`storage/machine.db`。以下数据全部存储在这一个 SQLite 文件中：

- 系统设置、面板账号和 LLM 配置；
- 模型注册信息；
- 摄像头及摄像头-规则关联；
- 规则模板、规则参数和画布 graph；
- 告警记录、名称快照和截图状态。

`config/*.yaml` 仅作为开发阶段的首次导入源、人工排查材料和显式导入/导出工具的输入输出，页面保存不会回写 YAML，程序启动也不会隐式读取 YAML。

## 快速开始

```bash
# 1. 安装依赖
uv pip install -r requirements.txt --python .venv/bin/python

# 2. （首次使用或重建开发库时）显式导入 YAML 到统一数据库
.venv/bin/python tools/import_yaml_config.py \
  --config-dir config \
  --database storage/machine.db

# 3. 启动检测 + 面板
.venv/bin/python main.py

# 4. 浏览器打开面板
#    http://localhost:8000
#    默认账号 admin；密码以导入的数据库配置为准，首次登录后请立即修改
```

Windows PowerShell 可将 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。
非空数据库再次导入默认会拒绝；开发阶段需要覆盖导入时必须显式增加 `--reset`。

## 配置一个新检测（全程面板操作，零代码）

以「检测门口是否有猫」为例，任何目标检测模型同理：

1. **上传模型**：「模型管理」→ 选择 `.pt` 文件 → 上传 → 注册并启用。
2. **新建规则**：「规则配置」→ ＋新建规则 → 选一个预置场景，在可视化画布上微调类别、阈值和区域 → 保存。
3. **绑定监控**：「监控管理」→ 编辑摄像头 → 勾选这条规则 → 保存。

下一帧画面即生效，改规则、停用、删除同样即时热生效。

### 内置四种「何时告警」

| 判定逻辑 | 什么情况算违规 | 典型场景 |
|----------|----------------|----------|
| 出现即告警 | 画面中出现所选类别 | 门口有猫、出现明火、打电话 |
| 靠近人员才告警 | 所选类别检出且贴着人员 | 手上有烟头（地上的不算） |
| 装备缺失检查 | 人员没戴该戴的装备 | 未戴安全帽、未穿反光衣 |
| 区域侵入告警 | 所选类别出现在框选区域内（可设滞留秒数） | 有人靠近门口、闯入围墙、危险区域逗留 |

> 需要新的判定方式时需要开发扩展；类别、阈值、区域、模型、摄像头绑定永远不需要。

## 面板功能

| 页面 | 功能 |
|------|------|
| 总览 | 监控在线状态、实时告警流、7 天趋势、磁盘水位 |
| 监控管理 | 增删改/启停/重连/实时预览，全部热生效（不重启主程序） |
| 模型管理 | `.pt` 上传 → 后台校验 → 注册 → 热加载；置信度阈值热更 |
| 数据集 | YOLO 格式：新建 / 上传图片 / 从快照导入 / AI 批量预标注 |
| 在线标注 | 拖拽画框标注，快捷键 1-9 选类别、Del 删除、←→ 切图自动保存 |
| 模型训练 | 选数据集 + 基础模型在线训练，完成后一键注册 `best.pt` |
| 规则配置 | 规则、模板、判定逻辑、类别、阈值、严重度和 graph 全部保存到 `machine.db` |
| 检测测试台 | 上传图片（或取监控帧）用已加载模型试跑，出标注图+JSON |
| 告警记录 | 筛选/分页、确认违规 / 误报标记 / 完结、误报率统计 |
| 快照库 | 按日期/规则浏览，每日量与体积，手动清理 |
| 系统设置 | 统一数据库中的设置段落 + LLM 配置（OpenAI 兼容接口，带测试连接） |
| 日志 | 实时 tail + 级别过滤 |

## 部署与运维

```bash
# 启动（检测 + 面板，端口来自 machine.db 中的 panel 设置）
python main.py

# 独立只读面板（主程序没开也能看历史告警 + 检测测试台）
python -m webapp.server
```

### 导入、导出、备份和恢复

```bash
# 从四类 YAML 显式导入（只执行一次；非空库默认拒绝）
python tools/import_yaml_config.py --config-dir config --database storage/machine.db

# 开发阶段明确覆盖数据库中的配置
python tools/import_yaml_config.py --config-dir config \
  --database storage/machine.db --reset

# 导出不含 API key、密码哈希和 RTSP 明文密码的公共配置
python tools/export_public_config.py --database storage/machine.db \
  --output storage/config-public.json

# 使用 SQLite backup API 备份数据库、模型和截图清单
python tools/backup_machine.py --database storage/machine.db --output storage/backups

# 先校验再原子恢复数据库；需要同时恢复模型/截图时增加 --restore-files
python tools/restore_machine.py storage/backups/machine-YYYYMMDD-HHMMSS \
  --database storage/machine.db --restore-files
```

备份目录包含 `machine.db`、脱敏配置、`manifest.json` 以及模型/截图文件清单。恢复工具会在替换目标前校验 SQLite integrity、外键、JSON 字段和迁移版本。

首次导入的 YAML 文件仍保留在 `config/`，但运行期间 settings、models、cameras、templates、rules、alerts 均以 `storage/machine.db` 为准。

## 内置规则速查

| ID | 名称 | 判定逻辑 | 绑定模型 |
|----|------|----------|----------|
| 1 | 未戴安全帽 | 装备缺失检查 | ppe |
| 13 | 禁火区吸烟 | 靠近人员才告警 | smoking |
| 14 | 持烟 | 靠近人员才告警 | smoking |
| 2 | 门口有人靠近（区域侵入示例） | 区域侵入告警 | 任意含 Person 的模型 |

新规则在面板上创建即可（自动分配 ID）。

## 目录说明

```
config/           开发阶段导入用 YAML 模板，不是运行时配置源
application/      ConfigManager / ConfigSnapshot 运行时配置边界
core/             取流 / 检测 / 分析 / 快照
rules/            规则数据模型与判定逻辑常量
infrastructure/   machine.db、Repository、告警持久化
storage/          运行数据：machine.db / snapshots/ / backups/
tools/            显式 YAML 导入、脱敏导出、备份和恢复工具
webapp/           面板（FastAPI + React SPA，构建产物在 webapp/spa/dist）
```

## 开发说明

```bash
# 后端回归测试
python -m unittest discover -s tests -v

# Python 编译检查
python -m compileall application core infrastructure tools webapp

# 前端（仅改界面时需要；日常运行不需要 Node）
cd webapp/spa && npm install && npm run build
```

`storage/`、真实模型、截图、日志和包含凭据的本地配置不应提交 Git。
