from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .models import Story, VisualAsset
from .openrouter import OpenRouterClient


@dataclass
class Segment:
    index: int
    audio_path: Path
    video_path: Path
    duration: float


def run_command(command: list[str], *, cwd: Path | None = None) -> None:
    completed = subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout: {completed.stdout[-2000:]}\nstderr: {completed.stderr[-4000:]}"
        )


def ffprobe_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"ffprobe failed for {path}: {completed.stderr}")
    return float(completed.stdout.strip())


class NarrationProducer:
    def __init__(self, client: OpenRouterClient, *, model: str, voice: str):
        self.client = client
        self.model = model
        self.voice = voice

    def synthesize(self, story: Story, run_dir: Path) -> list[Path]:
        speech_dir = run_dir / "speech"
        speech_dir.mkdir(exist_ok=True)
        outputs: list[Path] = []
        for index, scene in enumerate(story.scenes, start=1):
            print(
                f"[narration {index}/{len(story.scenes)}] synthesizing scene",
                flush=True,
            )
            raw = speech_dir / f"{index:02d}-raw.mp3"
            polished = speech_dir / f"{index:02d}.wav"
            # Scene-sized calls preserve natural breath and prevent one uniform cadence.
            self.client.speech(
                self._punctuate(scene.narration),
                model=self.model,
                voice=self.voice,
                output_path=str(raw),
            )
            run_command(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(raw),
                    "-af",
                    (
                        "highpass=f=65,lowpass=f=13500,"
                        "equalizer=f=180:t=q:w=1.1:g=1.5,"
                        "equalizer=f=3200:t=q:w=1.2:g=1.0,"
                        "acompressor=threshold=-18dB:ratio=2.2:attack=20:release=180,"
                        "loudnorm=I=-16:LRA=9:TP=-1.5"
                    ),
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(polished),
                ]
            )
            raw.unlink(missing_ok=True)
            outputs.append(polished)
        return outputs

    @staticmethod
    def _punctuate(text: str) -> str:
        text = re.sub(r"([。！？])", r"\1\n", text)
        text = re.sub(r"([；：])", r"\1 ", text)
        return re.sub(r"\n{2,}", "\n", text).strip()


class VideoRenderer:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.segment_dir = run_dir / "segments"
        self.segment_dir.mkdir(exist_ok=True)

    def render(
        self,
        story: Story,
        assets: list[VisualAsset],
        audio_paths: list[Path],
    ) -> dict[str, Path]:
        if len(story.scenes) != len(assets) or len(assets) != len(audio_paths):
            raise RuntimeError("scene, asset, and narration counts do not match")
        segments: list[Segment] = []
        for index, (asset, audio) in enumerate(zip(assets, audio_paths, strict=True)):
            print(
                f"[render {index + 1}/{len(assets)}] encoding documentary scene",
                flush=True,
            )
            duration = ffprobe_duration(audio) + 0.5
            video = self.segment_dir / f"{index + 1:02d}.mp4"
            self._render_segment(asset, audio, video, duration, index)
            segments.append(Segment(index, audio, video, duration))

        clean = self.run_dir / "video-clean.mp4"
        print("[render] joining clean 1080p master", flush=True)
        self._concatenate(segments, clean)
        captions = self.run_dir / "captions.srt"
        self._write_srt(story, segments, captions)
        subtitled = self.run_dir / "video-subtitled.mp4"
        print("[render] burning mobile-safe subtitles into publish master", flush=True)
        self._burn_subtitles(clean, captions, subtitled)
        thumbnail = self.run_dir / "thumbnail.jpg"
        self._make_thumbnail(story, assets[0], thumbnail)
        return {
            "clean": clean,
            "subtitled": subtitled,
            "captions": captions,
            "thumbnail": thumbnail,
        }

    def _render_segment(
        self,
        asset: VisualAsset,
        audio: Path,
        target: Path,
        duration: float,
        index: int,
    ) -> None:
        common_video = (
            "scale=2160:1215:force_original_aspect_ratio=increase,"
            "crop=2160:1215,scale=1920:1080,"
            "eq=contrast=1.05:saturation=0.90:brightness=-0.015,"
            "vignette=PI/5,fps=30,format=yuv420p"
        )
        if asset.media_type == "video":
            inputs = ["-stream_loop", "-1", "-i", str(asset.local_path)]
            video_filter = common_video
        else:
            inputs = ["-loop", "1", "-i", str(asset.local_path)]
            pan = "sin(on/65)*10" if index % 2 else "cos(on/70)*10"
            video_filter = (
                "scale=2160:1215:force_original_aspect_ratio=increase,"
                "crop=2160:1215,"
                f"zoompan=z='min(zoom+0.00035,1.09)':x='iw/2-(iw/zoom/2)+{pan}':"
                "y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30,"
                "eq=contrast=1.05:saturation=0.90:brightness=-0.015,"
                "vignette=PI/5,format=yuv420p"
            )
        run_command(
            [
                "ffmpeg",
                "-y",
                *inputs,
                "-i",
                str(audio),
                "-f",
                "lavfi",
                "-i",
                "anoisesrc=color=pink:amplitude=0.0015:sample_rate=48000",
                "-filter_complex",
                (
                    f"[0:v]{video_filter}[v];"
                    "[1:a]volume=1.0[voice];[2:a]lowpass=f=420,highpass=f=45,"
                    "volume=0.25[room];[voice][room]amix=inputs=2:duration=first:"
                    "dropout_transition=0,loudnorm=I=-16:LRA=9:TP=-1.5[a]"
                ),
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-t",
                f"{duration:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(target),
            ]
        )

    def _concatenate(self, segments: list[Segment], target: Path) -> None:
        list_path = self.segment_dir / "concat.txt"
        list_path.write_text(
            "\n".join(f"file '{segment.video_path.resolve()}'" for segment in segments),
            encoding="utf-8",
        )
        run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(target),
            ]
        )

    @staticmethod
    def _write_srt(story: Story, segments: list[Segment], target: Path) -> None:
        entries: list[str] = []
        cursor = 0.0
        counter = 1
        for scene, segment in zip(story.scenes, segments, strict=True):
            chunks = _subtitle_chunks(scene.narration)
            weights = [max(1, len(re.sub(r"\s", "", chunk))) for chunk in chunks]
            usable = max(segment.duration - 0.25, 0.5)
            total_weight = sum(weights)
            for chunk, weight in zip(chunks, weights, strict=True):
                start = cursor
                duration = max(1.2, usable * weight / total_weight)
                end = min(cursor + duration, cursor + usable)
                entries.extend(
                    [
                        str(counter),
                        f"{_srt_time(start)} --> {_srt_time(end)}",
                        chunk,
                        "",
                    ]
                )
                counter += 1
                cursor = end
            cursor = sum(item.duration for item in segments[: segment.index + 1])
        target.write_text("\n".join(entries), encoding="utf-8")

    def _burn_subtitles(self, clean: Path, captions: Path, target: Path) -> None:
        escaped = captions.name.replace("'", "\\'")
        style = (
            "FontName=Noto Sans CJK SC,FontSize=18,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H66000000,BorderStyle=3,Outline=1,Shadow=0,"
            "BackColour=&H66000000,MarginV=55,Alignment=2"
        )
        run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                clean.name,
                "-vf",
                f"subtitles='{escaped}':force_style='{style}'",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                target.name,
            ],
            cwd=self.run_dir,
        )

    def _make_thumbnail(self, story: Story, asset: VisualAsset, target: Path) -> None:
        base_path = asset.local_path
        extracted = self.run_dir / "thumbnail-base.jpg"
        if asset.media_type == "video":
            run_command(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    "1",
                    "-i",
                    str(base_path),
                    "-frames:v",
                    "1",
                    str(extracted),
                ]
            )
            base_path = extracted
        with Image.open(base_path) as source:
            image = source.convert("RGB")
            image = _cover(image, 1280, 720).filter(ImageFilter.GaussianBlur(0.3))
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for x in range(780):
            alpha = int(230 * (1 - x / 780) ** 1.8)
            draw.line((x, 0, x, 720), fill=(5, 9, 14, alpha))
        draw.rectangle((62, 82, 82, 180), fill=(232, 55, 43, 255))
        font = _find_font(96)
        text = _wrap_thumbnail_text(story.thumbnail_text)
        draw.multiline_text(
            (112, 210),
            text,
            font=font,
            fill=(255, 248, 235, 255),
            spacing=14,
            stroke_width=2,
            stroke_fill=(0, 0, 0, 180),
        )
        result = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        result.save(target, "JPEG", quality=94, optimize=True)
        extracted.unlink(missing_ok=True)


def _subtitle_chunks(text: str, maximum: int = 24) -> list[str]:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？；])", text) if item.strip()]
    chunks: list[str] = []
    for sentence in sentences:
        while len(sentence) > maximum:
            cut = max(sentence.rfind(mark, 0, maximum + 1) for mark in "，、：")
            if cut < maximum // 2:
                cut = maximum
            else:
                cut += 1
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            chunks.append(sentence)
    return chunks or [text]


def _srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (math.ceil(image.width * scale), math.ceil(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _find_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size, index=2)
    raise RuntimeError("No CJK font found; install fonts-noto-cjk")


def _wrap_thumbnail_text(text: str) -> str:
    if len(text) <= 5:
        return text
    midpoint = math.ceil(len(text) / 2)
    return f"{text[:midpoint]}\n{text[midpoint:]}"
