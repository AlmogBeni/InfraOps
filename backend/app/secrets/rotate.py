"""Re-encrypt every stored credential under the current encryption key.

Usage (inside the backend container): ``python -m app.secrets.rotate``
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import session_factory
from app.models.platform import SecretReference
from app.secrets.encryption import reencrypt_secret

log = get_logger(__name__)


async def rotate_all() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    count = 0
    async with session_factory() as db:
        rows = (await db.execute(select(SecretReference).with_for_update())).scalars().all()
        for row in rows:
            if row.encrypted_username:
                row.encrypted_username = reencrypt_secret(row.encrypted_username)
            if row.encrypted_password:
                row.encrypted_password = reencrypt_secret(row.encrypted_password)
            count += 1
        await db.commit()
    log.info("Re-encrypted %d stored credential(s) under the current key.", count)
    return count


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(rotate_all())
