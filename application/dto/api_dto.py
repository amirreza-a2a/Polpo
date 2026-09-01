# ============================================================
#  application/dto/api_dto.py
# ============================================================

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ApiSlotDTO:
    id: int
    provider: str
    label: str
    slot_type: str
    selected_model: Optional[str]
    base_url: Optional[str]
    supported_models: Optional[List[str]] = None


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
