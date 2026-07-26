from __future__ import annotations

import json
import re
from typing import Any

import requests


class OpenRouterClient:
    def __init__(self, api_key: str, model: str, timeout: int = 180):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = "https://openrouter.ai/api/v1"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://masterora.github.io/beyond-the-rules-pipeline/",
            "X-Title": "Beyond The Rules Pipeline",
        }

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        web_search: bool = False,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "max_tokens": 14_000,
            "provider": {
                "order": ["OpenAI", "Azure"],
                "allow_fallbacks": True,
            },
        }
        if web_search:
            body["tools"] = [
                {
                    "type": "openrouter:web_search",
                    "parameters": {"max_results": 8, "search_context_size": "high"},
                }
            ]
        last_error = "unknown response error"
        for attempt in range(1, 4):
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            finish_reason = "invalid_envelope"
            content = ""
            try:
                payload = self._parse_api_response(response.text)
                choice = payload.get("choices", [{}])[0]
                finish_reason = str(choice.get("finish_reason", "unknown"))
                content = choice.get("message", {}).get("content", "")
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict)
                    )
                if not str(content).strip():
                    raise ValueError(f"empty response, finish_reason={finish_reason}")
                return self._parse_json(str(content))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = str(exc)
                print(
                    f"[openrouter] invalid JSON response on attempt {attempt}/3 "
                    f"({finish_reason}, {response.headers.get('content-type', 'unknown')}, "
                    f"{len(response.content)} bytes); retrying",
                    flush=True,
                )
                messages.extend(
                    [
                        {"role": "assistant", "content": str(content)[:2000]},
                        {
                            "role": "user",
                            "content": (
                                "上一个响应为空或不是完整 JSON。请重新执行任务，只返回一个"
                                "完整、合法的 JSON 对象；不要 Markdown，不要解释。"
                            ),
                        },
                    ]
                )
        raise RuntimeError(f"OpenRouter returned no valid JSON after 3 attempts: {last_error}")

    @staticmethod
    def _parse_api_response(content: str) -> dict[str, Any]:
        """Decode a non-streaming OpenRouter envelope defensively."""
        stripped = content.lstrip()
        if stripped.startswith("data:"):
            raise ValueError("unexpected streaming API response")
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            if start < 0:
                raise
            data, _ = json.JSONDecoder().raw_decode(content[start:])
        if not isinstance(data, dict):
            raise TypeError("OpenRouter API response must be a JSON object")
        return data

    def speech(
        self,
        text: str,
        *,
        model: str,
        voice: str,
        output_path: str,
    ) -> None:
        # Qwen's OpenRouter adapter accepts the common speech fields but rejects
        # OpenAI-only delivery controls such as ``speed`` with HTTP 400.  Pace is
        # shaped by punctuation and by the post-processing chain instead.
        body: dict[str, Any] = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
        }
        response = requests.post(
            f"{self.base_url}/audio/speech",
            headers=self.headers,
            json=body,
            timeout=self.timeout,
        )
        if not response.ok:
            # The response contains no credentials or source text, but does
            # contain the provider's actionable validation message.
            detail = response.text[:1000].replace("\n", " ")
            raise RuntimeError(
                f"OpenRouter speech request failed ({response.status_code}): {detail}"
            )
        content_type = response.headers.get("content-type", "").lower()
        if "audio" not in content_type:
            raise RuntimeError(
                "OpenRouter speech response was not audio: "
                f"{content_type or 'missing content-type'}"
            )
        with open(output_path, "wb") as handle:
            handle.write(response.content)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            if start < 0:
                raise
            data, _ = json.JSONDecoder().raw_decode(content[start:])
        if not isinstance(data, dict):
            raise TypeError("model response must be a JSON object")
        return data
