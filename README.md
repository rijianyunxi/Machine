# Machine · 机器视觉不安全行为检测系统

工地不安全行为检测系统（YOLOv8）：RTSP 监控取流 → 多模型检测 → 规则判定 → 快照/告警入库，
带中文 Web 管理面板（端口 8000）。

## 快速开始

```bash
# 1. 安装依赖（uv 环境）
uv pip install -r requirements.txt --python .venv/bin/python

# 2. 配置监控
#    编辑 config/cameras.yaml，填入 RTSP 地址和启用的规则 ID

# 3. 启动（检测 + 面板一起跑）
source .venv/bin/activate
python main.py
#    面板: http://localhost:8000  默认账号 admin / admin（config/settings.yaml 可改）
```

## 没有摄像头也能开发

```bash
# 生成测试视频（取自 ultralytics 自带样例图）
python scripts/make_test_video.py

# 用测试配置跑（真实监控已置为停用，另有本地视频监控 CAM_TEST_1）
python main.py --config config_test
```

## 面板功能

| 页面 | 功能 |
|------|------|
| 总览 | 监控在线状态、实时告警流、7 天趋势、磁盘水位 |
| 监控管理 | 增删改/启停/重连，**全部热生效**（不重启主程序） |
| 模型管理 | .pt 上传 → 后台校验 → 注册 → 热加载；阈值热更；类别表 `0:Hardhat` 格式展示 |
| 数据集 | YOLO 格式：新建 / 上传图片 / 从快照导入 / AI 批量预标注 |
| 在线标注 | 画框标注（拖拽/移动/缩放），快捷键 1-9 选类别、Del 删除、←→ 切图自动保存；**AI 预标注**（本地模型）与 **LLM 建议**（视觉大模型识别，可解析 JSON 框应用） |
| 模型训练 | 选数据集 + 基础模型在线训练（子进程，崩溃不影响检测），实时进度/日志，完成后一键注册 best.pt |
| 规则配置 | 规则模板化在线编辑（参数/绑定模型/分配监控），存 `config/rules.yaml` |
| 检测测试台 | 上传图片（或取监控帧）用已加载模型试跑，出标注图+JSON |
| 告警记录 | 筛选/分页、确认违规 / 误报标记 / 完结、误报率统计 |
| 快照库 | 按日期/规则浏览，每日量与体积，手动清理 |
| 系统设置 | settings.yaml 全部可改段落 + **LLM 配置**（OpenAI 兼容 base_url/api_key/model，带测试连接） |
| 日志 | 实时 tail + 级别过滤 |

> LLM 用于标注辅助：在「系统设置 → 大模型 (LLM)」配置 OpenAI 兼容接口并启用，
> 标注页即可用「LLM 建议」让视觉大模型识别目标，能解析其返回的 JSON 框直接应用。

独立只读模式（主程序没开也能看历史 + 用测试台）：

```bash
python -m webapp.server --config config
```

## 规则 ID 速查

| ID | 名称 | 模板 | 绑定模型 |
|----|------|------|----------|
| 1 | 未戴安全帽 | ppe_absence | ppe |
| 13 | 禁火区吸烟 | presence_near_person | smoking |
| 14 | 持烟 | presence_near_person | smoking |

新规则在面板上创建即可（自动分配 ID），模板参数全部可调。

## 目录说明

```
config/           正式配置（settings.yaml / cameras.yaml / rules.yaml 自动生成）
config_test/      本地开发测试配置（真实监控停用）
core/             取流 / 检测 / 分析 / 快照
rules/            规则模板引擎（rules.yaml 驱动）
storage/          alerts.db / snapshots/ / test_results/
webapp/           面板（FastAPI + 原生 JS，无前端构建）
scripts/          测试视频生成 / 监控连通性测试 / 训练脚本
```

## 设计与优化文档

- [PANEL_DESIGN.md](PANEL_DESIGN.md) — 面板设计方案（v0.3）
- [OPTIMIZATION.md](OPTIMIZATION.md) — 代码优化项清单（含完成状态）
