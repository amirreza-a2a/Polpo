from typing import List, Optional, Tuple
from typing_extensions import Protocol, runtime_checkable


@runtime_checkable
class IProviderDetector(Protocol):
    """
    Abstract port for automatically detecting AI provider and supported models from an API key.
    """

    def detect_provider_and_models(
        self, api_key: str, base_url: Optional[str] = None
    ) -> Tuple[Optional[str], List[str]]:
        """
        Detects provider name and active model list via API key or base URL.
        Returns: (provider_name, list_of_models)
        """
        ...

    def get_default_base_url(self, provider: str) -> Optional[str]:
        """Returns the default base URL for the given provider."""
        ...

    def get_default_model(self, provider: str) -> str:
        """Returns the default model name for the given provider."""
        ...
