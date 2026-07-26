# YouTube API audit submission — prepared answers

The account owner must provide legal identity/address information, review the
answers, make the required attestations, and submit the official form. This file
does not attempt to make those declarations on the owner's behalf.

## Application

- Name: Beyond The Rules Uploader
- Project number: `628897441901`
- Primary website: `https://masterora.github.io/beyond-the-rules-pipeline/`
- Privacy policy: `https://masterora.github.io/beyond-the-rules-pipeline/privacy.html`
- Source: `https://github.com/Masterora/beyond-the-rules-pipeline`
- YouTube channel: `https://www.youtube.com/@BeyondThe_Rules`
- Users: one; the channel owner only
- Monetization: not sold as software; supports production for the owner's channel

## Purpose and data flow

The application is a private, scheduled production workflow for original Chinese
documentary videos. It uses `youtube.upload` to upload the final MP4 and thumbnail
to the owner's channel. It does not provide sign-in to third parties and does not
read profile, analytics, comments, subscriptions, or other account data.

The refresh token is held in GitHub encrypted Actions secrets. It is exchanged
for a short-lived access token only inside the job. Tokens and authorization
headers are not logged or included in artifacts. Upload receipts store the
YouTube video identifier and processing result only.

## Requested audit outcome

Lift the private-only restriction applied to uploads from unaudited API projects,
so validated videos can be scheduled for public release by `publishAt`. The
application will continue to default to private, and public scheduling remains
behind an explicit repository variable.

## Screenshots still required

1. GitHub Actions workflow showing the fact, license, render, QA, and upload steps.
2. A successful Actions run with secret values masked.
3. The private video in YouTube Studio.
4. The public privacy-policy page.
5. OAuth consent screen showing only `youtube.upload`.
