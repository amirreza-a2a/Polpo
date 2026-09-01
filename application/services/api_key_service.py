# ============================================================
#  application/services/api_key_service.py
# ============================================================

import uuid
import logging
from dataclasses import replace
from typing import List, Optional

from application.dto.api_dto import ApiSlotDTO, RegisterKeyCommand, UpdateKeyCommand
from application.ports.credential_resolver import ICredentialResolver
from application.ports.provider_detector import IProviderDetector
from application.ports.unit_of_work import IUnitOfWorkFactory
from core.entities.api_slot import ApiSlot
from core.entities.credential_ref import CredentialRef
from core.exceptions.domain_exceptions import EntityNotFoundError, CredentialConsistencyError

logger = logging.getLogger("application.services.api_key_service")


class ApiKeyService:
    """
    Application service managing BYOK credentials and metadata for the desktop application.
    Coordinates secret storage via ICredentialResolver and metadata storage via SQLite UoW.
    """

    def __init__(
        self,
        uow_factory: IUnitOfWorkFactory,
        credential_resolver: ICredentialResolver,
        provider_detector: Optional[IProviderDetector] = None,
    ):
        self.uow_factory = uow_factory
        self.credential_resolver = credential_resolver
        self.provider_detector = provider_detector

    def _to_dto(self, slot: ApiSlot) -> ApiSlotDTO:
        return ApiSlotDTO(
            id=slot.id,
            provider=slot.provider,
            label=slot.label,
            slot_type=slot.slot_type,
            selected_model=slot.selected_model,
            base_url=slot.base_url,
            supported_models=slot.supported_models,
        )

    def register_key(self, cmd: RegisterKeyCommand) -> ApiSlotDTO:
        """
        Registers a new BYOK API key. Securely stores the secret in the credential resolver
        and records the non-sensitive ApiSlot metadata in SQLite.
        """
        if not cmd.provider:
            raise ValueError("Provider must not be empty.")
        if not cmd.api_key:
            raise ValueError("API key must not be empty.")
        if not cmd.label:
            raise ValueError("Label must not be empty.")

        cred_identifier = f"{cmd.provider}_{uuid.uuid4().hex[:12]}"
        cred_ref = CredentialRef(
            identifier=cred_identifier,
            provider=cmd.provider,
            slot_type=cmd.slot_type or "byok",
        )

        # 1. Store raw secret securely in the credential resolver
        self.credential_resolver.store_api_key(cred_ref, cmd.api_key)

        # 2. Persist slot metadata in SQLite
        try:
            with self.uow_factory.create() as uow:
                slot = ApiSlot(
                    id=None,
                    provider=cmd.provider,
                    label=cmd.label,
                    credential_ref=cred_ref,
                    slot_type=cmd.slot_type or "byok",
                    selected_model=cmd.selected_model,
                    base_url=cmd.base_url,
                    supported_models=cmd.supported_models,
                )
                saved_slot = uow.apis.save(slot)
                uow.commit()
                return self._to_dto(saved_slot)
        except Exception:
            logger.error("Failed to persist ApiSlot in database for identifier %s. Cleaning up secret.", cred_identifier)
            try:
                self.credential_resolver.delete_api_key(cred_ref)
            except Exception as cleanup_err:
                logger.warning("Failed to clean up secret for identifier %s: %s", cred_identifier, cleanup_err)
            raise

    def update_key(self, cmd: UpdateKeyCommand) -> ApiSlotDTO:
        """
        Updates an existing API key slot. If a new API key is provided, verifies that the existing
        credential can be resolved first for rollback safety, writes the new secret, updates metadata,
        and restores the old secret if metadata persistence fails.
        """
        with self.uow_factory.create() as uow:
            slot = uow.apis.get_by_id(cmd.slot_id)
            if not slot:
                raise EntityNotFoundError("ApiSlot", cmd.slot_id)

            old_secret: Optional[str] = None
            if cmd.api_key:
                # Resolve existing secret upfront for rollback safety. If resolution fails, abort update.
                old_secret = self.credential_resolver.resolve_api_key(slot.credential_ref)
                self.credential_resolver.store_api_key(slot.credential_ref, cmd.api_key)

            updated_slot = replace(
                slot,
                label=cmd.label if cmd.label is not None else slot.label,
                selected_model=cmd.selected_model if cmd.selected_model is not None else slot.selected_model,
                base_url=cmd.base_url if cmd.base_url is not None else slot.base_url,
                supported_models=cmd.supported_models if cmd.supported_models is not None else slot.supported_models,
            )

            try:
                saved = uow.apis.save(updated_slot)
                uow.commit()
                return self._to_dto(saved)
            except Exception:
                if cmd.api_key and old_secret:
                    try:
                        self.credential_resolver.store_api_key(slot.credential_ref, old_secret)
                    except Exception as rb_err:
                        logger.error(
                            "CRITICAL: Failed to restore previous secret during update failure for identifier %s: %s",
                            slot.credential_ref.identifier,
                            rb_err,
                        )
                        raise CredentialConsistencyError(
                            f"Metadata persistence failed and secret rollback failed for identifier '{slot.credential_ref.identifier}'."
                        )
                raise

    def delete_key(self, slot_id: int) -> bool:
        """
        Deletes the ApiSlot metadata from SQLite and removes the associated secret.
        Surfaces CredentialConsistencyError if metadata is deleted but secret deletion fails.
        """
        with self.uow_factory.create() as uow:
            slot = uow.apis.get_by_id(slot_id)
            if not slot:
                return False

            deleted = uow.apis.delete(slot_id)
            uow.commit()

        if deleted:
            try:
                secret_deleted = self.credential_resolver.delete_api_key(slot.credential_ref)
                if not secret_deleted:
                    logger.warning(
                        "Metadata for slot %d deleted, but secret was not found or not deleted for identifier %s",
                        slot_id,
                        slot.credential_ref.identifier,
                    )
            except Exception as e:
                logger.error(
                    "CRITICAL: Metadata for slot %d deleted, but secret deletion failed for identifier %s: %s",
                    slot_id,
                    slot.credential_ref.identifier,
                    e,
                )
                raise CredentialConsistencyError(
                    f"Slot {slot_id} metadata was deleted from database, but secret deletion failed for identifier '{slot.credential_ref.identifier}'."
                )

        return deleted

    def list_slots(self) -> List[ApiSlotDTO]:
        """
        Returns all registered API slots as safe DTOs with no secret exposure.
        """
        with self.uow_factory.create() as uow:
            slots = uow.apis.list_all()
            return [self._to_dto(s) for s in slots]

    def get_slot(self, slot_id: int) -> Optional[ApiSlotDTO]:
        """
        Returns a single API slot by ID as a safe DTO.
        """
        with self.uow_factory.create() as uow:
            slot = uow.apis.get_by_id(slot_id)
            return self._to_dto(slot) if slot else None

    def test_key(self, slot_id: int) -> bool:
        """
        Verifies that the credential for the given slot can be resolved.
        """
        with self.uow_factory.create() as uow:
            slot = uow.apis.get_by_id(slot_id)
            if not slot:
                return False

        try:
            key = self.credential_resolver.resolve_api_key(slot.credential_ref)
            return bool(key and len(key.strip()) > 0)
        except Exception:
            return False
