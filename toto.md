# SQLite 配置迁移 TODO（开发阶段重构版）

> 更新时间：2026-09-01  
> 项目阶段：开发中，尚未上线  
> 适用目录：`D:\work\yolo-web-ui\Machine`

## 1. 结论与执行原则

当前项目可以直接进行配置存储重构，不需要为已上线数据、旧版本客户端或滚动发布保留长期兼容层。

本次整改采用以下原则：

1. **SQLite 是动态业务配置的唯一可写数据源**。
2. **先建立 `Repository + ConfigManager + ConfigSnapshot` 边界，再把后端从 YAML 切换成 SQLite**。这不是为了兼容线上旧版本，而是为了避免数据库读写、运行时刷新和业务逻辑同时耦合，降低重构后继续出现“页面已保存但检测线程仍读旧配置”的概率。
3. **不做 YAML/SQLite 双写**。YAML 只保留一次性导入、人工导出和问题排查能力。
4. **可以直接重建开发数据库**。迁移脚本仍要保留，但不为无价值的开发期历史数据增加复杂兼容代码。
5. **运行时不逐帧查询 SQLite**。数据库变更后生成不可变配置快照，检测循环每轮只读取一个完整快照。
6. **配置与告警合并到一个数据库**：统一使用 `storage/machine.db`，共享连接工厂、迁移体系、备份流程和 SQLite 参数。
7. **模型文件和截图仍保存在文件系统**，数据库只保存路径、哈希、状态和元数据。
8. **敏感信息不能通过配置接口明文返回**，也不能混在通用 settings JSON 中随页面读取。

---

## 2. 当前代码事实与必须先修的问题

### 2.1 YAML 并发覆盖不是唯一、也不是最紧急的问题

`D:\work\yolo-web-ui\Machine\webapp\config_service.py` 已经具备：

- `threading.RLock`；
- section merge；
- 临时文件写入后 `os.replace()`；
- 配置备份。

因此，同一进程内设置页面并发写 YAML 导致整份文件损坏的问题已基本缓解。SQLite 迁移的主要价值应当是：

- 统一配置实体和关联关系；
- 提供事务、约束、revision 和审计；
- 消除多个 YAML 文件之间的一致性问题；
- 解决运行时配置快照不能及时刷新；
- 为摄像头、规则、模型的 CRUD 提供稳定主键。

仍存在的 YAML 风险：

- 多进程写入没有统一锁；
- `rules.yaml` 等路径未必使用同一套原子写和备份机制；
- 浏览器可能基于旧数据覆盖新数据；
- 多文件变更无法放在同一事务中。

### 2.2 敏感配置存在接口泄露风险

当前 `D:\work\yolo-web-ui\Machine\webapp\state.py` 中的 `RuntimeState.get_settings()` 会返回完整设置，`GET /api/settings` 因而可能暴露：

- `llm.api_key`；
- `panel.password`；
- 后续可能加入的 RTSP 用户名和密码。

虽然 LLM 专用接口有掩码逻辑，但设置页面读取的是通用 settings 接口，不能依赖专用接口兜底。

必须先完成：

- 所有读取接口都不返回密钥明文；
- 返回 `configured: true/false` 或掩码展示值；
- 更新请求中空值表示“保持原值”，不能表示清空；
- 清空密钥必须使用独立、明确确认的操作；
- 日志、异常和审计记录不得记录密钥值；
- 面板密码只保存密码哈希，不保存可逆明文。

### 2.3 当前运行时配置可能与保存结果不一致

`D:\work\yolo-web-ui\Machine\main.py` 启动时加载 `_cameras_config`，处理循环继续从该对象读取摄像头列表。页面保存摄像头后，`RuntimeState` 即使更新了 YAML 和 `CameraManager`，也不等于检测循环已经换成新配置。

可能出现：

- 新摄像头流已创建，但检测循环不处理；
- 删除摄像头后旧项仍在处理列表中；
- 规则绑定和区域修改后继续使用旧参数；
- 同一轮处理读取到一半新、一半旧的配置。

SQLite 迁移前后都必须通过统一 `ConfigSnapshot` 解决，不能只把 `yaml.safe_load()` 替换成 SQL 查询。

---

## 3. 目标架构

### 3.1 分层职责

新增以下边界：

#### `ConfigRepository`

只负责持久化，不直接操作检测器、摄像头流或 Web 状态：

- 查询和保存 settings、models、cameras、rules；
- 维护关联表；
- 开启事务；
- 校验 revision；
- 更新全局配置 revision；
- 写配置审计记录。

建议路径：

- `infrastructure/persistence/config_repository.py`
- `infrastructure/persistence/sqlite_connection.py`
- `infrastructure/persistence/migrations/`

#### `ConfigManager`

负责应用层配置协调：

- 调用 Repository；
- 执行业务校验；
- 构建完整 `ConfigSnapshot`；
- 原子替换当前快照；
- 发布配置变更事件；
- 协调 `CameraManager` 启停流；
- 向 API 返回脱敏 DTO。

建议路径：

- `application/config_manager.py`
- `application/config_snapshot.py`

#### `ConfigSnapshot`

不可变运行时对象，至少包含：

- 全局设置；
- 启用的模型；
- 启用的摄像头；
- 摄像头对应的规则；
- 规则参数、区域和模型绑定；
- 全局 `revision`。

要求：

- 构建成功后一次性替换引用；
- 检测循环每轮只获取一次快照引用；
- 不允许业务线程修改快照内部字典或列表；
- 数据库变更失败或新快照校验失败时，继续使用旧快照。

### 3.2 配置刷新机制

同一进程：

1. API 调用 `ConfigManager` 保存配置；
2. Repository 在事务中更新数据和 revision；
3. 提交成功后重建快照；
4. 原子替换快照；
5. 通知摄像头和规则相关组件处理差异。

多进程预留：

- `config_meta.global_revision` 每次事务递增；
- 非写入进程每 1～2 秒轮询 revision；
- revision 改变时重建快照；
- 当前阶段不引入 Redis、消息队列或文件监听。

### 3.3 写入流程

页面更新不能直接执行零散 SQL。统一流程：

1. API 解析请求并获取 `expected_revision`；
2. `ConfigManager` 做字段和业务校验；
3. Repository 开启 `BEGIN IMMEDIATE` 事务；
4. 检查对象 revision；
5. 更新主表、关联表和审计表；
6. 递增全局 revision；
7. 提交事务；
8. 重建并发布快照；
9. 返回新 revision。

若 revision 不一致，返回 HTTP `409 Conflict`，不能静默覆盖。

---

## 4. 单库设计与 SQLite 基础要求

### 4.1 数据库文件

统一使用一个数据库：

- `D:\work\yolo-web-ui\Machine\storage\machine.db`

`machine.db` 同时保存：

- 全局设置、模型、摄像头、规则模板、规则和关联关系；
- 配置 revision 和审计记录；
- 告警记录及截图元数据。

采用单库的原因：

- 项目仍处于开发阶段，优先降低部署和维护复杂度；
- 只需要一套连接工厂、迁移版本、备份和恢复流程；
- 配置变更与必要的业务记录可以按需放入同一事务；
- 避免管理多个 `.db`、`-wal` 和 `-shm` 文件；
- 当前数据量和并发量适合 SQLite WAL 单库模式。

单库不代表告警与配置强耦合：

- 告警继续保存 `camera_name`、`rule_name` 等历史快照；
- 告警不依赖当前摄像头或规则记录才能展示；
- 告警清理使用短事务和分批删除，避免长时间阻塞配置写入；
- 不在应用运行期间频繁执行全库 `VACUUM`；
- 后续只有在实际出现写入争用、文件膨胀或独立归档需求时，再评估拆库。

### 4.2 每个连接必须设置

- `PRAGMA foreign_keys = ON`；
- `PRAGMA journal_mode = WAL`；
- `PRAGMA busy_timeout = 5000`；
- 合理设置 `synchronous`，默认使用 `NORMAL`；
- Python 连接不能在多个线程中无保护共享；
- 所有写操作必须显式事务管理。

### 4.3 迁移机制

使用版本化、顺序执行、事务化迁移，不要求每条迁移脚本反复执行都幂等。

`schema_migrations`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | INTEGER PK | 迁移版本 |
| `name` | TEXT | 迁移名称 |
| `checksum` | TEXT | 脚本校验值 |
| `applied_at` | INTEGER | Unix 时间戳 |

规则：

- 一个迁移要么完整成功，要么完整回滚；
- 已执行迁移的 checksum 改变时启动失败并提示；
- 开发阶段允许删除数据库后从 0 重建；
- 禁止启动时使用大量 `CREATE TABLE IF NOT EXISTS` 代替正式迁移版本。

---

## 5. `machine.db` 配置表建议结构

### 5.1 `config_meta`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 固定为 1 |
| `global_revision` | INTEGER | 任一配置事务成功后递增 |
| `updated_at` | INTEGER | 最后更新时间 |

### 5.2 `settings_sections`

全局设置按 section 保存 JSON，不再拆成过细的 EAV 行。

| 字段 | 类型 | 说明 |
|---|---|---|
| `section` | TEXT PK | 如 `detection`、`storage`、`llm`、`panel` |
| `value_json` | TEXT | 非敏感设置 JSON |
| `revision` | INTEGER | 乐观锁版本 |
| `updated_at` | INTEGER | 更新时间 |
| `updated_by` | TEXT | 操作者 |

约束：

- `CHECK(json_valid(value_json))`；
- Repository 在事务内做 patch/merge；
- 默认值和字段 schema 保留在代码中；
- 新默认字段只补缺失项，不能覆盖已有值；
- 密钥和密码不得放入 `value_json`。

### 5.3 敏感信息

敏感信息与普通 settings 分离：

- `panel.password_hash`：只保存强密码哈希；
- `llm.api_key`：优先从环境变量读取；如必须落库，保存受 Windows DPAPI 或等价本机密钥保护的密文；
- RTSP 密码：同样按可逆密钥处理，不通过普通配置接口返回；
- 数据库中不得同时保存旧密码明文和新哈希；
- 审计只记录“已设置/已清空”，不记录值。

开发阶段可以先使用环境变量完成第一版，不能为了赶迁移把密钥明文放入通用 JSON。

### 5.4 `models`

建议字段：

- `id` INTEGER PRIMARY KEY；
- `name` TEXT NOT NULL UNIQUE；
- `file_path` TEXT NOT NULL；
- `model_type` TEXT NOT NULL；
- `classes_json` TEXT；
- `sha256` TEXT；
- `file_size` INTEGER；
- `validation_status` TEXT；
- `validation_error` TEXT；
- `enabled` INTEGER NOT NULL DEFAULT 1；
- `revision` INTEGER NOT NULL DEFAULT 1；
- `created_at`、`updated_at` INTEGER。

要求：

- 模型文件不存 BLOB；
- 保存前校验路径、文件存在性和模型可加载性；
- JSON 字段增加 `json_valid` 约束；
- 删除被规则引用的模型时必须拒绝或先解除绑定。

### 5.5 `cameras`

建议字段：

- `id` INTEGER PRIMARY KEY AUTOINCREMENT；
- `name` TEXT NOT NULL；
- `source_type` TEXT NOT NULL；
- `source_uri` TEXT；
- `enabled` INTEGER NOT NULL DEFAULT 1；
- `config_json` TEXT NOT NULL DEFAULT `'{}'`；
- `revision` INTEGER NOT NULL DEFAULT 1；
- `deleted_at` INTEGER；
- `created_at`、`updated_at` INTEGER。

约束与规则：

- 活跃摄像头名称唯一；
- 采用软删除，ID 不复用；
- `source_uri` 如包含凭据，返回 API 前必须脱敏；
- 摄像头删除只停止流和检测，不删除历史告警及截图。

### 5.6 `rule_templates`

模板数量有限，参数 schema 作为整体 JSON 保存，不建立 `rule_template_params` 表，除非以后确实需要按单个参数查询或审计。

建议字段：

- `id` INTEGER PRIMARY KEY；
- `code` TEXT NOT NULL UNIQUE；
- `name` TEXT NOT NULL；
- `description` TEXT；
- `executor_type` TEXT NOT NULL；
- `executor_version` INTEGER NOT NULL；
- `schema_version` INTEGER NOT NULL；
- `params_schema_json` TEXT NOT NULL；
- `graph_schema_json` TEXT；
- `enabled` INTEGER NOT NULL DEFAULT 1；
- `revision` INTEGER NOT NULL DEFAULT 1；
- `created_at`、`updated_at` INTEGER。

参数名应以当前业务字段为准，例如：

- `target_classes`；
- `zones`；
- `min_confidence`；
- `dwell_seconds`。

模板版本只能描述数据 schema 和执行器版本，不能承诺 Python 执行代码升级后仍永久保留旧语义。升级时应显式迁移规则数据，或保留对应 executor 实现。

### 5.7 `rules`

建议字段：

- `id` INTEGER PRIMARY KEY AUTOINCREMENT；
- `template_id` INTEGER NOT NULL；
- `name` TEXT NOT NULL；
- `description` TEXT；
- `enabled` INTEGER NOT NULL DEFAULT 1；
- `params_json` TEXT NOT NULL DEFAULT `'{}'`；
- `graph_json` TEXT；
- `schema_version` INTEGER NOT NULL；
- `revision` INTEGER NOT NULL DEFAULT 1；
- `created_at`、`updated_at` INTEGER；
- 外键 `template_id -> rule_templates.id`，删除策略使用 `RESTRICT`。

要求：

- `params_json`、`graph_json` 增加 `json_valid` 约束；
- 服务端按模板 schema 校验类型、范围、必填项和枚举；
- graph 必须继续经过 `D:\work\yolo-web-ui\Machine\core\rules_graph.py` 校验；
- 未知节点、未知执行器和非法边必须拒绝保存，不能静默跳过。

### 5.8 `rule_models`

| 字段 | 类型 | 说明 |
|---|---|---|
| `rule_id` | INTEGER | 规则 ID |
| `model_id` | INTEGER | 模型 ID |
| `role` | TEXT | 模型在规则中的用途 |
| `sort_order` | INTEGER | 顺序 |

- 主键：`(rule_id, model_id, role)`；
- 两侧外键建议使用 `ON DELETE RESTRICT`；
- 解除绑定应由业务层明确执行。

### 5.9 `camera_rules`

区域天然与摄像头画面坐标相关，因此第一版就支持摄像头级覆盖，不再把它推迟为“可选功能”。

建议字段：

- `camera_id` INTEGER NOT NULL；
- `rule_id` INTEGER NOT NULL；
- `enabled` INTEGER NOT NULL DEFAULT 1；
- `params_override_json` TEXT NOT NULL DEFAULT `'{}'`；
- `revision` INTEGER NOT NULL DEFAULT 1；
- `created_at`、`updated_at` INTEGER；
- 主键 `(camera_id, rule_id)`。

规则：

- `zones` 默认放在 `params_override_json`；
- `dwell_seconds` 等确实随摄像头变化的参数也可覆盖；
- 合并顺序为：模板默认值 < 规则参数 < 摄像头规则覆盖；
- 合并后的最终参数必须再次通过模板 schema 校验。

### 5.10 `config_audit_log`

`updated_by` 只能说明最后一次修改，不能代替审计日志。

建议字段：

- `id` INTEGER PRIMARY KEY AUTOINCREMENT；
- `object_type` TEXT NOT NULL；
- `object_id` TEXT NOT NULL；
- `operation` TEXT NOT NULL；
- `before_json` TEXT；
- `after_json` TEXT；
- `actor` TEXT；
- `revision` INTEGER NOT NULL；
- `created_at` INTEGER NOT NULL。

要求：

- 与配置变更处于同一事务；
- 密钥、密码和 URI 凭据必须脱敏；
- 开发阶段可先实现结构和基本写入，不必先做完整审计页面。

---

## 6. `machine.db` 告警表调整

保留 `D:\work\yolo-web-ui\Machine\infrastructure\persistence\alert_database.py` 的告警访问职责，但将其连接目标改为统一的 `storage/machine.db`，并纳入同一套 connection factory 和 migration runner。开发阶段可直接重建告警表；如需保留现有开发数据，则提供一次性导入脚本。

建议给告警保存以下快照：

- `camera_id`；
- `camera_name`；
- `rule_id`；
- `rule_name`；
- `model_name`（如页面需要）；
- 告警产生时使用的关键参数摘要（可选）。

截图状态不要只依赖一个容易漂移的字符串字段，建议至少包含：

- `snapshot_path`；
- `snapshot_created_at`；
- `snapshot_cleaned_at`。

页面状态判定：

- 路径存在且文件存在：可用；
- `snapshot_cleaned_at` 非空：已按保留策略清理；
- 路径存在、文件不存在、且无清理记录：文件异常缺失。

摄像头或规则改名、停用、软删除后，历史告警继续显示写入时的名称快照，不能依赖当前配置反查名称。

---

## 7. YAML 处理策略

### 7.1 不做双写和长期兼容

切换后：

- 页面只写 SQLite；
- 后台任务只读 `ConfigSnapshot`；
- YAML 不再作为运行时回退；
- 禁止“先写 YAML，再同步 SQLite”或反向双写。

### 7.2 不保留配置 YAML 导入工具

仓库不再提供固定的配置 YAML 和命令行导入工具。配置与告警统一存储在 `storage/machine.db`，初始化、修改、备份和恢复均围绕 SQLite 数据库进行。数据集功能所需的 `dataset.yaml` 属于数据集格式，不属于系统配置。

### 7.3 导出

提供人工触发的导出命令或管理接口：

- 导出非敏感 settings；
- 导出模型、摄像头、规则、模板和关联；
- 密钥仅导出“已配置”状态，不导出明文；
- 导出结果用于排查、评审和备份，不作为运行时数据源。

---

## 8. 页面与 API 约定

### 8.1 更新语义

- 优先使用 PATCH 或明确的 changed-fields 请求；
- 请求必须携带 `expected_revision`；
- 未提交字段保持不变；
- null、空字符串和“未提交”必须有不同语义；
- 关联关系变更必须整体事务提交；
- 冲突返回 `409` 和当前 revision。

### 8.2 敏感字段

读取示例语义：

- `api_key_configured: true`；
- `api_key_masked: "****abcd"`（可选）；
- 不返回真实 key；
- 密码不返回掩码内容，只返回是否已设置。

写入语义：

- 字段缺失：保持原值；
- 提交新值：替换；
- 空字符串：保持原值，不清空；
- 清空：调用独立 clear 操作并确认。

### 8.3 服务端校验

前端校验只改善体验，服务端必须再次校验：

- 摄像头 URI 和名称；
- 模型路径和模型状态；
- 模板参数 schema；
- graph 节点和边；
- 规则与模型引用；
- 摄像头区域坐标；
- revision 冲突。

---

## 9. 实施 TODO

由于项目尚未上线，本次不做漫长兼容期，按短分支、小提交、可测试的方式直接切换。

### 阶段 0：修复当前确定问题

- [x] `GET /api/settings` 对 API key、面板密码和 URI 凭据统一脱敏。
- [x] 面板密码改为哈希校验，停止保存和返回明文。
- [x] 定义密钥更新、保持和清空的明确 API 语义。
- [x] 修复 `main.py`、`RuntimeState`、`CameraManager` 之间的摄像头配置同步问题。
- [ ] 给现有规则 YAML 写入补充原子替换和备份，保证迁移期间稳定。
- [x] 为迁移后的 settings/cameras/rules Repository/Manager 读取、更新和删除行为补回归测试。
- [x] 保留并验证 Web 面板 `Request` 导入修复，增加 `create_app()` 启动测试。

完成标准：当前 YAML 版本行为稳定，已知敏感信息不再通过接口泄露。

备注：YAML 写入路径已直接退出运行时链路；因此不再为旧 YAML 写服务增加迁移期双写兼容。

### 阶段 1：建立配置抽象

- [x] 定义 Repository 接口或协议，不暴露 YAML/SQLite 细节。
- [x] 实现 `ConfigManager` 的查询、保存、校验和变更通知入口。
- [x] 定义不可变 `ConfigSnapshot`。
- [x] 检测循环每轮只读取一次 snapshot。
- [x] 摄像头和规则运行组件停止直接持有可变 YAML 字典。
- [ ] 先用现有 YAML 实现临时 Repository，验证业务层边界。

说明：临时 YAML Repository 只用于快速验证抽象，完成 SQLite Repository 后立即删除，不形成双写或长期兼容层。

完成标准：业务层不再直接调用 YAML 文件读写函数，替换 Repository 不需要改检测核心逻辑。

备注：项目直接建立 SQLite Repository，未引入临时 YAML Repository。

备注：未建立临时 YAML Repository，而是直接切换到 SQLite Repository，符合本项目尚未上线、允许直接重构的前提。

### 阶段 2：建立 SQLite 基础设施

- [x] 新增 `machine.db` 统一连接工厂和事务封装。
- [x] 新增正式 migration runner 和 `schema_migrations`。
- [x] 创建 `config_meta`、配置实体表、关联表和审计表。
- [x] 每个连接启用 foreign keys、WAL 和 busy timeout。
- [x] 所有 JSON 字段增加 `json_valid` 约束。
- [x] 明确每个外键的 `RESTRICT`、`CASCADE` 或 `SET NULL` 策略。
- [x] 编写 Repository 事务、冲突和回滚测试。

完成标准：空数据库可以从 0 迁移到最新版；故意制造迁移失败时不会留下半成品 schema。

开发数据处理：原 `storage/alerts.db` 中的 5 条历史告警已事务导入 `storage/machine.db`，旧数据库文件已移除，运行时只保留统一数据库。

### 阶段 3：实现 SQLite Repository 和导入器

- [x] 实现 settings sections 的 patch/merge 和 revision。
- [x] 实现 models、cameras、templates、rules CRUD。
- [x] 实现 `rule_models` 和 `camera_rules` 关联更新。
- [x] 实现全局 revision 和配置审计。
- [x] 实现 YAML 一次性导入器。
- [x] 实现非敏感配置导出器。
- [x] 用当前四类 YAML 数据做完整导入测试。
- [x] 校验 zones 已迁移到摄像头规则覆盖，而不是错误地作为全局规则区域共享。

完成标准：导入后生成的 snapshot 与原配置语义一致，引用、顺序、默认值和 graph 不丢失。

### 阶段 4：切换运行时和 Web API

- [x] `ConfigManager` 默认使用 SQLite Repository。
- [x] 设置、模型、摄像头、规则 API 全部改走 `ConfigManager`。
- [x] 页面保存请求增加 `expected_revision`。
- [x] 冲突时页面提示刷新，不静默覆盖。
- [x] 配置提交成功后原子更新 snapshot。
- [x] 摄像头新增、修改、停用和删除能够正确触发运行时差异更新。
- [x] 规则及区域修改后下一处理轮次生效。
- [x] 删除所有页面和后台任务的 YAML 写路径。

完成标准：断开/删除 YAML 文件后，应用仍可完整启动、配置、检测和产生告警。

已验证：前端生产构建通过；后端统一数据库回归测试覆盖 revision、热同步、导入回滚和敏感字段语义。

备注：运行时已不再自动导入 YAML；仓库内也不再保留固定的系统配置 YAML 或命令行导入工具。

### 阶段 5：调整告警快照和截图状态

- [x] 给 `machine.db` 中的 alerts 表增加 camera/rule 名称快照字段。
- [x] 新告警写入时保存名称快照。
- [x] 页面优先显示告警快照，不反查当前配置名称。
- [x] 增加 `snapshot_cleaned_at`。
- [x] 区分截图可用、已清理和异常缺失。
- [x] 验证摄像头/规则改名和软删除不影响历史告警展示。

完成标准：历史告警不依赖当前配置实体是否存在。

已验证：告警接口返回名称快照与 snapshot_status，历史记录不反查当前配置名称。

### 阶段 6：删除临时兼容代码

- [x] 删除临时 YAML Repository（本项目未建立临时实现，直接切换 SQLite）。
- [x] 删除启动时自动导入和 YAML fallback。
- [ ] 删除重复默认值覆盖逻辑。
  说明：设置页的 `SETTINGS_SCHEMA` 服务于表单校验，外部 YAML 导入数据由导入器单独校验；运行时已删除未使用的规则 seed 默认副本。
- [x] 删除已废弃的 YAML 写入服务和调用点。
- [x] 删除配置 YAML 导入工具，仅保留数据库公共配置导出、备份和恢复工具。
- [x] 删除仓库内的 `config/` YAML 样例、首次导入依赖和命令行导入工具。
- [x] 删除未被运行时引用的 `rules/rules_engine.py` 兼容导出层、死代码 API 和无效的 `--config` 运行时参数。
- [x] 更新 README、配置说明、备份与恢复说明。
- [x] 检查 Git 中不应提交 `machine.db`、`machine.db-wal`、`machine.db-shm`、密钥和实际摄像头凭据。

完成标准：系统配置业务代码不存在直接读写配置 YAML 的路径；数据集 YAML 仅由数据集功能按 YOLO 格式读写。

---

## 10. 测试与验收标准

### 10.1 数据库和迁移

- [x] 空库可一次迁移到最新版本。
- [x] 每个迁移失败时完整回滚。
- [x] 外键、唯一约束、JSON 约束全部生效。
- [x] 删除被引用的模型、模板或规则时行为符合预定策略。
- [ ] SQLite busy 时等待或返回可理解错误，不损坏配置。

### 10.2 导入和导出

- [x] 当前 YAML 可在单个事务中完整导入。
- [x] 非空库默认拒绝再次导入。
- [x] 导入失败不产生部分数据。
- [ ] 导出后关键业务语义可人工检查。
- [x] 导出文件不包含 API key、密码和 URI 凭据明文。

### 10.3 配置一致性

- [x] 两个请求更新同一对象时，旧 revision 请求返回 409。
- [x] 两个请求更新不同对象时互不覆盖。
- [x] 多表关联更新要么全部成功，要么全部回滚。
- [x] 数据库提交失败时运行时继续使用旧 snapshot。
- [x] snapshot 构建失败时不发布半成品配置。
- [x] 应用重启前后配置一致（通过重建 ConfigManager/快照回归测试验证）。

### 10.4 摄像头和检测运行时

- [ ] 新增摄像头后无需重启即可进入检测循环。
- [ ] 停用或删除摄像头后停止拉流和检测。
- [ ] 修改规则、模型、区域后下一处理轮次使用新 snapshot。
- [ ] 一轮处理期间不会混用两个 revision。
- [ ] 数据库不会被逐帧查询。

### 10.5 规则与模板

- [ ] 模板参数名、类型、默认值、范围、顺序和来源完整迁移。
- [ ] `target_classes`、`zones`、`min_confidence` 等字段语义正确。
- [ ] 非法参数和非法 graph 被服务端拒绝。
- [ ] 未知节点和执行器不会被静默执行。
- [ ] 摄像头级 zones 覆盖正确合并并再次校验。

### 10.6 密钥和权限

- [x] 通用设置接口不返回任何密钥明文。
- [x] 页面源码、网络响应、日志和审计记录无明文密钥。
- [x] 空值保存不会意外清空密钥。
- [x] 清空密钥必须是明确操作。
- [x] 面板密码只保存哈希。

### 10.7 告警

- [x] 摄像头或规则改名后，历史告警仍显示原名称。
- [x] 摄像头或规则软删除后，历史告警仍可查看。
- [x] 截图已清理与文件异常缺失能正确区分。
- [x] 告警保留任务采用分批短事务，不会长时间阻塞同库中的配置写入。

---

## 11. 备份与恢复

### 11.1 数据库

- 使用 SQLite backup API 备份统一的 `storage/machine.db`；
- 不直接复制正在写入的 `.db` 文件；
- 备份记录代码版本和 schema migration 版本；
- 恢复后校验配置表、告警表、外键和 JSON 字段；
- 配置校验通过后再构建并发布新 snapshot。

### 11.2 文件

数据库之外还要备份：

- 截图目录；
- 模型文件；
- 非敏感配置导出文件。

截图、模型和数据库之间允许最终一致，但恢复工具必须能报告路径存在而文件缺失的记录。

---

## 11.1 新机器首次启动行为

- [x] 空的 `storage/machine.db` 不再因没有模型而让 `main.py` 直接退出。
- [x] 检测进程可以先启动面板并等待模型注册；模型注册后通过配置快照热加载。
- [x] README 补充空数据库、模型文件和跨机器迁移的操作说明。

约束仍保持不变：模型注册信息只写入 `machine.db`，不会因为扫描 `models/` 目录而隐式生成配置；模型文件和数据库仍需通过备份/恢复或面板上传迁移。

## 12. 本次重构明确不做的事情

- 不保留 YAML/SQLite 双写；
- 不为尚未上线的旧数据库版本维护复杂兼容代码；
- 不把模型文件或截图存成 SQLite BLOB；
- 不把 graph 每个节点强行拆成关系表；
- 不把模板每个参数拆成独立表，除非出现真实查询需求；
- 不在检测帧循环中直接查数据库；
- 不用 `updated_by` 冒充完整审计历史；
- 不承诺仅靠模板版本字段永久保留旧 Python 执行语义；
- 不先引入 Redis、消息队列或分布式锁。

---

## 13. 最终完成定义

满足以下条件后，SQLite 配置迁移才算完成：

1. `machine.db` 是配置和告警数据的统一 SQLite 数据库，动态配置只允许通过 Repository 写入；
2. 运行时只消费不可变 `ConfigSnapshot`；
3. 所有配置更新具备事务、revision、校验和审计；
4. 系统配置只存在于 `storage/machine.db`；数据集 YAML 仅用于 YOLO 数据集格式；
5. 新增、删除、修改摄像头和规则无需重启即可正确生效；
6. 密钥、密码和 RTSP 凭据不会通过 API、日志或审计明文泄露；
7. 历史告警不依赖当前摄像头和规则记录；
8. 仓库不含配置 YAML 时，应用仍能通过测试并完整运行；
9. `machine.db`、截图目录和模型文件有可验证的统一备份与恢复流程。

执行顺序保持为：**先修安全与运行时同步问题 → 建立 Repository/Manager/Snapshot 边界 → 实现 SQLite Repository → 一次性导入 → 直接切换 SQLite → 删除 YAML 兼容代码**。