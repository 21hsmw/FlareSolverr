import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple
from uuid import uuid1

from nodriver import Browser

import utils


@dataclass
class Session:
    session_id: str
    driver: Browser
    created_at: datetime

    def lifetime(self) -> timedelta:
        return datetime.now() - self.created_at


class SessionsStorage:
    """SessionsStorage creates, stores and process all the sessions"""

    def __init__(self):
        self.sessions = {}

    async def create(
        self,
        session_id: Optional[str] = None,
        proxy: Optional[dict] = None,
        force_new: Optional[bool] = False,
    ) -> Tuple[Session, bool]:
        """create new instance of Browser if necessary,
        assign defined (or newly generated) session_id to the instance
        and returns the session object. If a new session has been created
        second argument is set to True.

        Note: The function is idempotent, so if session_id
        already exists in the storage, a new instance of WebDriver won't be created
        and existing session will be returned. Second argument defines if
        new session has been created (True) or an existing one was used (False).
        """
        session_id = session_id or str(uuid1())

        if force_new:
            await self.destroy(session_id)

        if self.exists(session_id):
            return self.sessions[session_id], False

        driver = await utils.get_webdriver_nd(proxy)
        created_at = datetime.now()
        session = Session(session_id, driver, created_at)

        self.sessions[session_id] = session

        return session, True

    def exists(self, session_id: str) -> bool:
        return session_id in self.sessions

    async def destroy(self, session_id: str) -> bool:
        """destroy closes the Browser instance and removes session from the storage.
        The function is noop if session_id doesn't exist.
        The function returns True if session was found and destroyed,
        and False if session_id wasn't found.
        """
        if not self.exists(session_id):
            return False

        session = self.sessions.pop(session_id)
        await utils.after_run_cleanup(driver=session.driver)
        return True

    async def get(
        self, session_id: str, ttl: Optional[timedelta] = None
    ) -> Tuple[Session, bool]:
        session, fresh = await self.create(session_id)

        if ttl is not None and not fresh and session.lifetime() > ttl:
            logging.debug(
                f"session's lifetime has expired, so the session is recreated (session_id={session_id})"
            )
            session, fresh = await self.create(session_id, force_new=True)

        return session, fresh

    def session_ids(self) -> list[str]:
        return list(self.sessions.keys())
