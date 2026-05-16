"""
Repository for Account DB operations.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Account, AccountStatus
from utils.session_crypto import maybe_encrypt_session


class AccountRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict) -> Account:
        if data.get("session_string"):
            data = {**data, "session_string": maybe_encrypt_session(data["session_string"])}
        account = Account(**data)
        self.db.add(account)
        await self.db.flush()
        await self.db.refresh(account)
        return account

    async def get_by_id(self, account_id: str) -> Optional[Account]:
        result = await self.db.execute(
            select(Account).where(Account.id == account_id)
        )
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> Optional[Account]:
        result = await self.db.execute(
            select(Account).where(Account.phone == phone)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> List[Account]:
        result = await self.db.execute(select(Account).order_by(Account.created_at))
        return list(result.scalars().all())

    async def get_available(self) -> Optional[Account]:
        """Return an active account not in floodwait cooldown."""
        now = datetime.utcnow()
        result = await self.db.execute(
            select(Account).where(
                Account.status == AccountStatus.active,
                (Account.floodwait_until == None) | (Account.floodwait_until <= now),  # noqa
            )
        )
        return result.scalar_one_or_none()

    async def set_cooldown(self, account_id: str, seconds: int) -> None:
        until = datetime.utcnow() + timedelta(seconds=seconds)
        await self.db.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(status=AccountStatus.cooldown, floodwait_until=until)
        )

    async def clear_cooldown(self, account_id: str) -> None:
        await self.db.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(status=AccountStatus.active, floodwait_until=None)
        )

    async def set_status(self, account_id: str, status: AccountStatus) -> None:
        await self.db.execute(
            update(Account).where(Account.id == account_id).values(status=status)
        )

    async def save_session_string(self, account_id: str, session_string: str) -> None:
        enc = maybe_encrypt_session(session_string)
        await self.db.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(session_string=enc, status=AccountStatus.active)
        )

    async def increment_uploaded(self, account_id: str, count: int = 1) -> None:
        account = await self.get_by_id(account_id)
        if account:
            await self.db.execute(
                update(Account)
                .where(Account.id == account_id)
                .values(total_uploaded=account.total_uploaded + count)
            )

    async def delete(self, account_id: str) -> None:
        account = await self.get_by_id(account_id)
        if account:
            await self.db.delete(account)
