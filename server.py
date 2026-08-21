"""Homer-on-OpenClaw — the baseball agent's OpenClaw brain over the Pipecat
WebRTC stack on :7865, alongside the hermes-agent Homer on :7864. Same Android
Pipecat client, different brain framework — point the client's server URL here.

Same audio rails as the other surfaces (Deepgram STT + Cartesia TTS + Silero
VAD, the pipecat-1.3.0 patches, the RTVI 0.3.4 compat shim, 40ms TTS lead
silence). The LLM slot is `OpenClawLLMService`, streaming from an
`openclaw --profile homer acp` subprocess via the vendored BotWorker. No
Pipecat tools — OpenClaw owns the mlb.py tool and the persona inside the
brain (workspace docs in ./workspace, state in ~/.openclaw-homer, gateway
:19011 via openclaw-homer-gateway.service).
"""
from __future__ import annotations

import asyncio
import json as _json
import os
import sys
from pathlib import Path as _Path

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMTextFrame,
    MixerEnableFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
)
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.frames.frames import MetricsFrame  # token-usage metrics
from pipecat.metrics.metrics import LLMUsageMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.turns.user_start.vad_user_turn_start_strategy import (
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver

from botworker import BotWorker
from config import load_config
from openclaw_llm import OpenClawLLMService

# A single OpenClaw ACP session, booted once at startup and shared across
# connections — so connects skip the ~4s boot (the boot primes the brain with
# identity + workspace docs, which can't be made cheap). Trade-off: brain
# context persists across calls (fine for a single user; bound it later if
# needed). The per-turn brain latency (~6-18s, tool-dependent) is inherent and
# unaffected by this.
_BOT: BotWorker | None = None

load_dotenv()

logger.remove()
logger.add(sys.stderr, level=os.getenv("LOG_LEVEL", "INFO"))

HOST = os.getenv("HOMER_OC_HOST", "0.0.0.0")
PORT = int(os.getenv("HOMER_OC_PORT", "7865"))
TTS_LEAD_SILENCE_MS = 40
TYPING_SOUND_PATH = _Path(__file__).parent / "assets" / "keyboard-typing-24k.ogg"


def _require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"missing required env: {name}")
    return val


# --- Agent-screen state (shared with the unified display) --------------------
_DISPLAY_STATE = _Path(os.getenv(
    "HERMES_DISPLAY_STATE", str(_Path.home() / ".hermes" / "display.json")))
_GREET_SEED = ("Greet the user as Homer in ONE short, friendly sentence and "
               "ask what they'd like to know about baseball. Nothing else — "
               "no team or player name-drops, no examples, no second sentence.")


# brain_label lives outside this repo (shared across the demo box's voice
# surfaces for the little status LCD). Fresh installs won't have it — the
# display state is a no-op anywhere but the demo box, so degrade to a plain
# label instead of failing the import.
import sys as _sys
_sys.path.insert(0, os.getenv("HOMER_SHARED_LIB", "/home/mjw/projects/lib"))
try:
    import brain_label as _brain_label_mod   # shared across all four voice surfaces
    _BRAIN = _brain_label_mod.from_openclaw(
        path=str(_Path.home() / ".openclaw-homer" / "openclaw.json"))
except ImportError:
    _BRAIN = "LLM"

_IDENTITY = {"name": "Homer", "transport": "Pipecat", "agent": "OpenClaw",
             "brain": _BRAIN,
             "tools": ["mlb"]}


def _write_display_state(**fields) -> None:
    """Merge fields into the agent-screen JSON atomically. Never raises."""
    try:
        _DISPLAY_STATE.parent.mkdir(parents=True, exist_ok=True)
        cur = {}
        if _DISPLAY_STATE.exists():
            try:
                cur = _json.loads(_DISPLAY_STATE.read_text())
            except ValueError:
                cur = {}
        cur.update(fields)
        tmp = _DISPLAY_STATE.with_suffix(".tmp")
        tmp.write_text(_json.dumps(cur))
        tmp.replace(_DISPLAY_STATE)
    except Exception:
        pass


def _last_assistant(messages) -> str:
    for m in reversed(messages):
        content = m.get("content")
        if isinstance(content, str) and content.strip() and m.get("role") == "assistant":
            return content.strip()
    return ""


class TTSLeadSilence(FrameProcessor):
    """Pad the first audio frame of each TTS turn with leading silence so the
    first phoneme of short utterances doesn't clip on Android."""

    def __init__(self, pad_ms: int = TTS_LEAD_SILENCE_MS):
        super().__init__()
        self._pad_ms = pad_ms
        self._pad_pending = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSStartedFrame):
            self._pad_pending = True
            await self.push_frame(frame, direction)
            return
        if self._pad_pending and isinstance(frame, TTSAudioRawFrame):
            self._pad_pending = False
            silence_bytes = (
                frame.sample_rate * frame.num_channels * 2 * self._pad_ms // 1000
            )
            await self.push_frame(
                TTSAudioRawFrame(
                    audio=b"\x00" * silence_bytes,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                ),
                direction,
            )
        await self.push_frame(frame, direction)


class ThinkingSound(FrameProcessor):
    """Play the typing sound while the OpenClaw brain is working.

    OpenClaw's tool use / 'thinking' happens inside the ACP brain, not as Pipecat
    function calls — so there are no FunctionCalls frames to hook. Instead we fill
    the long 'thinking' gap directly: enable the transport mixer when the LLM
    starts a turn (LLMFullResponseStartFrame), and disable it on the first text
    chunk (LLMTextFrame), right before Homer starts speaking.
    """

    def __init__(self):
        super().__init__()
        self._armed = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._armed = True
            await self.push_frame(frame, direction)
            await self.push_frame(MixerEnableFrame(enable=True), direction)
            return
        if self._armed and isinstance(frame, LLMTextFrame):
            self._armed = False
            await self.push_frame(MixerEnableFrame(enable=False), direction)
        await self.push_frame(frame, direction)


class ActivityState(FrameProcessor):
    """Publish the agent's current activity to the shared display state so the
    LCD can show Listening / Thinking / Speaking. Driven by the pipeline frames:
    LLM start = Thinking, TTS start = Speaking, TTS stop = back to Listening.
    Placed late (after TTS) so it sees all three. Writes are best-effort.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            _write_display_state(activity="Thinking")
        elif isinstance(frame, BotStartedSpeakingFrame):
            _write_display_state(activity="Speaking")
        elif isinstance(frame, BotStoppedSpeakingFrame):
            _write_display_state(activity="Listening")
        await self.push_frame(frame, direction)


class TokenMeter(FrameProcessor):
    """Accumulate this session's LLM token usage and publish it to the LCD.
    Catches the MetricsFrame the LLM emits (real for Grok; an estimate for the
    OpenClaw ACP brain, which reports no usage). One instance per connection, so
    it resets to zero each call."""

    def __init__(self):
        super().__init__()
        self._tokens = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, MetricsFrame):
            for d in frame.data:
                if isinstance(d, LLMUsageMetricsData):
                    self._tokens += d.value.total_tokens
                    _write_display_state(tokens=self._tokens)
        await self.push_frame(frame, direction)


async def run_bot(transport: SmallWebRTCTransport) -> None:
    """Build and run the voice pipeline for a single WebRTC connection."""
    deepgram_key = _require_env("DEEPGRAM_API_KEY")
    cartesia_key = _require_env("CARTESIA_API_KEY")
    # Default voice = Cartesia "Austin - Conversational Guide", same as the
    # hermes-agent Homer on :7864.
    cartesia_voice = os.getenv(
        "CARTESIA_VOICE_ID", "1fcd23d0-bf12-4896-8f60-4f21ef5c9b98")
    cartesia_model = os.getenv("CARTESIA_MODEL", "sonic-3")

    # keyterm boosts recognition of these names on nova-3 (baseball names get
    # mangled otherwise: "Ohtani" -> "O'Tawny" etc.).
    stt = DeepgramSTTService(
        api_key=deepgram_key, sample_rate=16000,
        settings=DeepgramSTTService.Settings(keyterm=["Homer", "Ohtani", "Dodgers"]))

    # Reuse the pre-warmed shared brain (booted in _lifespan) — no per-connect
    # boot. Fall back to a per-connection boot if pre-warm failed.
    if _BOT is not None:
        llm = OpenClawLLMService(bot=_BOT)
        logger.info(f"LLM: OpenClaw ACP (shared pre-warmed session, brain={_IDENTITY['brain']})")
    else:
        logger.info("Booting OpenClaw brain per-connection (pre-warm unavailable)…")
        llm = await OpenClawLLMService.create(cfg=load_config())

    tts = CartesiaTTSService(
        api_key=cartesia_key,
        sample_rate=24000,
        settings=CartesiaTTSService.Settings(voice=cartesia_voice, model=cartesia_model),
    )

    vad = SileroVADAnalyzer(sample_rate=16000, params=VADParams(stop_secs=0.2))
    turn_strategies = UserTurnStrategies(
        start=[VADUserTurnStartStrategy(enable_interruptions=True)],
        stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.55)],
    )

    # No system prompt — OpenClaw owns persona/identity inside the brain.
    context = LLMContext(messages=[])
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad, user_turn_strategies=turn_strategies),
    )

    @aggregators.user().event_handler("on_user_turn_stopped")
    async def _on_user_turn(_aggregator, _strategy, message):
        text = getattr(message, "content", "")
        if isinstance(text, str) and text.strip():
            _write_display_state(state="LIVE", last_user=text.strip(), last_bot="")

    @aggregators.assistant().event_handler("on_assistant_turn_stopped")
    async def _on_assistant_turn(_aggregator, _message):
        _write_display_state(state="LIVE", last_bot=_last_assistant(context.messages))

    # RTVI + the 0.3.4 Android SDK compat shim (inject data.config: []).
    rtvi = RTVIProcessor()
    import pipecat as _pipecat
    from pipecat.frames.frames import OutputTransportMessageUrgentFrame
    from pipecat.processors.frameworks.rtvi import models as _rtvi_models

    async def _send_bot_ready_legacy(about=None):
        if not about:
            about = {"library": "pipecat-ai", "library_version": _pipecat.__version__}
        msg = {
            "label": "rtvi-ai",
            "type": "bot-ready",
            "id": rtvi._client_ready_id,
            "data": {"version": _rtvi_models.PROTOCOL_VERSION, "about": about, "config": []},
        }
        await rtvi.push_frame(OutputTransportMessageUrgentFrame(message=msg))

    rtvi._send_bot_ready = _send_bot_ready_legacy

    pipeline = Pipeline([
        transport.input(),
        rtvi,
        stt,
        aggregators.user(),
        llm,
        TokenMeter(),
        ThinkingSound(),
        tts,
        TTSLeadSilence(),
        ActivityState(),
        transport.output(),
        aggregators.assistant(),
    ])

    latency_observer = UserBotLatencyObserver()
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000, audio_out_sample_rate=24000,
            enable_metrics=True, enable_usage_metrics=True),
        observers=[latency_observer, RTVIObserver(rtvi)],
    )

    @transport.event_handler("on_client_connected")
    async def _on_client_connected(_transport, _client):
        logger.info("Client connected — OpenClaw Homer greeting.")
        _write_display_state(**_IDENTITY, state="LIVE", peer="mobile",
                             last_user="", last_bot="", activity="Listening", tokens=0)
        context.messages.append({"role": "user", "content": _GREET_SEED})
        await worker.queue_frame(LLMRunFrame())

    @transport.event_handler("on_client_disconnected")
    async def _on_client_disconnected(_transport, _client):
        logger.info("Client disconnected.")
        _write_display_state(**_IDENTITY, state="READY", peer="", activity="")

    from pipecat.workers.runner import WorkerRunner
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


# --- FastAPI signaling: POST /api/offer -------------------------------------
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import BackgroundTasks, FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pipecat.transports.smallwebrtc.request_handler import (  # noqa: E402
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

_webrtc_handler = SmallWebRTCRequestHandler()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    logger.info(f"Homer-OpenClaw server up on {HOST}:{PORT} — POST /api/offer to connect.")
    # Pre-warm the shared OpenClaw brain so the first connect's greeting isn't
    # delayed by the ~4s boot. Best-effort: if it fails, run_bot falls back to a
    # per-connection boot.
    global _BOT
    try:
        import time as _t
        cfg = load_config()
        _t0 = _t.monotonic()
        _BOT = BotWorker(
            bin_path=cfg["bot_bin"], profile=cfg.get("bot_profile") or None,
            agent_id=cfg["bot_agent"], cwd=cfg["bot_cwd"],
            thought_level=cfg.get("bot_thought_level", "off"),
            timeout_secs=cfg.get("bot_timeout_secs", 120),
            identity_path=cfg.get("bot_identity_path") or None,
            user_name=cfg.get("user_name"), user_name_spoken=cfg.get("user_name_spoken"),
            workspace_docs=cfg.get("workspace_docs", []),
        )
        await _BOT.start()
        logger.info(f"OpenClaw brain pre-warmed in {_t.monotonic()-_t0:.1f}s")
    except Exception as e:
        logger.warning(f"brain pre-warm failed ({e}); will boot per-connection")
        _BOT = None
    # No display startup write — Hermes owns the idle baseline; this surface
    # only writes on connect/disconnect/turn (same rule as Pica).
    yield
    if _BOT is not None:
        try:
            await _BOT.close()
        except Exception:
            pass
    await _webrtc_handler.close()


app = FastAPI(lifespan=_lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/offer")
async def offer(request: dict, background_tasks: BackgroundTasks):
    async def on_connection(connection):
        # Typing sound under the long 'thinking' gap; ThinkingSound flips it
        # on/off via MixerEnableFrame. Starts disabled (mixing=False).
        mixer = SoundfileMixer(
            sound_files={"typing": str(TYPING_SOUND_PATH)},
            default_sound="typing",
            volume=0.45,
            mixing=False,
            loop=True,
        )
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_out_mixer=mixer,
                audio_in_passthrough=True,
                vad_analyzer=SileroVADAnalyzer(
                    sample_rate=16000, params=VADParams(stop_secs=0.2)),
            ),
        )
        background_tasks.add_task(run_bot, transport)

    answer = await _webrtc_handler.handle_web_request(
        request=SmallWebRTCRequest.from_dict(dict(request)),
        webrtc_connection_callback=on_connection,
    )
    return JSONResponse(content=answer)


if __name__ == "__main__":
    import uvicorn
    try:
        uvicorn.run(app, host=HOST, port=PORT)
    except KeyboardInterrupt:
        pass
