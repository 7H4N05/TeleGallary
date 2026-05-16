"""
Accounts API endpoints — manage Telegram accounts.
"""

from fastapi import APIRouter, HTTPException
from pyrogram import Client
from pyrogram.errors import (
    PhoneCodeExpired,
    PhoneCodeInvalid,
    SessionPasswordNeeded,
)

from db.database import AsyncSessionLocal
from db.models import AccountStatus
from db.repositories.account_repo import AccountRepository
from models.schemas import AccountCreate, AccountLoginCode, AccountOut
from telegram.client_manager import get_client_manager
from utils.logger import get_logger
from utils.session_crypto import maybe_decrypt_session

router = APIRouter()
logger = get_logger(__name__)

# Temporary store for login state between /send-code and /verify
_pending_logins: dict = {}


@router.get("/", response_model=list[AccountOut])
async def list_accounts():
    async with AsyncSessionLocal() as db:
        repo = AccountRepository(db)
        accounts = await repo.list_all()
        return [AccountOut.model_validate(a) for a in accounts]


@router.post("/send-code")
async def send_code(req: AccountCreate):
    """Step 1 of login: send code to the phone number."""
    client_mgr = get_client_manager()
    client = Client(
        name=f"temp_{req.phone}",
        api_id=req.api_id,
        api_hash=req.api_hash,
        in_memory=True,
    )
    try:
        await client.connect()
        sent = await client.send_code(req.phone)
        _pending_logins[req.phone] = {
            "client": client,
            "phone_code_hash": sent.phone_code_hash,
            "api_id": req.api_id,
            "api_hash": req.api_hash,
            "display_name": req.display_name,
        }
        return {"phone_code_hash": sent.phone_code_hash, "message": "Code sent"}
    except Exception as e:
        await client.disconnect()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/verify", response_model=AccountOut)
async def verify_code(req: AccountLoginCode):
    """Step 2 of login: verify the code and save the session."""
    pending = _pending_logins.get(req.phone)
    if not pending:
        raise HTTPException(status_code=400, detail="No pending login for this phone")

    client: Client = pending["client"]
    try:
        await client.sign_in(
            phone_number=req.phone,
            phone_code_hash=req.phone_code_hash,
            phone_code=req.code,
        )
    except SessionPasswordNeeded:
        if not req.password:
            raise HTTPException(
                status_code=400,
                detail="Two-step verification required. Provide password.",
            )
        await client.check_password(req.password)
    except (PhoneCodeInvalid, PhoneCodeExpired) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    session_string = await client.export_session_string()
    await client.disconnect()
    _pending_logins.pop(req.phone, None)

    async with AsyncSessionLocal() as db:
        repo = AccountRepository(db)
        existing = await repo.get_by_phone(req.phone)
        if existing:
            await repo.save_session_string(existing.id, session_string)
            await db.commit()
            account = await repo.get_by_phone(req.phone)
        else:
            account = await repo.create(
                {
                    "phone": req.phone,
                    "display_name": pending.get("display_name") or req.phone,
                    "session_string": session_string,
                    "api_id": pending["api_id"],
                    "api_hash": pending["api_hash"],
                    "status": AccountStatus.active,
                }
            )
            await db.commit()

        # Register in client manager
        client_mgr = get_client_manager()
        await client_mgr.add_client(
            account_id=account.id,
            phone=account.phone,
            api_id=account.api_id,
            api_hash=account.api_hash,
            session_string=session_string,
        )
        return AccountOut.model_validate(account)


@router.delete("/{account_id}")
async def delete_account(account_id: str):
    async with AsyncSessionLocal() as db:
        repo = AccountRepository(db)
        account = await repo.get_by_id(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        client_mgr = get_client_manager()
        await client_mgr.remove_client(account_id)
        await repo.delete(account_id)
        await db.commit()
    return {"deleted": True}


@router.post("/{account_id}/connect")
async def connect_account(account_id: str):
    """Re-connect a previously saved account."""
    async with AsyncSessionLocal() as db:
        repo = AccountRepository(db)
        account = await repo.get_by_id(account_id)
        if not account or not account.session_string:
            raise HTTPException(status_code=400, detail="Account not found or no session")

    client_mgr = get_client_manager()
    await client_mgr.add_client(
        account_id=account.id,
        phone=account.phone,
        api_id=account.api_id,
        api_hash=account.api_hash,
        session_string=maybe_decrypt_session(account.session_string),
    )
    started = await client_mgr.start_client(account_id)
    return {"connected": started}
