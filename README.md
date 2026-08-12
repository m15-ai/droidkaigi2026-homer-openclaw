# Homer-on-OpenClaw — baseball voice agent (:7865)

The same Homer persona as
[droidkaigi2026-homer-hermes](https://github.com/m15-ai/droidkaigi2026-homer-hermes)
(Nous hermes-agent, :7864), rebuilt on **OpenClaw** — a side-by-side comparison of
two agent frameworks behind the identical Pipecat WebRTC voice stack. Point
the Android Pipecat client at `http://<server-ip>:7865` (`/api/offer`) to talk
to this one.

Part of the [DroidKaigi 2026 demo hub](https://github.com/m15-ai/droidkaigi2026)
(start there for the talk materials and the other repos).

## Architecture

```
📱 Android Pipecat client
   │  WebRTC audio
   ▼
server.py  :7865      [ Deepgram STT → OpenClawLLMService → Cartesia TTS ]
                                          │
                          botworker.py  (vendored ACP mgr, JSON over stdio)
                                          │
                          openclaw --profile homer acp
                                          │        (gateway :19011)
                          workspace/  IDENTITY.md AGENTS.md TOOLS.md USER.md
                                          │  exec tool
                          ../baseball-agent/skills/mlb/mlb.py → statsapi.mlb.com
```

## The pieces

| Piece | Where | Notes |
|---|---|---|
| OpenClaw state | `~/.openclaw-homer/` | isolated `--profile homer` — leaves any default `~/.openclaw` install untouched |
| Brain model | `openai/gpt-4.1` | `agents.defaults.model.primary` in `~/.openclaw-homer/openclaw.json`; key in `agents/main/agent/auth-profiles.json` |
| Gateway | `openclaw-homer-gateway.service` (:19011, loopback) | `openclaw acp` is a gateway client — ACP dies with ECONNREFUSED without it |
| Voice server | `homer-openclaw.service` (:7865) | hermes-agent venv (same as the other Pipecat surfaces) |
| Persona/tools | `workspace/*.md` | OpenClaw reads these natively — no identity boot needed (pre-warm ~2.5s) |
| Voice | Cartesia "Austin" `1fcd23d0-…`, sonic-3 | same voice as hermes Homer |
| Tools denied | `web_fetch, web_search, browser, image_generate` | keeps turns fast and on-topic; mlb.py via exec is the only data path |

## Key files (demo walk-through order)

| # | File | What it is / what to say |
|---|------|--------------------------|
| 1 | `server.py` | The Pipecat WebRTC server on :7865. Same audio rails as the hermes Homer — Deepgram STT → `OpenClawLLMService` → Cartesia TTS, Silero VAD for turn-taking/barge-in, a typing sound while the brain "thinks", the RTVI compat shim for the Android SDK. Pre-warms one shared ACP brain session at startup (~2.5s) so connects greet instantly. |
| 2 | `openclaw_llm.py` | The **bridge**. A Pipecat `LLMService` whose "model" is the OpenClaw agent: forwards each user turn (with a tiny `[voice]` tag) to the ACP subprocess, streams the reply, and chunks it into sentences so TTS starts speaking early. No Pipecat tools — OpenClaw owns its tools inside the brain. |
| 3 | `botworker.py` | Process manager for the brain. Spawns one persistent `openclaw --profile homer acp` subprocess and talks JSON-RPC over stdio — many turns, one session. Transport-agnostic: nothing in it knows about WebRTC or audio. |
| 4 | `config.py` | Brain wiring: which OpenClaw binary/profile/agent, the workspace cwd, timeouts. Everything overridable via env. |
| 5 | `voice_directive.py` | The voice-mode rules and the per-turn `[voice]` tag. |
| 6 | `workspace/` | Homer's **persona and tool docs** — `IDENTITY.md` (who he is), `AGENTS.md` (voice rules + ground-truth discipline), `TOOLS.md` (the MLB CLI cheat-sheet), `USER.md`. OpenClaw ingests these natively at session start; there is no prompt-injection machinery in this repo at all. |
| 7 | `examples/` | Sanitized templates for the host-specific bits: `openclaw.json` (profile config), `auth-profiles.json` (LLM key), and both systemd units. |
| 8 | `assets/` | The typing sound played during the thinking gap. |

The MLB data tool itself (`mlb.py`) lives in the
[homer-hermes repo](https://github.com/m15-ai/droidkaigi2026-homer-hermes/blob/main/skills/mlb/mlb.py)
— both Homers literally run the same file, which is the point of the
comparison.

## Setting it up fresh

### API keys (three of them)

| Key | What for | Where it goes | Get one at |
|---|---|---|---|
| OpenAI | the brain model (`openai/gpt-4.1`) | `~/.openclaw-homer/agents/main/agent/auth-profiles.json` (template: [`examples/auth-profiles.json`](examples/auth-profiles.json)) and the gateway unit's `Environment=OPENAI_API_KEY=` line | platform.openai.com |
| Deepgram | speech-to-text | `.env` (copy [`.env.example`](.env.example)) | console.deepgram.com |
| Cartesia | text-to-speech | `.env` (same file) | play.cartesia.ai |

```bash
cp .env.example .env    # then edit: Deepgram + Cartesia keys
chmod 600 .env
```

The MLB data needs **no key** — `statsapi.mlb.com` is free.

### Everything else

Sanitized templates for the host-specific bits are in [`examples/`](examples):
`openclaw.json` + `auth-profiles.json` go under `~/.openclaw-homer/` (the
latter at `agents/main/agent/`), and the two systemd units go in
`~/.config/systemd/user/`. Install OpenClaw itself with `npm i -g openclaw`
(Node 22+), fix the absolute `/home/mjw/...` paths to match your box, and
start the gateway before the voice server.

## Ops

```bash
systemctl --user status openclaw-homer-gateway.service homer-openclaw.service
curl -s http://localhost:7865/health        # {"status":"ok"}
journalctl --user -u homer-openclaw.service -f
# brain alone (bypasses voice): see the ACP test snippets in git history
```

Differences vs the hermes Homer worth demoing: OpenClaw natively ingests the
workspace docs (no SOUL.md injection machinery), tool use is agentic inside
the brain (no iteration-budget tuning), and the same memory-write risks don't
apply — the workspace docs are read-only unless you grant write tools.
