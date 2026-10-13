"""Headless login (email + password + SMS/email OTP), then list & optionally unlock.

Usage:
    python examples/login_and_unlock.py

First run: enter your myQ Community email + password; when prompted, type the
one-time code texted/emailed to you. The script saves a refresh token to
``~/.myqcommunity_token.json`` and from then on logs in with NO prompts.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os

from myqcommunity import MfaChallenge, MyQCommunity, Token

TOKEN_FILE = os.path.expanduser("~/.myqcommunity_token.json")


def save_token(tok: Token) -> None:
    # Owner-only permissions: the refresh token grants full account access.
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tok.to_dict(), f)


def otp_prompt(challenge: MfaChallenge) -> str:
    where = challenge.masked_phone or challenge.masked_email or "your device"
    return input(f"Enter the {challenge.method.upper()} code sent to {where}: ")


async def connect() -> MyQCommunity:
    # Reuse a stored refresh token when we have one (fully headless).
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            rt = json.load(f).get("refresh_token")
        if rt:
            try:
                return await MyQCommunity.from_refresh_token(rt, on_token_refresh=save_token)
            except Exception as e:  # expired/revoked -> fall through to fresh login
                print(f"(stored token unusable: {e}; logging in fresh)")
    email = input("myQ email: ")
    password = getpass.getpass("myQ password: ")
    return await MyQCommunity.login(email, password, otp_callback=otp_prompt,
                                    on_token_refresh=save_token)


async def main() -> None:
    async with await connect() as myq:
        print("Logged in.\n")
        for comm in await myq.communities():
            print(f"Community: {comm.name}  ({comm.id})")
            locks = await myq.locks(comm.id)
            for i, d in enumerate(locks):
                print(f"  [{i}] {d.name}  {d.serial_number}  online={d.online}")
            if locks:
                choice = input("  Unlock which # (enter to skip)? ").strip()
                if choice.isdigit() and int(choice) < len(locks):
                    d = locks[int(choice)]
                    ok = await myq.unlock(comm.id, d)  # pass the Device so it routes by family
                    print(f"  {'unlocked' if ok else 'failed'}: {d.name}")


if __name__ == "__main__":
    asyncio.run(main())
