"""Background timer that auto-deletes a lobby that never got started.

Important: the in-process task is tied to a *specific* lobby generation
(``created_at``). If the process stays up while a lobby is deleted and a
new one is created in the same chat (bot kicked/re-added, or a new
/newgame), an older timer must not delete the new lobby.
"""

from __future__ import annotations

import asyncio
import time

from aiogram import Bot

from app.domain.game_state import GameStatus
from app.repositories.game_state_repository import GameStateRepository
from app.utils.logging import get_logger
from app.utils.telegram_helpers import safe_delete_message, safe_send_message

logger = get_logger(__name__)

# At most one live lobby-timeout task per chat.
_tasks_by_chat: dict[int, asyncio.Task] = {}

NOTICE_TEXT = "⏰ بازی به علت شروع نشدن پس از پنج دقیقه، به‌صورت خودکار حذف شد."
NOTICE_LIFETIME_SECONDS = 60


def start_lobby_timeout(
    bot: Bot,
    repo: GameStateRepository,
    chat_id: int,
    timeout_seconds: int,
    *,
    created_at: float | None = None,
) -> None:
    """Schedule lobby expiry for this chat's current lobby generation.

    Pass ``created_at`` from the lobby just written to Redis. On wake the
    worker only deletes if that same generation is still in LOBBY and old
    enough.
    """
    prev = _tasks_by_chat.pop(chat_id, None)
    if prev is not None and not prev.done():
        prev.cancel()

    task = asyncio.create_task(
        _lobby_timeout_worker(bot, repo, chat_id, timeout_seconds, created_at),
        name=f"lobby-timeout-{chat_id}",
    )
    _tasks_by_chat[chat_id] = task

    def _cleanup(t: asyncio.Task) -> None:
        if _tasks_by_chat.get(chat_id) is t:
            _tasks_by_chat.pop(chat_id, None)

    task.add_done_callback(_cleanup)


async def _lobby_timeout_worker(
    bot: Bot,
    repo: GameStateRepository,
    chat_id: int,
    timeout_seconds: int,
    expected_created_at: float | None,
) -> None:
    try:
        await asyncio.sleep(timeout_seconds)

        game = await repo.get_game(chat_id)
        if game is None or game.status != GameStatus.LOBBY:
            return

        # Stale timer from a previous lobby in the same chat.
        if expected_created_at is not None:
            if abs(game.created_at - expected_created_at) > 0.5:
                logger.info(
                    "lobby_timeout_skipped_stale_generation",
                    chat_id=chat_id,
                    expected_created_at=expected_created_at,
                    actual_created_at=game.created_at,
                )
                return

        # Must really be past the business deadline (same rule as recovery).
        if time.time() < game.created_at + timeout_seconds - 1.0:
            logger.info(
                "lobby_timeout_skipped_not_due",
                chat_id=chat_id,
                age=time.time() - game.created_at,
                timeout_seconds=timeout_seconds,
            )
            return

        if game.lobby_message_id is not None:
            await safe_delete_message(bot, chat_id, game.lobby_message_id)

        await repo.force_delete_game(chat_id)
        logger.info("lobby_auto_deleted", chat_id=chat_id)

        notice = await safe_send_message(bot, chat_id, NOTICE_TEXT)
        if notice is None:
            return
        await asyncio.sleep(NOTICE_LIFETIME_SECONDS)
        await safe_delete_message(bot, chat_id, notice.message_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("lobby_timeout_task_failed", chat_id=chat_id)
