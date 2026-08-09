---
name: tennis-reel
description: Generate Ten-Fifty5 tennis-tactics Reels (9:16 MP4 + caption) from the animated pattern board using the automated Playwright + ElevenLabs + ffmpeg pipeline. Use when the user wants to produce, regenerate, or batch social video for Ten-Fifty5 tennis patterns.
---

# Tennis Reel generator

Produces ready-to-post 9:16 MP4s (+ caption `.txt`) for Ten-Fifty5 tennis-tactics patterns, fully automated:
board animation (headless Playwright) + ElevenLabs voiceover + ffmpeg mux.

## Location — the 1050 (Ten-Fifty5) repo, NOT NextPoint
`C:\dev\webhook-server\marketing\reel-pipeline\` (this is Ten-Fifty5 marketing content, so it lives in the
1050 repo alongside the SEO-blog workflow). The board it records is
`C:\dev\webhook-server\marketing\pattern-board.html`. Full detail: that dir's `README.md` and
`C:\dev\webhook-server\marketing\ten-fifty5-pattern-board-playbook.md`.
The 1050 repo is otherwise READ-ONLY product code — only touch its `marketing/` area, and never commit
(its hook needs `CLAUDE_CODE=1`). The `.env` (ElevenLabs key) already exists there — never overwrite it.

## To generate reels
1. Confirm setup once: `C:\dev\webhook-server\marketing\reel-pipeline\node_modules/` exists (else `npm install` +
   `npx playwright install chromium` in that dir).
2. The ElevenLabs key lives in `C:\dev\webhook-server\marketing\reel-pipeline\.env` (`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`).
   If it's missing, the pipeline still runs but produces **silent** clips — tell the user and offer to
   proceed silent or wait for the key.
3. Run from `C:\dev\webhook-server\marketing\reel-pipeline\`:
   - all patterns: `node make-reel.js`
   - specific: `node make-reel.js <id> [<id>…]` (ids: serve-t, net-approach, run-around, return-depth,
     serve-wide, two-one, second-serve, three-one)
4. Outputs are in `C:\dev\webhook-server\marketing\reel-pipeline\out/` — `<id>.mp4` (the Reel) + `<id>.txt` (the caption).
   Report which MP4s were produced; the user reviews and posts them (publishing is intentionally manual).

## To change wording / add a pattern
Edit `C:\dev\webhook-server\marketing\reel-pipeline\patterns.json` (id, branch, script, caption). Script numbers are spelled as
words (TTS reads them naturally). A new `id` must match a pattern in the board's `PATTERNS[]` array. To add
a whole new tactical pattern, add it to the board first (see the playbook), then to `patterns.json`.

## Guardrails
- Keep ONE voice id across every Reel (brand consistency).
- Do NOT auto-publish — output the MP4 + caption for the user to review and post.
- This is Ten-Fifty5 marketing content; the 1050 product repo (`C:\dev\webhook-server`) stays read-only.
- `marketing/` is untracked ad-ops material — don't fold it into platform commits.
