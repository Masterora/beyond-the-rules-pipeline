# YouTube API compliance architecture

```text
GitHub Actions scheduler
        |
        v
OpenRouter research -> fact gate -> retention edit
        |                         (public web sources only)
        v
Wikimedia Commons -> license gate -> local job workspace
        |                              |
        +------------------------------+
                                       v
                         TTS -> ffmpeg dual render -> QA gate
                                                     |
                                                     v
                                  YouTube videos.insert + thumbnails.set
                                                     |
                                                     v
                                              private video
```

## Google data boundary

- OAuth scope: `youtube.upload` only.
- Subject: the owner's single channel. No third-party users.
- Reads: none from YouTube account data.
- Writes: original MP4 metadata and thumbnail.
- Token storage: GitHub encrypted Actions secrets.
- Log boundary: tokens and API authorization headers are never serialized.
- Revocation: Google Account permissions plus deletion of repository secrets.
- Default state: private. Public scheduling remains feature-locked until audit.

## Evidence produced per run

The job retains the exact editorial JSON, cited sources, per-asset license
manifest, two video masters, SRT, thumbnail, ffprobe results, QA decision, and
upload response. The upload is skipped if any gate fails.
