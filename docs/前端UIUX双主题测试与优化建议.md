# 前端 UI/UX 双主题测试与优化建议

- **测试日期**：2026-09-05
- **测试对象**：`/Users/song/work/Machine/webapp/spa`
- **结论性质**：本轮为 UI/UX 测试与优化建议，未直接修改前端业务代码。
- **测试方式**：真实 Vite + React SPA 渲染；Playwright/Chromium 自动化；通过请求拦截提供隔离的合成 API 与 SVG 图片数据。
- **安全边界**：未连接真实后端、摄像头、LLM、训练任务或数据库；写请求均被拦截并记录。

## 1. 测试覆盖

### 1.1 页面与视口矩阵

| 主题 | 桌面 1440×1000 | 平板 768×1000 | 手机 390×844 | 登录页 | 空/错/加载态 |
|---|---:|---:|---:|---:|---:|
| Light | 13 页 | 规则、监控、设置深测 | 13 页 | 已测 | 数据集空态、设置 500、总览加载 |
| Dark | 13 页 | 规则、监控、设置深测 | 13 页 | 已测 | 数据集空态、设置 500、总览加载 |

13 个页面：登录、总览、监控管理、模型管理、数据集、在线标注、规则配置、检测测试台、告警记录、快照库、系统设置、日志、模型训练。

### 1.2 交互与可访问性检查

已检查主题切换和刷新持久化、Modal 焦点进入/恢复、Shift+Tab、危险确认 Enter、自定义 Select 键盘操作、登录错误、空/错/加载态、页面错误与 console error、未映射 API、关键元素越界以及按钮 normal/hover/focus/disabled 状态。

## 2. 测试总览

- 主页面矩阵：**52 个组合**（13 页面 × 2 主题 × 2 视口）。
- 页面运行时异常：**0 个 page error**。
- 未映射 API 请求：**0 个**。
- Light/Dark 切换、路由跳转后主题保留、刷新后主题保留：**通过**。
- 自定义 Select 方向键、Enter、Escape：**通过**。
- 手机端真实横向溢出：**6 个组合**，即两个主题的 `模型管理`、`系统设置`、`模型训练`。
- 危险确认 Enter：**失败，已复现为会发送 DELETE 请求**。
- 普通 Modal 焦点陷阱：**失败，Shift+Tab 可离开弹窗进入背景页面**。

原始材料：

- `/Users/song/work/Machine/docs/audit/uiux/audit.cjs`
- `/Users/song/work/Machine/docs/audit/uiux/deep-checks.cjs`
- `/Users/song/work/Machine/docs/audit/uiux/results/results.json`
- `/Users/song/work/Machine/docs/audit/uiux/results/deep-checks.json`
- `/Users/song/work/Machine/docs/audit/uiux/results/summary.json`

## 3. Light 主题结果

### 3.1 优点

- 白色卡片、浅灰画布和深色文字组成清晰的后台层次。
- 总览、告警、快照、标注等信息密集页面在 1440px 下分栏和留白清楚。
- 手机端侧栏会折叠为图标栏；标注、检测测试台、日志等页面在 390px 下基本可读。
- 数据集空态、登录失败等状态有明确文案，不是空白页。

### 3.2 问题

1. **Light 主按钮偏黑，Dark 主按钮为品牌蓝，形成两套产品语言。** Light 实际为 `#232425` 实心黑底，Dark 为蓝色渐变。建议两个主题保留同一个品牌主操作语义，只改变明度，不要把 Light 变成另一种品牌色。
2. **active 导航差异过大。** Light 为浅灰底/黑字，Dark 为蓝色光晕/白字；明暗差异可以保留，但 active、主按钮、focus 应共享同一套 accent 语义。
3. **主题切换会改变组件几何。** Light 卡片圆角 16px、小圆角 10px；Dark 分别为 12px、8px。相同页面切换主题时组件形态变化，不符合同一套组件规范。
4. **辅助文字过浅。** `--muted: #85898e` 测得：白底约 **3.52:1**，Light 背景约 **3.29:1**，浅灰输入面约 **3.14:1**。普通字段标签/正文辅助信息建议改用更深的 secondary token。
5. **普通蓝色文本偏低。** `#0a7aff` 在白底约 **4.01:1**；小字号文本建议使用更深蓝或加下划线，避免仅靠颜色识别。
6. **手机端有真实整页溢出。** 390px 视口下，模型页 `scrollWidth=398`，设置页 `440`，训练页 `438`。

证据：

- `/Users/song/work/Machine/docs/audit/uiux/results/contact-light-1440.jpg`
- `/Users/song/work/Machine/docs/audit/uiux/results/contact-light-390.jpg`
- `/Users/song/work/Machine/docs/audit/uiux/results/light-390-settings.png`
- `/Users/song/work/Machine/docs/audit/uiux/results/light-390-train.png`
- `/Users/song/work/Machine/docs/audit/uiux/results/light-1440-graph-editor.png`

## 4. Dark 主题结果

### 4.1 优点

- 深色背景、深色卡片和蓝色操作色适合长时间监控和日志查看。
- 总览、告警、监控、规则页视觉层级稳定，彩色状态标签能快速区分告警级别。
- Dark `--muted: #7a8aa0` 在深色 surface 上约 **5.20:1**，辅助文字可读性明显优于 Light 当前 muted。
- 日志保留暗色终端面板是合理的工作区语义，不建议简单翻成白色日志框。

### 4.2 问题

1. **Dark CTA 渐变上的白字偏低。** 端点测量约为 `#fff/#4d9fff = 2.72:1`、`#fff/#3b82f6 = 3.68:1`；按钮文字为小字号，建议深蓝底配白字，或浅蓝底配深色文字。
2. **日志 INFO 对比度不足。** `#64748b` 在 `#0b101a` 上约 **4.00:1**；当前日志字号约 11.8px，建议改亮并抽成日志专用 token。
3. **active、CTA、输入 focus 的蓝色强度不完全统一。** 建议集中定义 `accent / accent-hover / accent-soft / focus-ring`。
4. **手机端和 Light 有同样的布局缺陷。** 说明是共用响应式布局问题，不应为两主题分别打补丁。
5. **固定暗色日志需显式建模。** 建议使用 `--log-bg / --log-text / --log-info / --log-warn / --log-error`，避免未来误用页面主题 token。

证据：

- `/Users/song/work/Machine/docs/audit/uiux/results/contact-dark-1440.jpg`
- `/Users/song/work/Machine/docs/audit/uiux/results/contact-dark-390.jpg`
- `/Users/song/work/Machine/docs/audit/uiux/results/dark-390-settings.png`
- `/Users/song/work/Machine/docs/audit/uiux/results/dark-390-train.png`
- `/Users/song/work/Machine/docs/audit/uiux/results/dark-1440-graph-editor.png`

## 5. P0/P1 优化项

### P0-01：危险确认框 Enter 误确认

**复现**：监控管理 → 删除 → 确认框默认将焦点放在“取消” → 按 Enter。

**结果**：即使“取消”有焦点，仍执行 `done(true)` 并发送 `DELETE /api/cameras/cam-01`。测试请求被拦截，没有真实删除，但行为已确认。

**代码位置**：`/Users/song/work/Machine/webapp/spa/src/ui/Confirm.tsx` 的 Enter 键处理。

**建议**：Enter 只触发当前焦点按钮的 click；或仅当焦点明确在确认按钮时执行确认。增加回归断言：取消焦点 + Enter 不产生写请求，确认焦点 + Enter 只产生一次写请求。

### P1-01：Modal 没有焦点陷阱和可访问名称

实测 `Shift+Tab` 可从新增监控弹窗离开到背景页“删除”按钮。Modal 虽有 `role="dialog" aria-modal="true"`，但没有 `aria-labelledby`/`aria-label`，也没有 focus trap 或 inert 背景。

**代码位置**：

- `/Users/song/work/Machine/webapp/spa/src/ui/Modal.tsx`
- `/Users/song/work/Machine/webapp/spa/src/ui/Confirm.tsx`

**建议**：标题生成稳定 id 并被 dialog `aria-labelledby` 引用；Tab/Shift+Tab 在弹窗内循环；锁定 body 滚动；Confirm 补 `aria-labelledby="confirm-title"` 和 `aria-describedby="confirm-msg"`；多层弹窗统一维护 focus stack。

### P1-02：手机端真实横向溢出

| 页面 | Light | Dark | 测量 |
|---|---:|---:|---|
| 模型管理 | 398px | 398px | 视口 390px，卡片超出 8px |
| 系统设置 | 440px | 440px | `minmax(360px,1fr)` 与内容边距叠加 |
| 模型训练 | 438px | 438px | 固定宽度控件/历史表格撑宽 |

监控和告警表格虽较宽，但被 `.table-wrap { overflow-x:auto }` 包装；属于滚动体验优化，不等同于整页溢出。

**代码位置**：`/Users/song/work/Machine/webapp/spa/src/pages/Settings.tsx`、`Train.tsx`、`/Users/song/work/Machine/webapp/spa/src/styles/spa.css`。

**建议**：520px 以下 grid 使用 `minmax(0,1fr)` 单列；`.w320` 改为 `width:100%; max-width:100%`；表格增加横向滑动提示或移动卡片视图。验收标准：390px 下 `document.documentElement.scrollWidth === 390`，除明确内部滚动容器外无元素超出视口。

### P1-03：统一双主题品牌色与组件几何

| Token | Light 建议 | Dark 建议 | 语义 |
|---|---|---|---|
| `--accent` | `#2563eb` | `#60a5fa` | 主操作/链接 |
| `--accent-hover` | `#1d4ed8` | `#93c5fd` | hover |
| `--on-accent` | `#fff` | `#0f172a` | 强调色上的文字 |
| `--focus-ring` | `#2563eb` | `#60a5fa` | 键盘焦点 |
| `--text-secondary` | `#526176` | `#aab8c9` | 普通辅助文本 |
| `--text-tertiary` | `#64748b` | `#7a8aa0` | 次要说明 |
| `--radius` | `12px` | `12px` | 卡片/大容器 |
| `--radius-sm` | `8px` | `8px` | 控件/标签 |

建议色值抽样对比度：Light 主按钮约 5.17:1，Light hover 约 6.70:1，Dark 主按钮使用深色文字约 7.02:1，Dark 辅助文本约 6.01:1。实际落地仍需对渐变、透明叠层、disabled、hover、focus、pressed 全状态复测。

### P1-04：表单 label/id 与错误关联

登录页输入框依靠 label 文案和 placeholder，输入框没有稳定 `id`/`htmlFor` 关联；自动化审查将用户名、密码识别为未命名字段。

**代码位置**：`/Users/song/work/Machine/webapp/spa/src/pages/Login.tsx`，以及设置页、各表单弹窗和自定义 Select。

**建议**：每个输入项使用唯一 id，label 通过 `htmlFor` 关联；placeholder 只做示例；错误信息与输入框用 `aria-describedby` 关联，错误时加 `aria-invalid="true"`。

### P1-05：错误态会停留在“加载中”

Settings 接口 500 隔离测试中，页面可见文案停留在“加载中…”，同时出现预期错误日志；用户无法知道加载失败，也没有重试入口。

**代码位置**：`/Users/song/work/Machine/webapp/spa/src/pages/Settings.tsx` 的初始加载与 `refresh`。

**建议**：明确区分 `loading / success / empty / error`；错误态显示失败原因摘要和“重试”；轮询失败时保留旧数据并显示非阻塞提示。

## 6. P2 优化项

1. 移动端表格增加“可横向滑动”提示，或将状态/名称/操作抽成卡片。
2. `mini` 按钮可保留桌面密集布局，但手机关键操作应尽量提供约 44px 高度或扩大行点击区。
3. 日志颜色统一迁移到日志专用 semantic token。
4. 复测 reduced-motion 下 Modal、Toast、spinner 的状态反馈和焦点移动。
5. 在线/离线、启用/停用、已确认/误报不能只依靠颜色，保留文字或图标差异。
6. 主题切换保持布局尺寸不变，避免圆角、字体、按钮高度、导航宽度跳变。
7. 为每个路由设置明确 document title，方便多标签和问题排查。
8. 图表、标注图、快照提供说明或替代文本，图例使用文字而不是只靠颜色。

## 7. 推荐实施顺序

### 第 1 批：交互安全与手机可用性

修复 Confirm Enter 误确认；补 Modal/Confirm aria name、focus trap、body scroll lock；修复 Settings/Train/Models 的 390px 溢出；给 Settings 错误态增加重试。

### 第 2 批：统一设计 token

合并 Light/Dark 的圆角、控件高度、spacing、字体层级；统一品牌蓝的主操作、active、focus、link；将硬编码颜色迁移到 semantic tokens；重新测正文、辅助文本、按钮、标签、日志、disabled 对比度。

### 第 3 批：移动端表格与表单可访问性

表格移动卡片化或增加可发现的横向滚动；补 label/id、aria-describedby、aria-invalid、dialog labelledby；为 icon-only controls 补 aria-label/title；增加键盘和 reduced-motion 回归用例。

## 8. 后续验收门槛

- Light/Dark 各 13 页面 × 桌面/手机通过截图回归。
- 390px 下整页无非预期横向溢出。
- 普通文本达到 4.5:1；大文本至少 3:1；非文本控件边界和 focus ring 单独检查。
- 危险确认：取消焦点按 Enter 不产生写请求；确认焦点按 Enter 只产生一次写请求。
- Modal Tab/Shift+Tab 不离开弹窗，Escape 关闭后焦点恢复。
- 所有可见表单控件具有关联 label 或明确 aria name。
- 错误、空态、加载态在两个主题均有可读文案和恢复路径。
- 页面布局 token 在 Light/Dark 间一致，主题差异只体现在语义颜色、阴影、表面层级等视觉 token。

## 9. 复现命令

```bash
cd /Users/song/work/Machine/webapp/spa
npm run dev -- --host 127.0.0.1
```

另开终端执行：

```bash
cd /Users/song/work/Machine
PLAYWRIGHT_MODULE=/Users/song/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
UI_AUDIT_BROWSER=/Users/song/Library/Caches/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-mac-arm64/chrome-headless-shell \
node docs/audit/uiux/audit.cjs

PLAYWRIGHT_MODULE=/Users/song/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright \
UI_AUDIT_BROWSER=/Users/song/Library/Caches/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-mac-arm64/chrome-headless-shell \
node docs/audit/uiux/deep-checks.cjs
```

> 脚本只对 API 使用隔离 fixture；自行修改 fixture 或拦截规则后，仍需确保不访问真实摄像头、训练、LLM 或数据库。
