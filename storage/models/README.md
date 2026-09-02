# 模型权重目录

基础权重文件放在本目录，应用会从 `storage/models/` 加载；用户上传和训练生成的模型也统一写入这里：

| 文件 | 说明 |
|------|------|
| yolov8n.pt | 官方预训练（训练基础模型，ultralytics 会自动下载） |
| yolov8-ppe.pt | PPE 检测（Hardhat / NO-Hardhat / Person 等 10 类） |
| yolov8-smoking.pt | 吸烟检测（cigarette / smoking） |

也可以在面板「模型管理」页直接上传 .pt 导入（后台校验后注册），或用
「模型训练」页训练后一键注册。
