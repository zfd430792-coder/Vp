"""Фабрики callback-данных. Значения не должны содержать «:» — это разделитель."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class RegCB(CallbackData, prefix="reg"):
    action: str            # begin | rules_ok | safety | gender | seeking | interest | done | skip
    value: str = ""


class CaptchaCB(CallbackData, prefix="cap"):
    value: str
    nonce: int = 0


class FeedCB(CallbackData, prefix="fd"):
    action: str            # like | super | pass | report | more
    target: int = 0


class LikesCB(CallbackData, prefix="lk"):
    action: str            # like | pass | report | skip | open
    target: int = 0


class MatchCB(CallbackData, prefix="mt"):
    action: str            # list | open | close | report | block | exit
    match_id: int = 0
    page: int = 0


class ProfileCB(CallbackData, prefix="pf"):
    action: str            # menu | name | age | gender | seeking | city | bio |
                           # photos | photo_add | photo_del | pause | resume | delete | confirm_delete
    value: str = ""


class ReportCB(CallbackData, prefix="rp"):
    action: str            # cat | send | skip | cancel | block
    target: int = 0
    value: str = ""        # код категории


class SettingsCB(CallbackData, prefix="st"):
    action: str            # menu | filters | seeking | age | city | notify | privacy | appeal
    value: str = ""


class AdminCB(CallbackData, prefix="ad"):
    action: str
    target: int = 0
    page: int = 0
    value: str = ""
