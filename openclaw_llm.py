"""Pipecat LLMService backed by OpenClaw's ACP brain (via BotWorker).

OpenClaw isn't an OpenAI-compatible endpoint — its brain is reached through an
`openclaw acp` subprocess, managed by the transport-agnostic BotWorker. This
adapter bridges that to Pipecat's LLM contract: on each LLMContextFrame it
pulls the latest user turn, streams the reply from ACP, splits it into
sentence-ish chunks (so first-audio latency stays low), and pushes them as
LLMTextFrames toward TTS.

No tools are registered here — OpenClaw brings its own tools inside the brain.
The brain also owns persona/identity (IDENTITY.md + VOICE_RULES via BotWorker's
boot handshake), so the Pipecat LLMContext's system prompt is irrelevant; only
the latest user message is forwarded.
"""
from __future__ import annotations

import re

from loguru import logger

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.metrics.metrics import LLMTokenUsage

from botworker import BotWorker, clean_response
from voice_directive import VOICE_TURN_TAG

# --- sentence chunking --------------------------------------------------------
# Emit on terminal punctuation; before the first emit, also accept a clause
# boundary (>= _SOFT_MIN_CHARS) or a char-budget split, to shrink time-to-first-
# audio on long opening sentences.
_SENTENCE_BOUNDARY = re.compile(r'([.!?]+["\'\)\]]*)\s+')
_SOFT_BOUNDARY = re.compile(r'[,;:]\s+|\s—\s+')
_SOFT_MIN_CHARS = 20
_BUDGET_CHARS = 80


def _find_emit(buf: str, first_done: bool):
    m = _SENTENCE_BOUNDARY.search(buf)
    if m:
        return m.end()
    if first_done:
        return None
    if len(buf) >= _SOFT_MIN_CHARS:
        sm = _SOFT_BOUNDARY.search(buf, _SOFT_MIN_CHARS)
        if sm:
            return sm.end()
    if len(buf) >= _BUDGET_CHARS:
        idx = buf.find(' ', _BUDGET_CHARS)
        if idx >= 0:
            return idx + 1
    return None


def _latest_user_text(context) -> str:
    for m in reversed(context.messages):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        if role != "user":
            continue
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for it in content:
                if isinstance(it, str):
                    parts.append(it)
                elif isinstance(it, dict) and it.get("text"):
                    parts.append(it["text"])
                elif getattr(it, "text", None):
                    parts.append(it.text)
            return " ".join(parts)
        return ""
    return ""


class OpenClawLLMService(LLMService):
    """Pipecat LLM service backed by an OpenClaw ACP BotWorker."""

    def __init__(self, *, bot: BotWorker, owns_bot: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._bot = bot
        self._owns_bot = owns_bot  # shared (pre-warmed) bots are owned by the server

    def can_generate_metrics(self) -> bool:
        # Base FrameProcessor returns False, which gates off start_llm_usage_metrics
        # — must override so our estimated token usage actually emits a MetricsFrame.
        return True

    @classmethod
    async def create(cls, *, cfg: dict, **kwargs) -> "OpenClawLLMService":
        """Construct + boot the BotWorker (spawns `openclaw acp`), return ready."""
        bot = BotWorker(
            bin_path=cfg["bot_bin"],
            profile=cfg.get("bot_profile") or None,
            agent_id=cfg["bot_agent"],
            cwd=cfg["bot_cwd"],
            thought_level=cfg.get("bot_thought_level", "off"),
            timeout_secs=cfg.get("bot_timeout_secs", 120),
            identity_path=cfg.get("bot_identity_path") or None,
            user_name=cfg.get("user_name"),
            user_name_spoken=cfg.get("user_name_spoken"),
            workspace_docs=cfg.get("workspace_docs", []),
        )
        await bot.start()
        return cls(bot=bot, owns_bot=True, **kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            await self.push_frame(LLMFullResponseStartFrame())
            await self.start_processing_metrics()
            try:
                await self._stream_turn(frame.context)
            finally:
                await self.stop_processing_metrics()
                await self.push_frame(LLMFullResponseEndFrame())
        else:
            await self.push_frame(frame, direction)

    async def _stream_turn(self, context):
        user_text = _latest_user_text(context).strip()
        if not user_text:
            return
        await self.start_ttfb_metrics()
        buf = ""
        first_done = False
        emitted = 0
        out_chars = 0
        try:
            async for raw in self._bot.prompt_streaming(VOICE_TURN_TAG + user_text):
                buf += raw
                while True:
                    end = _find_emit(buf, first_done)
                    if end is None:
                        break
                    sentence = clean_response(buf[:end])
                    buf = buf[end:]
                    if not sentence:
                        continue
                    if not first_done:
                        await self.stop_ttfb_metrics()
                        first_done = True
                    await self.push_frame(LLMTextFrame(sentence + " "))
                    emitted += 1
                    out_chars += len(sentence)
            tail = clean_response(buf)
            if tail:
                if not first_done:
                    await self.stop_ttfb_metrics()
                await self.push_frame(LLMTextFrame(tail + " "))
                emitted += 1
                out_chars += len(tail)
        except Exception as e:
            logger.warning(f"OpenClaw turn failed: {e}")
        if emitted == 0:
            await self.push_frame(
                LLMTextFrame("Sorry, I didn't catch that. Could you repeat?"))
        # ACP reports no token usage, so emit a rough estimate (~4 chars/token,
        # spoken text only — undercounts the brain's hidden prefill) to feed the
        # display's token meter via the same MetricsFrame path Grok uses.
        try:
            est_in = max(1, len(user_text) // 4)
            est_out = max(0, out_chars // 4)
            await self.start_llm_usage_metrics(LLMTokenUsage(
                prompt_tokens=est_in, completion_tokens=est_out,
                total_tokens=est_in + est_out))
        except Exception:
            pass

    async def cleanup(self):
        await super().cleanup()
        if self._owns_bot:  # don't close a shared/pre-warmed bot owned by the server
            try:
                await self._bot.close()
            except Exception:
                pass
