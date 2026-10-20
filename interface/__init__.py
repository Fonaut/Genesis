"""
Genesis Communication Interface package.
Local, secure browser-based interface for Genesis, Antigravity, and OpenAI-Agent.
"""

from interface.engine import CommunicationInterfaceEngine
from interface.models import (
    IndividualChatRequest,
    TriadInitiateRequest,
    TriadGateResponseRequest,
    KillSwitchRequest,
    JournalActionRequest,
)

__all__ = [
    "CommunicationInterfaceEngine",
    "IndividualChatRequest",
    "TriadInitiateRequest",
    "TriadGateResponseRequest",
    "KillSwitchRequest",
    "JournalActionRequest",
]
