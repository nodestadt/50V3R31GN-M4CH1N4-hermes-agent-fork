"""Abstract base class for pluggable model providers.

Model providers allow external inference backends (VSB, local servers, 
cloud providers) to be registered as native Hermes plugins.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ModelProvider(ABC):
    """Abstract base class for model providers."""

    @abstractmethod
    def list_models(self) -> List[Dict]:
        """List all available models from this provider."""

    @abstractmethod
    def get_model_info(self, model_id: str) -> Dict:
        """Return metadata for a specific model."""

    @abstractmethod
    def generate(
        self,
        model_id: str,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> Dict:
        """Generate a completion for the given messages."""

    def is_available(self) -> bool:
        """Return True if the provider is correctly configured and reachable."""
        return True
