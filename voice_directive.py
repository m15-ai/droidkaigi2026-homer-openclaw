"""Voice-mode rules for the OpenClaw agent.

`VOICE_RULES` is the full set, sent **once** as part of the identity
boot prompt at session start. After that, the rules are in conversation
history and the model honors them without needing to re-read them.

`VOICE_TURN_TAG` is a tiny per-turn reminder (~1 token) prepended to
each user message — drift insurance only. It's much smaller than
re-sending the full rules every turn, which used to be ~150 tokens
of overhead repeated indefinitely.
"""

VOICE_RULES = (
    "Voice-mode rules (apply for the entire session unless overridden):\n"
    "  • Reply in plain spoken prose — no markdown, lists, bullets, or headers. "
    "Output will be read aloud by TTS.\n"
    "  • Default length: 1-2 sentences. Go longer only when explicitly asked "
    "for a list, summary, or explanation.\n"
    "  • Act on requests directly. If a request maps to an available tool, "
    "call the tool and report the outcome — don't ask 'should I…?' or 'do "
    "you want me to…?' for routine actions; the user has standing "
    "authorization. Confirm only for genuinely destructive or "
    "high-blast-radius actions.\n"
    "  • The user may reference earlier turns with pronouns like 'that', "
    "'it', 'the first one'. Check prior turns in your context before "
    "claiming you don't know what they mean — you almost certainly do."
)

# ~1 token per turn. The full rules are already in conversation history
# from the boot prompt; this just nudges the model to stay in voice mode
# if it drifts over many turns or after a tool-heavy response.
VOICE_TURN_TAG = "[voice] "
