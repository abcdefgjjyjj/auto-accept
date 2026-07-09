# Auto Accept —— 任务状态

## 项目位置
`<project-root>/auto-accept\`

## 核心目标
做一个**通用工具**，自动接受 LLM CLI 的权限提示，不依赖特定设备配置。

## 三条路线对比

| 路线 | 文件 | 原理 | 状态 |
|------|------|------|------|
| PermissionRequest Hook | `auto_accept.py --hook` | LLM CLI 内置 hook | ❌ Windows 已知不可用（#28964） |
| PTY Wrapper | `auto_accept.py --wrapper` | ConPTY (pywinpty) → 子进程看到真 TTY，wrapper 监控输出 + pty.write 响应 | ✅ 已改为 ConPTY，待测试 |
| 终端截图监控 | `terminal_monitor.py` | 截图 + 画面变化检测 + 键盘模拟 | ⚠️ IME 干扰、误触 |

## 当前焦点：PTY Wrapper

### 架构（已改为 ConPTY）
```
python auto_accept.py --wrapper --mode always_yes -- llm

┌──────────────────────────────────┐
│ auto_accept.py (wrapper 进程)    │
│                                  │
│  PtyProcess.spawn(llm):       │
│    子进程看到的: TTY (isatty=1)  │
│    我们拿到的: 可读写的 PTY 句柄 │
│                                  │
│  监控线程: pty.read(4096)        │
│    → 回显到真实终端              │
│    → strip ANSI 码               │
│    → regex 匹配提示关键词        │
│    → 匹配到 → pty.write(b'\r')   │
└──────────────────────────────────┘
```

### 最近修改（2026-07-08）
1. ~~`stdin=None`（不是 PIPE）→ LLM CLI 以交互 TUI 模式运行~~（废弃）
2. ~~`_respond()` 改用 `SendInput` API 发 Enter~~（废弃，改为 pty.write）
3. `is_prompt_line()` 先 `strip_ansi()` 再匹配正则
4. `_read_pty()` 改为 4096 字节块读取
5. 匹配模式加了 `\bAllow\b`, `\bDeny\b`, `permission` 等 Ink TUI 关键词
6. **【本次】改用 ConPTY (pywinpty)** 替代 subprocess.Popen+PIPE
   - `PtyProcess.spawn()` 创建真伪终端，子进程 isatty()=True
   - `.cmd` 文件自动用 `cmd.exe /c` 包装（CreateProcess 不直接支持）
   - 响应方式从 SendInput 改为 `pty.write(b'\r')` / `pty.write(b'\t')`
   - 移除 `_write` 线程、`_queue`、`_win32_send_key` 等旧代码
   - 新增 `_resolve_cmd()`, `_force_kill()` 方法

### 启动命令
```bash
cd <project-root>/auto-accept
python auto_accept.py --wrapper --mode always_yes -- "C:/Users/user/AppData/Roaming/npm/llm.cmd"
```

### 配置文件
`<project-root>/auto-accept\config.yaml`
- `mode: llm`（auto_accept.py hook 用）和 `monitor.mode: always_yes`（监控用）
- LLM API key 已配置
- 窗口几何已配置 `monitor.window_geometry`

### 最新测试结果（2026-07-08）
- [x] ConPTY + 输入转发 → LLM CLI Ink TUI 正常交互
- [x] 权限提示自动响应 → 同意 2 次（测试中验证通过）
- [x] 英文输入正常
- [ ] 中文输入乱码 → 已改用 `getwch()` + UTF-8 编码转发，待测试
- [ ] 日志与 TUI 输出混杂 → 待优化（去掉 --verbose 时正常）

### 推荐启动命令
```powershell
# 直接启动 LLM（不要套 cmd.exe）
python auto_accept.py --wrapper --mode always_yes -- "C:/Users/user/AppData/Roaming/npm/llm.cmd"

# 调试时加 --verbose
python auto_accept.py --wrapper --mode always_yes --verbose -- "C:/Users/user/AppData/Roaming/npm/llm.cmd"
```

### 可能的问题
- 如果 `pty.write` 发送的 Enter 不被 Ink TUI 接收 → 回退到 SendInput
- 如果提示检测太慢 → 需调整匹配模式或检测逻辑
- ~~llm 在非 TTY 下拒绝交互~~ → ✅ 已改为 ConPTY，子进程看到真 TTY

## 文件清单
```
auto-accept/
├── auto_accept.py          # 主工具（hook + wrapper + install）
├── llm_checker.py     # LLM API 客户端
├── terminal_monitor.py     # 截图监控（备选方案）
├── config.yaml             # 配置文件
├── requirements.txt        # pip 依赖
├── STATUS.md               # 旧状态文档
├── PLAN_monitor.md         # 终端监控方案
├── README.md               # 使用说明
└── auto_accept.log         # 运行日志
```

## llm 路径
`C:\Users\user\AppData\Roaming\npm\llm.cmd`
