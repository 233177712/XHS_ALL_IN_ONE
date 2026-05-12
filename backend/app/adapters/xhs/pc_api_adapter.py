from __future__ import annotations

import urllib.parse
from typing import Any

from backend.app.adapters.xhs.request_env import direct_xhs_request_env


class XhsPcApiAdapter:
    def __init__(self, cookies: str) -> None:
        self.cookies = cookies

    @staticmethod
    def _parse_user_url(user_url: str) -> tuple[str, str, str]:
        url_parse = urllib.parse.urlparse(user_url)
        user_id = url_parse.path.rstrip("/").split("/")[-1]
        query = urllib.parse.parse_qs(url_parse.query)
        xsec_token = query.get("xsec_token", [""])[0]
        xsec_source = query.get("xsec_source", ["pc_search"])[0] or "pc_search"
        return user_id, xsec_token, xsec_source

    def search_note(
        self,
        keyword: str,
        page: int = 1,
        sort_type_choice: int = 0,
        note_type: int = 0,
        note_time: int = 0,
        note_range: int = 0,
        pos_distance: int = 0,
        geo: str = "",
    ) -> Any:
        with direct_xhs_request_env():
            from apis.xhs_pc_apis import XHS_Apis

            api = XHS_Apis()
            return api.search_note(
                query=keyword,
                page=page,
                cookies_str=self.cookies,
                sort_type_choice=sort_type_choice,
                note_type=note_type,
                note_time=note_time,
                note_range=note_range,
                pos_distance=pos_distance,
                geo=geo,
            )

    def get_note_info(self, url: str) -> Any:
        with direct_xhs_request_env():
            from apis.xhs_pc_apis import XHS_Apis

            api = XHS_Apis()
            return api.get_note_info(url=url, cookies_str=self.cookies)

    def get_note_comments(self, note_url: str) -> Any:
        with direct_xhs_request_env():
            from apis.xhs_pc_apis import XHS_Apis

            api = XHS_Apis()
            return api.get_note_all_comment(url=note_url, cookies_str=self.cookies)

    def get_user_notes(self, user_url: str) -> Any:
        with direct_xhs_request_env():
            from apis.xhs_pc_apis import XHS_Apis

            api = XHS_Apis()
            return api.get_user_all_notes(user_url=user_url, cookies_str=self.cookies)

    def get_user_notes_page(self, user_url: str, cursor: str = "") -> Any:
        with direct_xhs_request_env():
            from apis.xhs_pc_apis import XHS_Apis

            user_id, xsec_token, xsec_source = self._parse_user_url(user_url)

            api = XHS_Apis()
            return api.get_user_note_info(
                user_id=user_id,
                cursor=cursor,
                cookies_str=self.cookies,
                xsec_token=xsec_token,
                xsec_source=xsec_source,
            )

    def get_user_profile(self, user_url: str) -> Any:
        with direct_xhs_request_env():
            from apis.xhs_pc_apis import XHS_Apis

            user_id, _, _ = self._parse_user_url(user_url)
            api = XHS_Apis()
            return api.get_user_info(user_id=user_id, cookies_str=self.cookies)

    def get_self_info(self) -> Any:
        with direct_xhs_request_env():
            from apis.xhs_pc_apis import XHS_Apis

            api = XHS_Apis()
            success, message, payload = api.get_user_self_info(cookies_str=self.cookies)
        if not success or not payload:
            raise RuntimeError(message or "XHS self profile refresh failed")
        return payload
