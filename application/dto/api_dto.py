# ============================================================
#  application/dto/api_dto.py
# ============================================================

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ApiSlotDTO:
    """Safe presentation DTO representing an API slot with zero secret exposure."""
    id: int
    provider: str
    label: str
    slot_type: str
    selected_model: Optional[str]
    base_url: Optional[str]
    supported_models: Optional[List[str]] = None


@dataclass
class RegisterKeyCommand:
    """Command to register a new BYOK API key and slot in the desktop application."""
    provider: str
    api_key: str
    label: str
    selected_model: Optional[str] = None
    base_url: Optional[str] = None
    supported_models: Optional[List[str]] = None
    slot_type: str = "byok"


@dataclass
class UpdateKeyCommand:
    """Command to update an existing API key slot in the desktop application."""
    slot_id: int
    api_key: Optional[str] = None
    label: Optional[str] = None
    selected_model: Optional[str] = None
    base_url: Optional[str] = None
    supported_models: Optional[List[str]] = None


# Legacy Compatibility Commands (for frozen transports)
@dataclass
class RegisterApiCommand:
    user_id: int
    provider: str
    api_key: str
    label: str
    selected_model: Optional[str] = None
    base_url: Optional[str] = None


@dataclass
class DonateApiCommand:
    user_id: int
    provider: str
    api_key: str
    label: str
    models: List[str]


@dataclass
class DetectApiResultDTO:
    provider: Optional[str]
    models: List[str]
    default_model: Optional[str] = None
    default_base_url: Optional[str] = None
