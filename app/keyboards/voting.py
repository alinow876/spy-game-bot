"""Inline keyboard for the voting phase."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.domain.game_state import PlayerState
from app.keyboards.callback_data import VoteCallback


def build_voting_keyboard(
    chat_id: int,
    players: list[PlayerState],
    *,
    voting_round: int = 1,
) -> InlineKeyboardMarkup:
    """One button per eligible player to vote for (excludes self-vote in handler)."""
    builder = InlineKeyboardBuilder()
    for player in players:
        label = player.display_name or str(player.user_id)
        builder.button(
            text=label,
            callback_data=VoteCallback(
                chat_id=chat_id,
                target_user_id=player.user_id,
                voting_round=voting_round,
            ),
        )
    builder.adjust(1)
    return builder.as_markup()
