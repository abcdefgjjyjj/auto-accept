"""
LLM API 客户端 —— 用于判断是否该拒绝某个操作。

策略：默认允许，只有 LLM 明确说 DENY 才拒绝。
超时、网络错误、API 异常等情况均默认放行。
"""

import logging
from typing import Optional

import requests

logger = logging.getLogger("auto_accept.llm")


class LLMChecker:
    """LLM 审查器 —— 宽松策略：默认通过，明确拒绝才拦截。"""

    def __init__(self, config: dict):
        cfg = config.get("llm", {})
        self.api_key: str = cfg.get("api_key", "")
        self.base_url: str = cfg.get("base_url", "https://api.llm.com").rstrip("/")
        self.model: str = cfg.get("model", "llm-chat")
        self.timeout: int = cfg.get("timeout", 15)
        self.max_retries: int = cfg.get("max_retries", 2)
        self.extra_params: dict = cfg.get("extra_params", {})

        review_cfg = config.get("review", {})
        self.system_prompt: str = review_cfg.get(
            "system_prompt",
            (
                "你是一个命令审查器。判断规则：默认允许；"
                "只有涉及删除系统文件、格式化磁盘、强制推送代码、"
                "泄露密钥等危险操作时才回复 DENY: <理由>。"
                "否则回复 ALLOW。"
            ),
        )

    def check(self, prompt_text: str) -> tuple[bool, str]:
        """
        返回 (allowed, reason)。
        allowed=True   → 放行
        allowed=False  → 拒绝
        """
        if not self.api_key or self.api_key.startswith("sk-your-"):
            logger.warning("LLM API key 未配置，回退到 always_yes 模式")
            return True, "api key not configured, fallback to allow"

        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"请审查以下操作：\n\n{prompt_text}"},
            ],
            **self.extra_params,
        }

        for attempt in range(1 + self.max_retries):
            try:
                logger.info(
                    "LLM 审查请求 (attempt %d/%d): %s",
                    attempt + 1, 1 + self.max_retries, prompt_text[:120],
                )
                resp = requests.post(
                    url, json=payload, headers=headers,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()

                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                logger.info("LLM 回复: %s", content)

                return self._parse_response(content)

            except requests.Timeout:
                logger.warning("LLM API 超时 (attempt %d)，默认放行", attempt + 1)
                if attempt >= self.max_retries:
                    return True, "llm timeout, fallback to allow"

            except requests.RequestException as e:
                logger.warning("LLM API 请求失败: %s，默认放行", e)
                return True, f"llm request error: {e}, fallback to allow"

        return True, "max retries exceeded, fallback to allow"

    def _parse_response(self, content: str) -> tuple[bool, str]:
        """解析 LLM 回复，判断是 ALLOW 还是 DENY。"""
        upper = content.upper().strip()

        # 明确 DENY 才拒绝
        if upper.startswith("DENY") or upper.startswith("拒绝"):
            reason = content.split(":", 1)[-1].strip() if ":" in content else content
            logger.warning("LLM 拒绝: %s", reason)
            return False, reason

        # 其它一切情况（ALLOW / 允许 / 无法判断 / 空回复）一律放行
        return True, content
