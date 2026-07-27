# Beyond The Rules — cloud production pipeline

This repository runs the editorial and publishing workflow for the YouTube
channel **规则之外 / @BeyondThe_Rules**. It produces Chinese documentary-style
long videos about real rules, loopholes, transactions, and their human cost.

The pipeline deliberately fails closed. A video is not uploaded unless:

- every factual claim has a cited source;
- every visual has a machine-readable public-domain or Creative Commons record;
- the narration, thumbnail, subtitles, clean master, and subtitled master pass
  technical checks;
- retention and editorial checks pass;
- the upload can remain private while the YouTube API project is awaiting audit.

No scraped movies, broadcast footage, copyrighted music, or unlicensed social
media clips are used. The default visual providers are Wikimedia Commons and
optional Pexels API results. The soundtrack is generated procedurally.

## Cloud schedule

GitHub Actions wakes at **08:30 Asia/Shanghai** every day. A successful subtitled
build and its clean master are uploaded to YouTube as **private**. Public
scheduling is technically locked until the API compliance audit is approved and
the repository variable `YOUTUBE_PUBLIC_UPLOAD_ENABLED` is explicitly set to
`true`. Public Actions artifacts contain evidence and metadata, never unpublished
video or raw source media.

The lightweight derivatives in `media/gold-window/` are reproducible copies of
the Public Domain, CC0, and CC BY sources recorded in
`stories/gold-window-assets.json`. Original source pages, creators, licenses,
and attribution remain authoritative and are carried into the video description.

## Required repository secrets

| Secret | Purpose |
| --- | --- |
| `OPENROUTER_API_KEY` | research, editorial passes, and speech synthesis |
| `YOUTUBE_CLIENT_ID` | OAuth desktop application |
| `YOUTUBE_CLIENT_SECRET` | OAuth desktop application |
| `YOUTUBE_REFRESH_TOKEN` | upload-only refresh token |

Optional: `PEXELS_API_KEY`. The workflow still runs with Wikimedia Commons when
it is absent.

## Local verification

```bash
python -m pip install -e '.[test]'
pytest
python -m btr_pipeline.main --dry-run --run-dir runs/manual
```

The dry run validates configuration and policy without calling paid APIs or
uploading content. A production run needs `ffmpeg`, `ffprobe`, network access,
and the secrets above.

## Artifacts

Each successful run keeps:

- `video-clean.mp4` — private YouTube master without burned subtitles;
- `video-subtitled.mp4` — private or scheduled publish master;
- `captions.srt`, `thumbnail.jpg`;
- `story.json`, `sources.md`, `rights-manifest.json`;
- `qa-report.json`, `upload-receipt.json`.

Operational and compliance details are in [`audit/`](audit/). Privacy and terms
pages are published from [`docs/`](docs/).
