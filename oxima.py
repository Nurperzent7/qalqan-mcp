"""Клиент API Оксимы (https://api.oxima.kz/v1).

Публичной документации у API нет: эндпоинты и параметры восстановлены
по веб-приложению app.oxima.kz.
"""

from __future__ import annotations

import asyncio
import html
import re
from html.parser import HTMLParser
from typing import Any

import httpx

BASE_URL = "https://api.oxima.kz/v1"

# Статусы сообщения ИИ, пока ответ ещё генерируется.
_IN_PROGRESS = {"pending", "processing", "streaming", "in_progress"}


class OximaError(Exception):
    """Ошибка, которую вернул API Оксимы."""


class Oxima:
    def __init__(self, token: str, timeout: float = 60.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Accept-Language": "ru",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        resp = await self._client.request(method, path, **kwargs)
        try:
            data = resp.json()
        except ValueError:
            raise OximaError(f"Оксима вернула HTTP {resp.status_code} без JSON")
        if not data.get("success", resp.is_success):
            details = data.get("messages") or ""
            raise OximaError(f"{data.get('message') or resp.status_code} {details}".strip())
        return data

    # --- Документы (НПА) ---

    async def search_documents(self, query: str, dataset: str, page: int, sort_mode: str) -> dict:
        body = {"datasets": [dataset], "page": page, "search_query": query, "sort_mode": sort_mode}
        return await self._request("POST", "/documents/search", json=body)

    async def get_document(self, doc_id: int, language: str, date: str | None) -> dict:
        params = {"date": date} if date else None
        data = await self._request(
            "GET", f"/documents/{doc_id}", params=params, headers={"Accept-Language": language}
        )
        return data["data"]["document"]

    async def get_document_info(self, doc_id: int) -> dict:
        return (await self._request("GET", f"/documents/{doc_id}/info"))["data"]["info"]

    async def get_document_history(self, doc_id: int) -> list:
        return (await self._request("GET", f"/documents/{doc_id}/history"))["data"]["history"]

    # --- Судебные дела ---

    async def search_cases(self, params: dict) -> dict:
        return await self._request("GET", "/cases", params={k: v for k, v in params.items() if v not in (None, "", [])})

    async def get_case(self, case_id: int) -> dict:
        data = await self._request("GET", f"/cases/{case_id}")
        return data.get("data", {}).get("case") or data.get("case") or data

    async def search_parties(self, name: str, role: str) -> list:
        return (await self._request("GET", "/cases/parties", params={"search_query": name, "type": role}))["parties"]

    async def search_judges(self, name: str) -> list:
        return (await self._request("GET", "/cases/judges", params={"search_query": name}))["judges"]

    # --- Справочники ---

    async def handbook(self, path: str, params: dict | None = None) -> Any:
        return (await self._request("GET", f"/handbooks/{path}", params=params))["data"]

    # --- ИИ-ассистент ---

    async def ask(self, question: str, chat_id: str | None, timeout: float = 240.0) -> dict:
        """Задаёт вопрос ИИ Оксимы и ждёт, пока ответ сгенерируется."""
        if chat_id:
            data = await self._request("POST", f"/chats/{chat_id}/messages", json={"message": question})
            answer_id = data["data"]["assistant_message"]["id"]
        else:
            data = await self._request("POST", "/chats", json={"type": "general", "message": question})
            chat_id = data["data"]["chat"]["id"]
            answer_id = data["data"]["chat"]["messages"][-1]["id"]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            chat = (await self._request("GET", f"/chats/{chat_id}"))["data"]["chat"]
            answer = next((m for m in chat["messages"] if m.get("id") == answer_id), {})
            if answer and answer.get("status") not in _IN_PROGRESS:
                return {"chat_id": chat_id, "title": chat.get("title"), "message": answer}
            if loop.time() > deadline:
                raise OximaError(f"ИИ Оксимы не ответил за {int(timeout)} с (chat_id={chat_id})")
            await asyncio.sleep(2)


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(raw: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw)
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
