# 前端 UI/UX 优化实施与回归记录

日期：2026-09-05。对应《前端UIUX双主题测试与优化建议》。本次只修改 SPA 和审查脚本，不改动既有后端整改内容。

## 已实施

| 问题 | 实施内容 | 验证 |
|---|---|---|
| 取消焦点 Enter 误删除 | 删除全局 Enter 确认逻辑，采用按钮原生键盘行为 | 两主题：取消无 DELETE，确认只有一次 DELETE |
| 弹窗焦点与背景操作 | 共用 useDialog：Tab 循环、focusin 约束、嵌套弹窗栈、背景 inert、滚动锁、关闭恢复焦点 | 两主题：普通弹窗及规则画布内嵌套确认测试通过；Escape 只关闭顶层 |
| 弹窗名称缺失 | Modal 标题使用 useId + aria-labelledby；确认框补可访问名称/描述 | 按名称定位 dialog/alertdialog 通过 |
| 手机整页溢出 | 设置网格自适应最小宽度；grid 子项 min-width:0；固定宽控件 max-width:100%；手机页头可换行；登录卡片适配 320px | 模型、设置、训练、登录在 320/390/768px 双主题通过 |
| 双主题风格分叉 | 删除 Light 全局黑色按钮排除链与黑色品牌覆盖；共同蓝色品牌、相同圆角/布局；独立明暗语义色 | Light 主操作 #2563eb/白字；Dark #60a5fa/#0f172a；圆角 12px/8px 两主题一致 |
| 对比度不足 | Light muted 调整为 #526176；Dark 主按钮改深字浅蓝底；日志抽取独立 tokens，INFO 改 #94a3b8 | 主按钮配色计算对比度 Light 5.17:1、Dark 7.02:1；日志 INFO 7.42:1 |
| 设置错误态 | 失败提示、重试按钮、加载状态；刷新失败保留现有内容 | 模拟 500 无未处理异常，重试后恢复 |
| 表单名称 | 登录标签/错误关联；设置常规字段及 LLM 模型名；训练字段；模型置信度；监控主要字段；日志 Select 名称 | 登录可用 getByLabel 定位，错误有 aria-invalid/描述；自定义 Select id 移到实际触发按钮 |
| 折叠导航缺名称 | NavLink 增加 aria-label/title | 320/390/768px 可访问树仍有中文导航名称 |
| 其他 | 各业务页 document title；登录 title；输入/链接 focus ring；手机 mini 按钮扩大；表格横向滑动提示 | 构建与页面回归通过 |

## 回归结果

- 13 页面 × Light/Dark × 1440/390，共 **52 个主页面组合**。
- 运行时 page error：**0**；未映射请求：**0**。
- 主矩阵整页横向溢出：修复前 6 个组合，修复后 **0**。
- 监控、告警、训练历史表格仍允许内部横向滚动，测试中的元素越界记录不等于整页溢出。
- 关键行为断言：两主题全部通过；包含取消/确认 Enter、焦点循环/恢复、嵌套 Escape、滚动锁、登录错误、设置失败/恢复、320/390/768px 布局和导航名称。
- 补充深测：两主题的 1440/768/390px 规则编辑、监控弹窗、LLM 设置，以及按钮 normal/hover/focus/disabled。
- `npm --prefix webapp/spa run build` 通过；`git diff --check` 通过。

原始审查证据保留在 `/Users/song/work/Machine/docs/audit/uiux/results`，未用修复后截图覆盖。
修复后结果和截图位于 `/Users/song/work/Machine/docs/audit/uiux/after`。
回归断言脚本：`/Users/song/work/Machine/docs/audit/uiux/regression.cjs`。

## 复现

先在 SPA 目录启动 Vite（127.0.0.1:5173），再在项目根目录执行：

```sh
export PLAYWRIGHT_MODULE=/Users/song/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright
export UI_AUDIT_BROWSER=/Users/song/Library/Caches/ms-playwright/chromium_headless_shell-1217/chrome-headless-shell-mac-arm64/chrome-headless-shell
export UI_AUDIT_OUT=/Users/song/work/Machine/docs/audit/uiux/after
node docs/audit/uiux/regression.cjs
node docs/audit/uiux/audit.cjs
node docs/audit/uiux/deep-checks.cjs
```

这些测试使用合成 API/图片；不执行真实删除、训练、LLM 调用或摄像头访问。

## 边界与后续

本次完成审查发现的核心交互缺陷和共用主题/响应式整改，不代表已完成全站 WCAG 合规认证。以下仍可进一步做：

- 所有业务弹窗字段的 label/错误定位全量检查，目前优先覆盖了上述常用字段。
- 长表格移动卡片视图、固定关键列；当前采用安全的内部滚动和提示。
- 全站图表文本替代、画布键盘操作、全部色彩与透明叠层状态的系统化可访问性检查。
- 所有页面网络异常及会话初始加载体验；本次错误恢复重点是设置页。
- Safari/Firefox、真实移动设备、屏幕阅读器和真实后端联调尚未覆盖。
