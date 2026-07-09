# 独立终端监控工具 —— 实现方案

## 背景

PermissionRequest hook 在 Windows 上已知不可用（GitHub #28964），且存在竞态条件（#12176）。
需要一个不依赖 LLM CLI 内部机制的独立工具。

## 方案：截图 + OCR + 模拟键击

```
┌──────────────────────────────────┐
│  terminal_monitor.py             │
│                                  │
│  循环:                           │
│  1. 找到终端窗口                  │
│  2. 截取底部区域 (40%)            │
│  3. OCR 提取文字                  │
│  4. 匹配权限提示关键词             │
│  5. 匹配到 → 模拟键盘 Accept      │
│  6. 等待 scan_interval 秒        │
│  7. 重复                          │
│                                  │
│  热键: Ctrl+Shift+A 开关监听     │
└──────────────────────────────────┘
```

## 技术选型

| 组件 | 库 | 原因 |
|------|-----|------|
| 截图 | `pyautogui` + `PIL` | 成熟稳定，跨平台 |
| OCR | `pytesseract` | 离线，免费，准确 |
| 键盘模拟 | `pyautogui` | 同库，API 简单 |
| 热键 | `pynput` | 支持全局热键 |
| 配置 | `pyyaml` | 已有依赖 |

## 两种响应模式

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| `always_yes` | 检测到提示就按 Enter | 日常，零延迟 |
| `llm` | OCR 文字发给 LLM 审查，允许才按 | 高风险环境 |

## 检测策略

只截取终端窗口底部 40% 区域（提示通常出现在那里），OCR 后正则匹配：
- `[y/n]`, `(y/n)`, `yes/no`
- `Do you want to proceed`
- `Allow this tool`
- `Proceed?`, `Continue?`
- `Accept`, `Confirm`
- 中文: `是否`, `确认`, `允许`

## 窗口定位

优先级：
1. `--select-window` 手动选择
2. 配置文件指定的窗口标题关键词
3. 自动查找标题含 "llm" 的窗口
4. 使用当前活动窗口

## 文件变更

- 新增 `terminal_monitor.py`
- 更新 `config.yaml` 加 `monitor` 配置段
- 更新 `requirements.txt` 加新依赖
- 更新 `STATUS.md`
