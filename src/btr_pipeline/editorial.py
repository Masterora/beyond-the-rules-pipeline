from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import requests

from .models import Story
from .openrouter import OpenRouterClient

CHANNEL_BRIEF = """
你为中文 YouTube 频道《规则之外》工作。频道讲真实世界里的规则、漏洞、交易与代价。
受众是 22–45 岁、厌倦空泛鸡汤但喜欢真实制度故事的人。内容必须是常青题材，不追逐
当天热搜；以一个反常识的真实事件打开，然后回答：规则为什么出现、谁利用了它、谁付出
代价、规则后来如何改变，以及观众可以带走什么判断框架。

绝对禁止：虚构人物或对白、未经证实的指控、阴谋论、猎奇犯罪细节、洗稿、夸大数字、
依赖电影/电视/新闻台画面的选题、纯 AI 概念插图、开场寒暄、标题党却不兑现。
""".strip()


DISCOVERY_PROMPT = """
完成一次严谨的选题和资料搜集，输出一个 JSON 对象。先联网寻找至少 3 个不同发布机构的
可靠来源，其中至少 2 个必须是一手或权威材料（政府、法院、监管机构、当事组织档案、
学术论文或原始数据库）。优先选择拥有大量公共领域或 CC 授权历史影像的故事。

叙事总字数应为 2600–3600 个汉字，拆成 12–16 个场景。每个场景都必须列出实际支持该段
陈述的 source URL，visual_query 用具体英文描述可检索的真实地点、人物、物件或档案，
不要用抽象词。前 30 秒要制造信息差、明确赌注并承诺稍后兑现，但不能泄露全部答案。
每 45–75 秒引入新的证据、逆转或未解问题；结尾回扣开场并提供有用判断框架。

JSON 字段必须严格为：
{
  "title": "12–32字，准确而有张力",
  "thumbnail_text": "2–8字，不重复标题",
  "hook": "前30秒原文",
  "description": "两段简介",
  "tags": ["..."],
  "sources": [{"title":"","url":"https://...","publisher":"","published_at":"","authority":"primary|secondary"}],
  "scenes": [{"heading":"","narration":"","visual_query":"","cited_source_urls":["https://..."],"on_screen_fact":""}],
  "factual_risk_notes": ["需要谨慎表达的边界"],
  "synthetic_media_disclosure": "旁白使用合成语音；画面均来自有明确许可的档案或素材。"
}
""".strip()


FACT_GATE_PROMPT = """
你是事实编辑，不是创作者。审查输入故事中的每个可核验陈述和每个 URL，只保留来源明确
支持的表述。删除无法从给定来源确认的数字、因果、动机和引语；把有争议的解释标成
“一种解释”并列出边界。不得发明新 URL。保持原 JSON 结构，输出完整修订版。至少 3 个
发布机构、至少 2 个一手或权威来源、每个场景至少 1 个已列入 sources 的 URL。若故事无法
安全成立，输出 {"reject_reason":"具体原因"}。
""".strip()


RETENTION_PROMPT = """
你是纪录片成片编辑。不得更改事实或 URL，只改善观看留存：
1. 第一段在 8 秒内给出具体异常与损失/赌注，不寒暄、不解释频道；
2. 每段只承担一个叙事动作，句式长短交替，像资深真人讲述而不是广告文案；
3. 每 45–75 秒设置一个有事实依据的新问题或逆转，避免廉价悬念；
4. 删掉总结式重复、套话和 AI 常见排比；
5. 标题和缩略图文案彼此补全，并在正文中兑现；
6. 保持 2600–3600 个汉字和 12–16 个场景。
输出原结构的完整 JSON，不增加无来源事实。
""".strip()


SOURCE_REPAIR_PROMPT = """
你是资料核验编辑。输入 JSON 中部分 source URL 已失效。请联网找到仍可访问且直接支持原陈述
的替代页面；优先同一机构的新 URL，其次政府、法院、监管、大学、论文或原始档案。更新
sources 中的 URL，并同步替换每个 scenes.cited_source_urls 中的旧 URL。不要借机改写故事、
增加未经证实的事实或返回搜索结果页。保持完整原 JSON 结构。若找不到等强度来源，输出
{"reject_reason":"具体缺口"}。
""".strip()


class EditorialPipeline:
    def __init__(self, client: OpenRouterClient):
        self.client = client

    def build_story(self, run_dir: Path, recent_topics: list[str] | None = None) -> Story:
        print("[editorial 1/4] researching evergreen topic and primary sources", flush=True)
        exclusions = json.dumps(recent_topics or [], ensure_ascii=False)
        draft_data = self.client.chat_json(
            system=CHANNEL_BRIEF,
            user=f"{DISCOVERY_PROMPT}\n\n最近已做选题，不得重复：{exclusions}",
            web_search=True,
            temperature=0.55,
        )
        self._write_json(run_dir / "01-research-draft.json", draft_data)

        print("[editorial 2/4] running independent factual boundary pass", flush=True)
        fact_data = self.client.chat_json(
            system=f"{CHANNEL_BRIEF}\n\n{FACT_GATE_PROMPT}",
            user=json.dumps(draft_data, ensure_ascii=False),
            web_search=True,
            temperature=0.1,
        )
        fact_data = self._unwrap_story_payload(fact_data)
        if fact_data.get("reject_reason"):
            raise RuntimeError(f"fact gate rejected story: {fact_data['reject_reason']}")
        self._write_json(run_dir / "02-fact-checked.json", fact_data)

        fact_story = Story.from_dict(fact_data)
        fact_errors = fact_story.validate()
        if fact_errors:
            raise RuntimeError("fact gate returned incomplete story: " + "; ".join(fact_errors))
        source_errors = self._verify_sources(fact_story)
        for repair_attempt in range(1, 3):
            if not source_errors:
                break
            print(
                f"[editorial 3/4] repairing {len(source_errors)} unavailable source(s), "
                f"attempt {repair_attempt}/2",
                flush=True,
            )
            repaired_data = self.client.chat_json(
                system=f"{CHANNEL_BRIEF}\n\n{SOURCE_REPAIR_PROMPT}",
                user=json.dumps(
                    {"unavailable_sources": source_errors, "story": fact_data},
                    ensure_ascii=False,
                ),
                web_search=True,
                temperature=0.1,
            )
            repaired_data = self._unwrap_story_payload(repaired_data)
            if repaired_data.get("reject_reason"):
                raise RuntimeError(
                    f"source repair rejected story: {repaired_data['reject_reason']}"
                )
            fact_data = repaired_data
            fact_story = Story.from_dict(fact_data)
            repaired_errors = fact_story.validate()
            if repaired_errors:
                raise RuntimeError(
                    "source repair returned incomplete story: "
                    + "; ".join(repaired_errors)
                )
            source_errors = self._verify_sources(fact_story)
            self._write_json(
                run_dir / f"03-source-repair-{repair_attempt}.json", fact_data
            )
        if source_errors:
            raise RuntimeError("source repair failed: " + "; ".join(source_errors))

        story = fact_story
        fact_urls = {source.url for source in fact_story.sources}
        for retention_attempt in range(1, 3):
            print(
                f"[editorial 4/4] editing for human cadence and retention, "
                f"attempt {retention_attempt}/2",
                flush=True,
            )
            final_data = self._unwrap_story_payload(
                self.client.chat_json(
                    system=f"{CHANNEL_BRIEF}\n\n{RETENTION_PROMPT}",
                    user=json.dumps(fact_data, ensure_ascii=False),
                    temperature=0.35 if retention_attempt == 1 else 0.15,
                )
            )
            candidate = Story.from_dict(final_data)
            errors = candidate.validate()
            candidate_urls = {source.url for source in candidate.sources}
            if candidate_urls != fact_urls:
                errors.append("retention edit changed the verified source set")
            if len(candidate.scenes) != len(fact_story.scenes):
                errors.append("retention edit changed the verified scene count")
            if not errors:
                source_errors = self._verify_sources(candidate)
                errors.extend(source_errors)
            if not errors:
                story = candidate
                break
            print(
                "[editorial 4/4] rejected incomplete retention edit: "
                + "; ".join(errors),
                flush=True,
            )
        else:
            print(
                "[editorial 4/4] using complete fact-checked version after "
                "retention edits failed validation",
                flush=True,
            )
        self._write_json(run_dir / "story.json", story.as_dict())
        self._write_sources(run_dir / "sources.md", story)
        return story

    @staticmethod
    def _unwrap_story_payload(data: dict) -> dict:
        """Accept a model's harmless {story: ...} wrapper, never an empty shell."""
        nested = data.get("story")
        if isinstance(nested, dict):
            return nested
        return data

    @staticmethod
    def _verify_sources(story: Story) -> list[str]:
        errors: list[str] = []
        for source in story.sources:
            parsed = urlparse(source.url)
            if parsed.scheme != "https" or not parsed.netloc:
                errors.append(f"invalid source URL: {source.url}")
                continue
            try:
                response = requests.get(
                    source.url,
                    timeout=20,
                    allow_redirects=True,
                    headers={"User-Agent": "BeyondTheRulesResearch/1.0"},
                    stream=True,
                )
                if response.status_code in {404, 410, 451} or response.status_code >= 500:
                    errors.append(f"source returned HTTP {response.status_code}: {source.url}")
                response.close()
            except requests.RequestException as exc:
                errors.append(f"source unavailable: {source.url} ({exc.__class__.__name__})")
        return errors

    @staticmethod
    def _write_json(path: Path, data: object) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _write_sources(path: Path, story: Story) -> None:
        lines = [f"# Sources — {story.title}", ""]
        for source in story.sources:
            lines.append(
                f"- [{source.title}]({source.url}) — {source.publisher}; "
                f"{source.authority}; {source.published_at or 'date not supplied'}"
            )
        lines.extend(["", "## Disclosure", "", story.synthetic_media_disclosure, ""])
        path.write_text("\n".join(lines), encoding="utf-8")
