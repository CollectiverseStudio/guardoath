# GuardOath website launch video — approved production notes

Updated: 2026-10-30. Audience: website developer and the Director coding agent.

## Approved creative and production asset

The approved video is **`guardoath_ai_final.mp4`**, a 136-second (2:16) GuardOath AI-agent introduction. It uses the GuardOath G/shield brand treatment, an eight-second music-only introduction, first-person AI-agent narration generated with ElevenLabs, and an original rhythmic electronic score. The intended delivery format is **1280×720, H.264 video, AAC stereo audio, MP4 with fast-start metadata**. The rendered local artifact was verified to contain video and stereo audio streams; it has not been independently reviewed on all browsers.

The film is a **development preview**, not a claim of production deployment, independently verified security effectiveness, or certification. The voice speaks as a fictional AI narrator, not as a real autonomous agent endorsing the product. Preserve the final “Development preview” disclosure.

### Asset transfer status — important

The MP4 was rendered in a ChatGPT working session, **not uploaded to this GitHub repository or a production web host**. Do not assume `/videos/guardoath_ai_final.mp4` exists until the website owner transfers it. The owner has the download link in the originating conversation. The audio-only narration was separately generated with ElevenLabs. Do not substitute the older `guardoath_ai_242.mp4` or the earlier synthesized-voice variants.

The final rendered video was approximately **5.2 MB**, with a measured 136-second duration. The web team should independently verify checksum, duration, audio loudness, caption synchronization, playback and file accessibility after upload.

## Recommended website placement

Add a **Watch GuardOath in 2 Minutes** section near the product introduction or hero area, followed by a short caption:

> Meet the AI agent who believes powerful intelligence needs clear boundaries. Explore GuardOath's approach to AI input and output validation, configurable policies, and an evolving security roadmap. **Development preview — some capabilities shown are planned or in progress.**

Avoid “fully secure,” “prevents all prompt injection,” “HIPAA certified,” “production-ready,” or any unsupported claim.

Use a user-initiated, accessible video player; do not autoplay narrated audio. Example after the asset is actually hosted:

```html
<section aria-labelledby="guardoath-film-heading">
  <h2 id="guardoath-film-heading">Meet GuardOath</h2>
  <p>An AI agent's perspective on building safer, more accountable AI systems.</p>
  <video controls playsinline preload="metadata"
         poster="/images/guardoath-video-poster.jpg"
         width="100%"
         aria-label="GuardOath AI security development preview">
    <source src="/videos/guardoath_ai_final.mp4" type="video/mp4">
    <track kind="captions" src="/videos/guardoath_ai_final.en.vtt"
           srclang="en" label="English" default>
    Your browser does not support HTML5 video.
  </video>
  <p><small>Development preview. Features under active development.</small></p>
</section>
```

The poster and caption track in this example are **proposed assets, not created files**. Create and review a synchronized English WebVTT caption track before publishing; a transcript alone is not a caption file. Use a meaningful poster, responsive layout, controls, keyboard access, and mobile testing.

## Deployment acceptance checklist

- [ ] Transfer `guardoath_ai_final.mp4` to the website's actual public media directory or approved CDN
- [ ] Verify the exact public HTTPS video URL returns MP4 bytes (not an HTML redirect)
- [ ] Verify desktop Firefox, Chrome, Edge, Safari, iOS and Android playback with audible narration and music
- [ ] Review voice/music balance and ensure the opening is music-only for approximately eight seconds
- [ ] Create and verify accurate synchronized captions and accessible fallback text
- [ ] Create a poster frame and confirm responsive layout and no unexpected autoplay
- [ ] Check no unsupported product claims appear in surrounding marketing copy
- [ ] Document the deployed URL, file version, owner and release commit

## Source and ownership

This repository currently contains a FastAPI dashboard under `guardoath/static/`; it does **not** contain an identified separate public marketing website or a `website/` folder. The website owner must identify the actual website repository or hosting system before modifying the live page. Do not confuse updating this documentation with deploying the video.

See [README](../README.md), [user guide](user-guide.md), [implementation progress](input-validation-implementation-progress.md), and [Director coding-agent handoff](director-coding-agent-handoff.md) for the product's real implementation status.
