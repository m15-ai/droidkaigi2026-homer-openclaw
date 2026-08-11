"""Bot config for Homer's OpenClaw ACP brain.

Points at the isolated `homer` OpenClaw profile (state in ~/.openclaw-homer,
gateway :19011 via openclaw-homer-gateway.service), so it never touches a
default OpenClaw install at ~/.openclaw. Override any value via env.

No identity boot: OpenClaw natively reads the workspace docs (IDENTITY.md /
AGENTS.md / TOOLS.md / USER.md) from the workspace, so the persona and voice
rules are already in the brain without spending a boot turn on them.
"""
import os


def load_config() -> dict:
    return {
        "bot_bin": os.getenv(
            "OPENCLAW_BIN", "/home/mjw/.nvm/versions/node/v22.22.2/bin/openclaw"),
        "bot_profile": os.getenv("OPENCLAW_PROFILE", "homer"),
        "bot_agent": os.getenv("OPENCLAW_AGENT", "main"),
        "bot_cwd": os.getenv(
            "OPENCLAW_CWD", "/home/mjw/projects/baseball-openclaw/workspace"),
        "bot_thought_level": os.getenv("OPENCLAW_THOUGHT", "off"),
        "bot_timeout_secs": int(os.getenv("OPENCLAW_TIMEOUT", "120")),
        "bot_identity_path": os.getenv("OPENCLAW_IDENTITY", ""),  # "" = no boot
        "user_name": os.getenv("OPENCLAW_USER", ""),
        "user_name_spoken": os.getenv("OPENCLAW_USER_SPOKEN", ""),
        "workspace_docs": [],
    }
