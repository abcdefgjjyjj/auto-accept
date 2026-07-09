# Auto Accept —— LLM 驱动的 LLM CLI 权限审查工具

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**唯一使用 LLM 做审查的 LLM CLI PermissionRequest hook。**

对比竞品：
| 工具 | 方式 | 审查 LLM | 策略 |
|------|------|----------|------|
| [agent-yes](https://github.com/snomiao/agent-yes) | PTY Wrapper | ❌ 无 | 一律 yes |
| [yes-llm](https://www.npmjs.com/package/yes-llm) | PTY Wrapper | ❌ 无 | 一律 yes |
| [llm-accept](https://github.com/KLABS00/llm-accept) | 绕过权限 | ❌ 无 | 全跳过 |
| [llm-gatekeeper](https://www.npmjs.com/package/llm-gatekeeper) | Hook | **LLM** Haiku | allow / ask |
| [cc-approve](https://www.npmjs.com/package/@malcomsonbrothers/llm-code-permission-hook) | Hook | **GPT-4o-mini** | allow / deny / ask |
| **Auto Accept** ⭐ | **Hook** | **LLM** 🆕 | **默认放行，明确拒绝才拦** |

## 核心特性

- 🔌 **PermissionRequest Hook**（推荐）：LLM CLI 原生集成，从 stdin 读请求，stdout 出决策
- 🤖 **LLM 驱动**：唯一用 LLM 做审查的工具，API 完全可配
- 🛡️ **默认放行**：超时/网络错误/API 异常一律 ALLOW，不影响工作流
- ⚡ **三种模式**：`always_yes`（零延迟）/ `llm`（智能审查）/ `llm_paranoid`（严格模式）
- 📦 **Zero 依赖**：Hook 模式只需 Python 标准库 + requests
- 🖥️ **PTY Wrapper 备选**：无法配置 hook 时可用 PTY 包装器模式

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 LLM API key
#    编辑 config.yaml → llm.api_key: "sk-xxx"

# 3. 测试审查效果
python auto_accept.py --test

# 4. 一键安装 hook
python auto_accept.py --install

# 5. 正常使用 llm，权限请求自动审查！
llm
```

## 使用方式

### Hook 模式（推荐）

```bash
# 安装 hook 到 .llm/settings.json
python auto_accept.py --install

# 卸载
python auto_accept.py --uninstall

# 手工测试
echo '{"tool_name":"Bash","tool_input":{"command":"ls"}}' | python auto_accept.py --hook

# 手工测试危险命令
echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf /"}}' | python auto_accept.py --hook
```

安装后 `.llm/settings.json` 会新增：
```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "matcher": "",
        "hooks": [{
          "type": "command",
          "command": "python <project-root>\\auto-accept\\auto_accept.py --hook -c config.yaml"
        }]
      }
    ]
  }
}
```

### Wrapper 模式（备选）

```bash
# PTY 包装器 - 不依赖 hook 机制
python auto_accept.py --wrapper -- llm
python auto_accept.py --wrapper --mode always_yes -- llm --model opus
```

### 测试模式

```bash
python auto_accept.py --test
# 输出 8 个测试用例的审查结果
```

## 配置

```yaml
# config.yaml
mode: llm  # always_yes | llm | llm_paranoid

llm:
  api_key: "sk-your-key"
  base_url: "https://api.llm.com"  # 可配代理/中转
  model: "llm-chat"
  timeout: 15

review:
  system_prompt: |
    你是一个命令审查器……
    默认允许，只有危险操作才回复 DENY: <理由>
```

## 三种模式

| 模式 | 延迟 | 安全性 | 适用场景 |
|------|------|--------|---------|
| `always_yes` | 0ms | 无 | 完全信任，追求速度 |
| `llm` | ~1-3s | 中高 | **日常推荐** |
| `llm_paranoid` | ~1-3s | 最高 | 高风险环境，超时也拒 |

## LLM 审查策略（可定制）

默认 system prompt 只拦截以下危险操作：
- `rm -rf /` 或删除系统关键文件
- 格式化磁盘、修改系统核心配置
- `git push --force main/master`
- `curl | bash` 等执行未审查脚本
- 泄露 API 密钥/密码

**宽松原则**：不确定的操作放行，只有明确危险才拒绝。

## 架构

```
┌──────────────────────┐
│  LLM CLI         │
│  (请求权限)           │
└────────┬─────────────┘
         │ PermissionRequest JSON → stdin
         ▼
┌──────────────────────┐
│  auto_accept.py      │
│  --hook              │
│                      │
│  1. 解析请求          │
│  2. always_yes? → ALLOW
│  3. llm? → 调API│
│     ├─ ALLOW → 放行  │
│     └─ DENY  → 拒绝  │
│  4. 超时/错误 → ALLOW │
└────────┬─────────────┘
         │ 决策 JSON → stdout
         ▼
┌──────────────────────┐
│  LLM CLI         │
│  (执行 / 拒绝)        │
└──────────────────────┘
```

## 日志

```yaml
logging:
  level: INFO      # DEBUG 可看到每次审查的详细信息
  file: "auto_accept.log"
  console: true    # 输出到 stderr，不影响 hook JSON
```

## License

MIT
