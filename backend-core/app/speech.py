"""Backward-compatible imports for the speech infrastructure adapters.

New code should import from app.infrastructure.speech. This module remains as a
small compatibility surface for existing integrations and tests.
"""

from app.infrastructure.llm import LLMSemanticRefiner, SemanticRefinement
from app.infrastructure.speech import (
    HttpStreamingProvider,
    ProviderRegistry,
    ProviderSpeechServices,
    RecognitionUpdate,
    RemoteProviderConfig,
    SpeechProvider,
    SpeechServices,
    SpeechStream,
    SpeechUnavailableError,
)

__all__ = [
    "LLMSemanticRefiner",
    "SemanticRefinement",
    "HttpStreamingProvider",
    "ProviderRegistry",
    "ProviderSpeechServices",
    "RemoteProviderConfig",
    "SpeechProvider",
    "RecognitionUpdate",
    "SpeechServices",
    "SpeechStream",
    "SpeechUnavailableError",
]
