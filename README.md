# Homer-on-OpenClaw — baseball voice agent (:7865)

**Homer**, the baseball voice agent, on an [OpenClaw](https://openclaw.ai)
brain: a bare-bones OpenClaw agent behind a
[Pipecat](https://github.com/pipecat-ai/pipecat) WebRTC voice stack, answering
MLB questions from live data. Homer's favorite team is the Los Angeles Dodgers
and his favorite player is Shohei Ohtani. Point the Android Pipecat client at
`http://<server-ip>:7865` (`/api/offer`) and talk baseball.

> Part of the [DroidKaigi 2026 demo suite](https://github.com/m15-ai/droidkaigi2026) — see the
> top-level repo for the session overview and the sibling demo apps.

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
                          skills/mlb/mlb.py → statsapi.mlb.com (free, no key)
```

## The pieces

| Piece | Where | Notes |
|---|---|---|
| OpenClaw state | `~/.openclaw-homer/` | isolated `--profile homer` — leaves any default `~/.openclaw` install untouched |
| Brain model | `openai/gpt-4.1` | `agents.defaults.model.primary` in `~/.openclaw-homer/openclaw.json`; key in `agents/main/agent/auth-profiles.json` |
| Gateway | `openclaw-homer-gateway.service` (:19011, loopback) | `openclaw acp` is a gateway client — ACP dies with ECONNREFUSED without it |
| Voice server | `homer-openclaw.service` (:7865) | Homer agent venv |
| Persona/tools | `workspace/*.md` | OpenClaw reads these natively — no identity boot needed (pre-warm ~2.5s) |
| Voice | Cartesia "Austin" `1fcd23d0-…`, sonic-3 | |
| Tools denied | `web_fetch, web_search, browser, image_generate` | keeps turns fast and on-topic; mlb.py via exec is the only data path |

## Key files

| # | File | What it is |
|---|------|------------|
| 1 | `server.py` | The Pipecat WebRTC server on :7865. Deepgram STT → `OpenClawLLMService` → Cartesia TTS, Silero VAD for turn-taking/barge-in, a typing sound while the brain "thinks", the RTVI compat shim for the Android SDK. Pre-warms one shared ACP brain session at startup (~2.5s) so connects greet instantly. |
| 2 | `openclaw_llm.py` | The **bridge**. A Pipecat `LLMService` whose "model" is the OpenClaw agent: forwards each user turn (with a tiny `[voice]` tag) to the ACP subprocess, streams the reply, and chunks it into sentences so TTS starts speaking early. No Pipecat tools — OpenClaw owns its tools inside the brain. |
| 3 | `botworker.py` | Process manager for the brain. Spawns one persistent `openclaw --profile homer acp` subprocess and talks JSON-RPC over stdio — many turns, one session. Transport-agnostic: nothing in it knows about WebRTC or audio. |
| 4 | `config.py` | Brain wiring: which OpenClaw binary/profile/agent, the workspace cwd, timeouts. Everything overridable via env. |
| 5 | `voice_directive.py` | The voice-mode rules and the per-turn `[voice]` tag. |
| 6 | `workspace/` | Homer's **persona and tool docs** — `IDENTITY.md` (who he is), `AGENTS.md` (voice rules + ground-truth discipline), `TOOLS.md` (the MLB CLI cheat-sheet), `USER.md`. OpenClaw ingests these natively at session start; there is no prompt-injection machinery in this repo at all. |
| 7 | `skills/mlb/mlb.py` | The **data tool**: a ~250-line, stdlib-only CLI over the free MLB Stats API (`statsapi.mlb.com`, no API key). Commands: `scores`, `schedule <team>` (with probable starters), `dodgers`, `ohtani`, `standings`, `player <name>`, `game <team>` (recap: score, decisions, homers, top performers). Output is compact text meant to be read by the LLM and spoken. |
| 8 | `examples/` | Sanitized templates for the host-specific bits: `openclaw.json` (profile config), `auth-profiles.json` (LLM key), and both systemd units. |
| 9 | `assets/` | The typing sound played during the thinking gap. |

## The typing sound

An agent brain has a much longer time-to-first-token than a bare LLM: a turn
that runs `mlb.py` inside the brain can take several seconds before the first
word comes back, and that much dead air on a phone call reads as "it's broken."
So a quiet keyboard-typing loop plays under the gap — the caller hears Homer
"working on it" instead of silence.

How it's wired: [`assets/keyboard-typing-24k.ogg`](assets) is attached to the
transport's output as a Pipecat `SoundfileMixer` (`audio_out_mixer`, looped,
volume 0.45, starts muted). OpenClaw's tool use happens *inside* the ACP
brain — the pipeline never sees Pipecat function-call frames it could hook —
so a small `ThinkingSound` frame processor in `server.py` fills the gap
directly: it unmutes the mixer when the LLM turn starts
(`LLMFullResponseStartFrame`) and mutes it again on the first streamed text
chunk (`LLMTextFrame`), right before TTS starts speaking. Fast turns barely
tick; a long tool-running turn types until the answer lands.

## Setting it up fresh

Tested on a Raspberry Pi 5 (Debian 12, aarch64) with Python 3.11 and Node 22;
any Linux box should do.

### What you need

| Thing | Why | Where |
|---|---|---|
| Node.js **22.12+** | runs OpenClaw (the `openclaw` CLI refuses older Nodes) | `nvm install 22` |
| Python 3.11 | the Pipecat voice server's venv | your package manager |
| OpenAI API key | the brain model (`openai/gpt-4.1`) | platform.openai.com |
| Deepgram API key | speech-to-text | console.deepgram.com |
| Cartesia API key | text-to-speech | play.cartesia.ai |
| The Android Pipecat client | the phone side | the DroidKaigi 2026 demo suite (point it at this box) |

The MLB data itself needs **no key** — `statsapi.mlb.com` is free.

### 1. Get this repo

```bash
git clone <this-repo> ~/projects/baseball-openclaw
cd ~/projects/baseball-openclaw
```

The persona docs in `workspace/TOOLS.md` reference the clone by absolute path
(the brain runs `mlb.py` via its exec tool, so relative paths can't be trusted
across cwds). Point them at *your* clone:

```bash
grep -rl '/home/mjw/projects/baseball-openclaw' workspace/ \
  | xargs sed -i "s|/home/mjw/projects/baseball-openclaw|$PWD|g"
```

### 2. Install OpenClaw

```bash
npm i -g openclaw
openclaw --version       # reference: 2026.4.14
```

If the version check complains `Node.js v22.12+ is required`, your default
`node` is too old — `nvm install 22 && nvm alias default 22`.

### 3. Create Homer's profile (`~/.openclaw-homer`)

OpenClaw keeps a profile's whole state — config, agent auth, sessions — under
one directory, selected by `--profile homer`. Keeping Homer in his own profile
leaves any default `~/.openclaw` install untouched. This repo carries
sanitized templates in [`examples/`](examples):

```bash
mkdir -p ~/.openclaw-homer/agents/main/agent
cp examples/openclaw.json ~/.openclaw-homer/openclaw.json
cp examples/auth-profiles.json ~/.openclaw-homer/agents/main/agent/auth-profiles.json
```

Then three edits:

1. `~/.openclaw-homer/openclaw.json` — set `agents.defaults.workspace` to this
   clone's `workspace/` directory (absolute path), and replace the gateway
   `auth.token` placeholder (`openssl rand -hex 24`).
2. `~/.openclaw-homer/agents/main/agent/auth-profiles.json` — your OpenAI key.
3. Nothing else. The persona and tool docs (`IDENTITY.md`, `AGENTS.md`,
   `TOOLS.md`, `USER.md`) are read natively from `workspace/` at session
   start — there is no prompt-injection machinery to configure.

### 4. Start the gateway

`openclaw acp` (what the voice server spawns) is a gateway *client* — without
a running gateway it dies with `ECONNREFUSED`. Install the unit template and
start it:

```bash
cp examples/openclaw-homer-gateway.service ~/.config/systemd/user/
# edit it: your node/openclaw paths + your OPENAI_API_KEY on the Environment= line
systemctl --user daemon-reload
systemctl --user enable --now openclaw-homer-gateway.service
systemctl --user status openclaw-homer-gateway.service   # active (running), :19011 loopback
```

(While experimenting you can skip systemd and just run
`openclaw --profile homer gateway --port 19011` in a spare terminal.)

### 5. Install the voice server (Pipecat)

Its own venv, so the audio stack's pins never fight anything else on the box:

```bash
python3.11 -m venv venv
venv/bin/pip install "pipecat-ai[deepgram,cartesia,silero,webrtc]==1.3.0" \
  fastapi uvicorn loguru python-dotenv
```

Reference versions this was built against: `pipecat-ai 1.3.0`,
`deepgram-sdk 7.3.0`, `aiortc 1.14.0`, `fastapi 0.136.3`, `uvicorn 0.48.0`.

Configure the voice keys:

```bash
cp .env.example .env    # then edit: Deepgram + Cartesia keys (voice id ships as "Austin")
chmod 600 .env
```

The brain's OpenAI key does **not** go in `.env` — it lives in the OpenClaw
profile and the gateway unit (step 3/4). If your OpenClaw binary or clone path
differ from the defaults in [`config.py`](config.py), override via env
(`OPENCLAW_BIN`, `OPENCLAW_CWD`, … — every value there is env-overridable).

### 6. Run it

```bash
venv/bin/python -u server.py
```

You should see `Homer-OpenClaw server up on 0.0.0.0:7865 — POST /api/offer to
connect.` and, a couple of seconds later, `OpenClaw brain pre-warmed in 2.5s`.
Check from another terminal:

```bash
curl http://localhost:7865/health          # -> {"status":"ok"}
python3 skills/mlb/mlb.py dodgers          # the data tool, standalone
```

Then point the Android Pipecat client at `http://<server-ip>:7865` (signaling
endpoint `/api/offer`) and say hello.

### 7. Run it as a service (optional)

```bash
cp examples/homer-openclaw.service ~/.config/systemd/user/
# edit it: WorkingDirectory + the venv python path for your box
systemctl --user daemon-reload
systemctl --user enable --now homer-openclaw.service
loginctl enable-linger $USER    # keep it running after logout
```

### Troubleshooting

- **`ECONNREFUSED` at startup / `brain pre-warm failed`** — the gateway isn't
  running (step 4); the ACP subprocess is a gateway client, not a standalone
  brain.
- **`Node.js v22.12+ is required`** — the `openclaw` shim resolves `node` from
  `PATH`; make Node 22 the default (`nvm alias default 22`) or use absolute
  node paths as the example gateway unit does.
- **Homer invents stats or won't run the tool** — the `mlb.py` cheat-sheet
  paths in `workspace/TOOLS.md` must point at your clone (step 1).
- **Turns are slow or wander off-topic** — check the tool denies in
  `~/.openclaw-homer/openclaw.json`: `web_fetch`, `web_search`, `browser`, and
  `image_generate` should stay denied so `mlb.py` via exec is the only data
  path.
- **No audio / connect fails from the phone** — client and server must share a
  network (or VPN); check `curl http://<server-ip>:7865/health` from the
  phone's network, and that nothing else owns port 7865.

## Ops

```bash
systemctl --user status openclaw-homer-gateway.service homer-openclaw.service
curl -s http://localhost:7865/health        # {"status":"ok"}
journalctl --user -u homer-openclaw.service -f
# brain alone (bypasses voice): see the ACP test snippets in git history
```

