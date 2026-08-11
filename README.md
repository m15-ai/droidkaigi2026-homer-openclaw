# Homer-on-OpenClaw — baseball voice agent (:7865)

The same Homer persona as [baseball-agent](../baseball-agent) (Nous
hermes-agent, :7864), rebuilt on **OpenClaw** — a side-by-side comparison of
two agent frameworks behind the identical Pipecat WebRTC voice stack. Point
the Android Pipecat client at `http://<server-ip>:7865` (`/api/offer`) to talk
to this one.

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

## Setting it up fresh

Sanitized templates for everything host-specific are in [`examples/`](examples):
`openclaw.json` + `auth-profiles.json` go under `~/.openclaw-homer/` (the
latter at `agents/main/agent/`, with your OpenAI key), and the two systemd
units go in `~/.config/systemd/user/`. Install OpenClaw itself with
`npm i -g openclaw` (Node 22+), fix the absolute `/home/mjw/...` paths to
match your box, and start the gateway before the voice server.

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
