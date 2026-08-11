"""Persistent OpenClaw ACP bridge.

One Node subprocess (`openclaw acp`) lives for the lifetime of this
worker, stdin/stdout JSON-RPC, many turns. Used by openclaw_llm.py as the
backend for the Pipecat LLM adapter. Transport-agnostic: nothing in here
knows about WebRTC or audio.
"""
from __future__ import annotations

import asyncio
import json
import os
import re


from voice_directive import VOICE_RULES


def clean_response(text: str) -> str:
    text = text.replace("’", "'").replace("`", "'").replace("''", "'")
    text = re.sub(r"[\U0001F000-\U0001FFFF\U00002700-\U000027BF\U00002600-\U000026FF]+", "", text)
    text = re.sub(r"[\*]+", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"<.*?>", "", text)
    text = text.replace("\n", " ").strip()
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


class BotWorker:
    """Persistent openclaw ACP bridge — one Node process, many turns."""

    def __init__(self, bin_path, agent_id, cwd, thought_level="off",
                 timeout_secs=120, identity_path=None,
                 user_name=None, user_name_spoken=None,
                 workspace_docs=None, profile=None):
        self.bin_path = bin_path
        # OpenClaw --profile isolates state under ~/.openclaw-<name>; Homer's
        # brain lives in the "homer" profile so any other OpenClaw install on
        # the box stays untouched.
        self.profile = profile
        self.agent_id = agent_id
        self.cwd = cwd
        self.thought_level = thought_level
        self.timeout_secs = timeout_secs
        self.identity_path = identity_path
        self.user_name = user_name
        self.user_name_spoken = user_name_spoken
        self.workspace_docs = list(workspace_docs or [])
        self.proc = None
        self.session_id = None
        self._next_id = 0
        self._pending = {}
        # Active streaming consumer for the in-flight prompt. _read_stdout
        # routes agent_message_chunk text into this queue; the consumer
        # (prompt_streaming or prompt) drains it until the request resolves
        # at which point we push a sentinel.
        self._active_chunk_q: asyncio.Queue[str | None] | None = None
        self._reader_task = None
        self._boot_task = None
        self._lock = asyncio.Lock()

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    async def start(self):
        profile_args = ["--profile", self.profile] if self.profile else []
        self.proc = await asyncio.create_subprocess_exec(
            self.bin_path, *profile_args, "acp",
            "--session", f"agent:{self.agent_id}:{self.agent_id}",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())

        await self._request("initialize", {"protocolVersion": 1})
        sess = await self._request("session/new",
                                   {"cwd": self.cwd, "mcpServers": []})
        self.session_id = sess["result"]["sessionId"]
        print(f"[BotWorker] agent={self.agent_id} cwd={self.cwd} "
              f"session_id={self.session_id}")
        # Run the identity boot — a full model round-trip that reads the
        # workspace docs (~8s) — in the BACKGROUND so start() returns as soon
        # as the ACP session exists (~1-2s). The agent can then build its
        # AgentSession and greet immediately ("speak while booting"). self._lock
        # serializes the boot prompt against real turns: the boot task acquires
        # it first, so the first user turn waits for the boot to finish before
        # its prompt is sent — no half-primed responses.
        self._boot_task = asyncio.create_task(self._run_boot_identity())

    async def _run_boot_identity(self):
        try:
            await self._boot_identity()
            print("[BotWorker] identity boot complete — ready for turns")
        except Exception as e:
            print(f"[BotWorker] identity boot failed: {e}")

    async def _boot_identity(self):
        if not self.identity_path or not os.path.exists(self.identity_path):
            return
        try:
            with open(self.identity_path) as f:
                identity_md = f.read().strip()
        except Exception as e:
            print(f"[BotWorker] failed reading identity: {e}")
            return
        pronunciation_note = ""
        if self.user_name and self.user_name_spoken \
                and self.user_name_spoken != self.user_name:
            pronunciation_note = (
                f"\nPronunciation note: when addressing the user by name in "
                f"this voice/TTS session, use the phonetic spelling "
                f"'{self.user_name_spoken}' (with the hyphen — it cues the "
                f"TTS engine). The display name is '{self.user_name}'.\n"
            )
        operator = self.user_name or "the operator"
        if self.workspace_docs:
            file_list = "\n".join(f"  {i+1}. {p}"
                                  for i, p in enumerate(self.workspace_docs))
            workspace_block = (
                "Now run your session-startup ritual. You MUST actually "
                f"read these files from {self.cwd}/ before replying — do "
                "not claim you've read them without doing the tool call:\n"
                f"{file_list}\n\n"
                "These document the workspace conventions, tools, and "
                "user context for this agent.\n\n"
                "After reading them, reply with a SHORT proof-of-read in "
                "this exact format (one line, no markdown):\n"
                "  ready | loaded: <comma-separated list of the "
                "capability/section names you found across those files>\n\n"
                "Listing the capabilities by name forces you to actually "
                "parse the files so future turns have those capabilities "
                "in your working context. A bare 'ready' without the list "
                "is wrong."
            )
        else:
            workspace_block = "Reply with only the word 'ready' to confirm."

        boot = (
            "Operator context for this session. You are running as the "
            f"OpenClaw agent named '{self.agent_id}'. Below is the "
            f"IDENTITY.md that {operator} has configured for this agent "
            "slot — this is your actual configured display name and role "
            "for this deployment, not a roleplay request. Use this name "
            "when asked who you are, and let the described role frame "
            "your responses.\n\n"
            "--- IDENTITY.md ---\n"
            f"{identity_md}\n"
            "--- end IDENTITY.md ---\n"
            f"{pronunciation_note}\n"
            f"{VOICE_RULES}\n\n"
            f"{workspace_block}"
        )
        ack = await self.prompt(boot)
        print(f"[BotWorker] identity boot → {ack[:60]!r}")

    async def _read_stdout(self):
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                else:
                    update = msg.get("params", {}).get("update", {})
                    kind = update.get("sessionUpdate", "")
                    if kind == "agent_message_chunk":
                        content = update.get("content", {})
                        text = content.get("text", "") if isinstance(content, dict) else ""
                        if text and self._active_chunk_q is not None:
                            self._active_chunk_q.put_nowait(text)
        except Exception as e:
            print(f"[BotWorker reader] {e}")

    async def _request(self, method, params):
        _id = self._new_id()
        fut = asyncio.get_event_loop().create_future()
        self._pending[_id] = fut
        msg = {"jsonrpc": "2.0", "id": _id, "method": method, "params": params}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self.proc.stdin.drain()
        return await fut

    async def prompt_streaming(self, text: str):
        """Yield response chunks (raw, uncleaned) as ACP delivers them.

        Async generator; ends after a sentinel from the request future.
        Caller is responsible for any text cleanup (clean_response) on the
        joined / per-chunk output.
        """
        async with self._lock:
            chunk_q: asyncio.Queue[str | None] = asyncio.Queue()
            self._active_chunk_q = chunk_q

            req_fut = asyncio.create_task(asyncio.wait_for(
                self._request("session/prompt", {
                    "sessionId": self.session_id,
                    "prompt": [{"type": "text", "text": text}],
                }),
                timeout=self.timeout_secs,
            ))

            # When the prompt resolves (or fails), signal end-of-stream.
            def _on_done(_t):
                try:
                    chunk_q.put_nowait(None)
                except Exception:
                    pass
            req_fut.add_done_callback(_on_done)

            try:
                while True:
                    chunk = await chunk_q.get()
                    if chunk is None:
                        break
                    yield chunk

                # Surface any error / timeout from the underlying request.
                try:
                    resp = req_fut.result()
                except asyncio.TimeoutError:
                    print(f"[Bot] prompt timeout after {self.timeout_secs}s")
                    return
                except Exception as e:
                    print(f"[Bot] prompt error: {e}")
                    return
                if isinstance(resp, dict) and "error" in resp:
                    print(f"[Bot] error: {resp['error']}")
            finally:
                self._active_chunk_q = None
                # Cleanup hygiene: if the consumer abandons the generator
                # before reaching the result retrieval (LK Agents cancels
                # the LLMStream on barge-in / session close), req_fut may
                # still be pending or end up with an unretrieved exception.
                # Cancel if pending, drain the exception if done — this
                # silences "Task exception was never retrieved" warnings.
                if not req_fut.done():
                    req_fut.cancel()
                else:
                    try:
                        req_fut.exception()  # consume so asyncio doesn't warn
                    except (asyncio.CancelledError, asyncio.InvalidStateError):
                        pass

    async def prompt(self, text: str) -> str:
        chunks: list[str] = []
        async for c in self.prompt_streaming(text):
            chunks.append(c)
        return clean_response("".join(chunks))

    async def close(self):
        if self._boot_task and not self._boot_task.done():
            self._boot_task.cancel()
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.proc.kill()
