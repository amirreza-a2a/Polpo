# ============================================================
#  infrastructure/persistence/credential_resolver.py
# ============================================================

import os
from typing import Optional
from database.connection import DatabaseManager
from application.ports.credential_resolver import ICredentialResolver
from core.entities.credential_ref import CredentialRef
from core.exceptions.domain_exceptions import EntityNotFoundError


class MySQLCredentialResolver(ICredentialResolver):
    """
    Legacy MySQL credential resolver adapter for frozen transports.
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db_manager = db_manager or DatabaseManager()

    def resolve_api_key(self, credential_ref: CredentialRef) -> str:
        if credential_ref.slot_type == "system":
            env_key = f"{credential_ref.provider.upper()}_API_KEY"
            key = os.getenv(env_key) or os.getenv(credential_ref.identifier)
            if not key:
                raise EntityNotFoundError("System API Credential", env_key)
            return key

        table = "private_apis" if credential_ref.slot_type == "private" else "public_apis"
        with self.db_manager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT api_key FROM {table} WHERE id = %s", (credential_ref.identifier,))
                row = cur.fetchone()
                if not row or not row[0]:
                    raise EntityNotFoundError(f"API Credential ({credential_ref.slot_type})", credential_ref.identifier)
                return row[0]

    def store_api_key(self, credential_ref: CredentialRef, api_key: str) -> None:
        table = "private_apis" if credential_ref.slot_type == "private" else "public_apis"
        with self.db_manager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE {table} SET api_key = %s WHERE id = %s", (api_key, credential_ref.identifier))

    def delete_api_key(self, credential_ref: CredentialRef) -> bool:
        table = "private_apis" if credential_ref.slot_type == "private" else "public_apis"
        with self.db_manager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {table} WHERE id = %s", (credential_ref.identifier,))
                return cur.rowcount > 0
