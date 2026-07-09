# Auto Accept - LLM 驱动的 LLM CLI 权限审查工具

## 任务目标
做一个工具，自动接受 LLM CLI 的 yes/no 选项：
- **默认行为**：一律同意（yes）
- **可选增强**：将命令/提示词发送给 LLM 判断是否需要拒绝
- **拒绝规则**：除非 LLM 回复**一定要拒绝**，否则统统同意
- **配置要求**：LLM API 参数（endpoint、key、model 等）可自由配置

## 调研结论：PermissionRequest Hook 路线走不通

经测试和搜索确认，LLM CLI 的 PermissionRequest hook 在 Windows 上有已知问题：
- **Windows 回归**（#28964）：v2.1.47+ 的 hook shell 切换导致 PermissionRequest 完全不触发
- **竞态条件**（#12176）：hook 异步执行，慢速 LLM hook 来不及拦截就已弹窗
- **VSCode 不支持**（#13203）：VSCode 扩展版完全不触发 PermissionRequest hook

## 新方案：独立终端监控 + 画面变化检测

不依赖 LLM CLI 内部机制，通过截图 + 画面变化检测在 OS 层面工作。

### 原理
终端平时画面稳定，权限提示出现时底部画面突变 → 检测到突变 → 自动按 Enter

### 两种检测模式
| 模式 | 原理 | 依赖 | 准确性 |
|------|------|------|--------|
| diff（默认） | 对比连续截图，检测底部像素变化 | 零额外依赖 | 中高 |
| ocr（增强） | tesseract OCR 识别提示文字 | tesseract-ocr | 高 |

## 文件结构
```
auto-accept/
  STATUS.md                 ← 本文件
  PLAN_monitor.md           ← 终端监控实现方案
  README.md                 ← 使用说明（含竞品对比表）
  config.yaml               ← 配置文件（含 monitor 段）
  requirements.txt          ← Python 依赖
  auto_accept.py            ← Hook + Wrapper 模式（Hook 在 Windows 上不可用）
  llm_checker.py       ← LLM API 客户端
  terminal_monitor.py       ← 【新】独立终端监控工具
```

## 当前进度
- [x] 竞品调研
- [x] PermissionRequest Hook 模式（在 Windows 上不可用！）
- [x] PTY Wrapper 模式
- [x] 一键安装/卸载 hook
- [x] 测试模式
- [x] 三种审查模式
- [x] LLM API 集成
- [x] README（含竞品对比表）
- [x] 调研确认 Hook 路线在 Windows 上不可行
- [x] 独立终端监控工具（terminal_monitor.py）
  - [x] 画面变化检测（diff，默认，零依赖）
  - [x] OCR 文字检测（ocr，需 tesseract）
  - [x] 全局热键切换（Ctrl+Shift+A）
  - [x] 单次响应热键（Ctrl+Shift+X）
  - [x] 窗口自动定位
  - [x] LLM 审查集成（可选）

## 下一步
1. 用户在实际 LLM CLI 会话中测试 terminal_monitor.py
2. 根据实际效果调整 diff_threshold / stable_frames 等参数
3. 可选：安装 tesseract 启用 OCR 模式（更精准）
4. 可选：上传到 GitHub
