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
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if web_search:
            body["tools"] = [
                {
                    "type": "openrouter:web_search",
                    "parameters": {"max_results": 8, "search_context_size": "high"},
                }
            ]
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self.headers,
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        return self._parse_json(str(content))

    def speech(
        self,
        text: str,
        *,
        model: str,
        voice: str,
        output_path: str,
    ) -> None:
        response = requests.post(
            f"{self.base_url}/audio/speech",
            headers=self.headers,
            json={
                "model": model,
                "input": text,
                "voice": voice,
                "response_format": "mp3",
                "speed": 0.96,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        with open(output_path, "wb") as handle:
            handle.write(response.content)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        data = json.loads(content)
        if not isinstance(data, dict):
            raise TypeError("model response must be a JSON object")
        return data
