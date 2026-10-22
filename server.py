"""MCP-сервер Qalqan Platform: доступ Claude к правовой базе Оксимы.

Запуск:  .venv\\Scripts\\python.exe server.py
Настройки берутся из .env (см. .env.example).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from oxima import Oxima, OximaError, html_to_text

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

log = logging.getLogger("qalqan")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://mcp.qalqan.org").rstrip("/")
ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "qalqan.org").lower()
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "3847"))
AUTH_DISABLED = os.environ.get("AUTH_DISABLED") == "1"
# Коннектор Qalqan Platform в Claude обращается к https://mcp.qalqan.org/mcp.
MCP_PATH = os.environ.get("MCP_PATH", "/mcp")

# Адреса, куда клиенты MCP возвращаются после входа: веб и десктоп Claude, Claude Code.
CLIENT_REDIRECTS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "http://localhost:*",
    "http://127.0.0.1:*",
]


def build_auth():
    if AUTH_DISABLED:
        if HOST not in ("127.0.0.1", "localhost"):
            raise SystemExit("AUTH_DISABLED=1 разрешён только при HOST=127.0.0.1")
        log.warning("Авторизация ОТКЛЮЧЕНА — только для локальной проверки")
        return None

    from fastmcp.server.auth.providers.google import GoogleProvider

    auth = GoogleProvider(
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        base_url=PUBLIC_URL,
        required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
        jwt_signing_key=os.environ["JWT_SIGNING_KEY"],
        allowed_client_redirect_uris=CLIENT_REDIRECTS,
        extra_authorize_params={"hd": ALLOWED_DOMAIN, "prompt": "select_account consent"},
    )

    # Google пускает любой аккаунт, поэтому домен проверяем сами при каждой проверке токена.
    # Параметр hd выше только подсказывает Google, какой аккаунт предложить.
    verifier = auth._token_validator
    google_verify = verifier.verify_token

    async def verify_domain(token: str):
        access = await google_verify(token)
        if access is None:
            return None
        email = (access.claims.get("email") or "").lower()
        verified = str(access.claims.get("email_verified")).lower() == "true"
        if not verified or not email.endswith("@" + ALLOWED_DOMAIN):
            log.warning("Отказано во входе: %s", email or "<без email>")
            return None
        return access

    verifier.verify_token = verify_domain
    return auth


oxima = Oxima(os.environ["OXIMA_TOKEN"])


@asynccontextmanager
async def lifespan(_server):
    yield
    await oxima.aclose()


mcp = FastMCP(
    name="Qalqan Platform",
    instructions=(
        "Правовая база Республики Казахстан из Оксимы: нормативные правовые акты (НПА), "
        "акты МФЦА, судебные дела с ИИ-аннотациями и ИИ-ассистент Оксимы. "
        "Сначала ищи (search_documents, search_cases), затем открывай найденное по id. "
        "Ссылаясь на нормы, указывай документ, статью и id документа Оксимы."
    ),
    auth=build_auth(),
    lifespan=lifespan,
)

READ_ONLY = {"readOnlyHint": True, "openWorldHint": True}


def _fail(e: OximaError) -> ToolError:
    return ToolError(f"Оксима: {e}")


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# --- Документы ---


@mcp.tool(annotations=READ_ONLY)
async def search_documents(
    query: Annotated[str, Field(description="Название, номер или орган, например «Трудовой кодекс» или «414-V»")],
    dataset: Annotated[
        Literal["adilet", "aifc"],
        Field(description="adilet — НПА Республики Казахстан, aifc — акты МФЦА"),
    ] = "adilet",
    page: Annotated[int, Field(ge=1)] = 1,
) -> dict:
    """Ищет нормативные документы в базе Оксимы. Результаты отсортированы по юридической силе."""
    try:
        data = await oxima.search_documents(query, dataset, page, "FORCE_DESC")
    except OximaError as e:
        raise _fail(e)
    docs = data.get("data", {}).get("documents", [])
    return {
        "total": data["meta"]["total"],
        "page": data["meta"]["current_page"],
        "last_page": data["meta"]["last_page"],
        "documents": [
            {
                "id": d["id"],
                "name": d["name"],
                "requisites": d.get("requisites"),
                "status": (d.get("status") or {}).get("name"),
                "published_at": d.get("published_at"),
            }
            for d in docs
        ],
    }


@mcp.tool(annotations=READ_ONLY)
async def get_document(
    document_id: Annotated[int, Field(description="id документа из search_documents")],
    find: Annotated[
        str | None,
        Field(description="Начать с первого вхождения этого текста, например «Статья 178.»"),
    ] = None,
    offset: Annotated[int, Field(ge=0, description="С какого символа читать (для продолжения)")] = 0,
    max_chars: Annotated[int, Field(ge=1000, le=60000)] = 20000,
    language: Annotated[Literal["ru", "kk"], Field(description="Язык текста")] = "ru",
    date: Annotated[str | None, Field(description="Редакция на дату, ГГГГ-ММ-ДД")] = None,
) -> dict:
    """Возвращает текст документа по частям. Большие кодексы читай через find или offset/next_offset."""
    try:
        doc = await oxima.get_document(document_id, language, date)
    except OximaError as e:
        raise _fail(e)
    text = html_to_text(doc.get("content") or "")
    start = offset
    if find:
        pos = text.find(find, offset)
        if pos < 0:
            pos = text.lower().find(find.lower(), offset)
        if pos < 0:
            raise ToolError(f"«{find}» не найдено в документе {document_id}")
        start = pos
    chunk = text[start : start + max_chars]
    end = start + len(chunk)
    return {
        "id": doc["id"],
        "name": doc["name"],
        "requisites": doc.get("requisites"),
        "status": (doc.get("status") or {}).get("name"),
        "versions": [v.get("published_at") for v in doc.get("versions", [])][:10],
        "total_chars": len(text),
        "offset": start,
        "next_offset": end if end < len(text) else None,
        "text": chunk,
    }


@mcp.tool(annotations=READ_ONLY)
async def get_document_info(document_id: int) -> dict:
    """Реквизиты документа (тип, орган, даты, опубликование) и история его редакций."""
    try:
        info = await oxima.get_document_info(document_id)
        history = await oxima.get_document_history(document_id)
    except OximaError as e:
        raise _fail(e)
    return {"info": info, "history": history}


# --- Судебные дела ---

CaseProperty = Literal[
    "FACTUAL_BACKGROUND", "PLAINTIFF_POSITION", "DEFENDANT_POSITION", "RELIEF_SOUGHT", "LEGAL_QUALIFICATION"
]


@mcp.tool(annotations=READ_ONLY)
async def search_cases(
    query: Annotated[
        str | None, Field(description="Смысловой ИИ-поиск по описанию ситуации, например «взыскание долга по расписке»")
    ] = None,
    search_in: Annotated[
        CaseProperty,
        Field(
            description="Где искать query: FACTUAL_BACKGROUND — фабула, PLAINTIFF_POSITION / DEFENDANT_POSITION — "
            "позиции сторон, RELIEF_SOUGHT — требования, LEGAL_QUALIFICATION — правовая квалификация"
        ),
    ] = "FACTUAL_BACKGROUND",
    number: Annotated[str | None, Field(description="Номер дела, например 1000-26-00-2а/108")] = None,
    case_type: Annotated[
        str | None, Field(description="Вид дела, например CIVIL_CASE (значения — в list_handbook)")
    ] = None,
    instance: Literal["FIRST_INSTANCE", "APPELLATE_INSTANCE", "CASSATION_INSTANCE"] | None = None,
    category_id: Annotated[int | None, Field(description="id категории из list_handbook('case-categories')")] = None,
    authority_id: Annotated[int | None, Field(description="id суда из list_handbook('case-authorities')")] = None,
    result_id: Annotated[int | None, Field(description="id результата из list_handbook('case-results')")] = None,
    plaintiff_id: Annotated[int | None, Field(description="id истца из find_case_party")] = None,
    defendant_id: Annotated[int | None, Field(description="id ответчика из find_case_party")] = None,
    judge_id: Annotated[int | None, Field(description="id судьи из find_judge")] = None,
    date_from: Annotated[str | None, Field(description="Дата начала дела от, ГГГГ-ММ-ДД")] = None,
    date_to: Annotated[str | None, Field(description="Дата начала дела до, ГГГГ-ММ-ДД")] = None,
    page: Annotated[int, Field(ge=1)] = 1,
) -> dict:
    """Ищет судебные дела Казахстана: по смыслу (query) и/или по фильтрам."""
    params = {
        "page": page,
        "number": number,
        "type": case_type,
        "instance": instance,
        "category": category_id,
        "authority": authority_id,
        "result": result_id,
        "plaintiff": plaintiff_id,
        "defendant": defendant_id,
        "judge": judge_id,
    }
    if date_from and date_from == date_to:
        params["started_at"] = date_from
    else:
        params["started_at_from"] = date_from
        params["started_at_until"] = date_to
    if query:
        params.update(search_query=query, search_mode="CONTEXT_PLUS", search_property=search_in)
    try:
        data = await oxima.search_cases(params)
    except OximaError as e:
        raise _fail(e)
    return {
        "total": data["meta"]["total"],
        "page": data["meta"]["current_page"],
        "last_page": data["meta"]["last_page"],
        "cases": [_case_summary(c) for c in data.get("cases", [])],
    }


def _name(obj) -> str | None:
    return obj.get("name") if isinstance(obj, dict) else obj


def _case_summary(c: dict) -> dict:
    return {
        "id": c["id"],
        "number": c.get("number"),
        "type": _name(c.get("type")),
        "instance": _name(c.get("instance")),
        "category": _name(c.get("category")),
        "court": _name(c.get("authority")),
        "result": _name(c.get("result")),
        "plaintiffs": c.get("plaintiffs"),
        "defendants": c.get("defendants"),
        "started_at": c.get("started_at"),
    }


@mcp.tool(annotations=READ_ONLY)
async def get_case(case_id: Annotated[int, Field(description="id дела из search_cases")]) -> dict:
    """Карточка дела: стороны, судьи, ход дела, документы и ИИ-аннотация (фабула, позиции, выводы суда)."""
    try:
        c = await oxima.get_case(case_id)
    except OximaError as e:
        raise _fail(e)
    out = _case_summary(c)
    out.update(
        judges=c.get("judges"),
        history=c.get("history"),
        documents=[
            {k: d.get(k) for k in ("id", "name", "title", "type", "date") if d.get(k) is not None}
            for d in c.get("documents") or []
        ],
        annotations=c.get("annotations"),
    )
    return out


@mcp.tool(annotations=READ_ONLY)
async def find_case_party(
    name: Annotated[str, Field(description="Имя, название или БИН/ИИН стороны")],
    role: Literal["PLAINTIFF", "DEFENDANT"] = "PLAINTIFF",
) -> dict:
    """Находит id истца или ответчика для фильтра в search_cases."""
    try:
        return {"parties": await oxima.search_parties(name, role)}
    except OximaError as e:
        raise _fail(e)


@mcp.tool(annotations=READ_ONLY)
async def find_judge(name: Annotated[str, Field(description="Фамилия судьи")]) -> dict:
    """Находит id судьи для фильтра в search_cases."""
    try:
        return {"judges": await oxima.search_judges(name)}
    except OximaError as e:
        raise _fail(e)


@mcp.tool(annotations=READ_ONLY)
async def list_handbook(
    kind: Literal[
        "document-types",
        "document-groups",
        "document-organizations",
        "case-categories",
        "case-authorities",
        "case-results",
        "legal-acts",
    ],
    search: Annotated[str | None, Field(description="Фильтр по названию (работает для legal-acts)")] = None,
    page: Annotated[int, Field(ge=1)] = 1,
) -> dict:
    """Справочники Оксимы: типы и группы документов, органы, категории дел, суды, результаты, правовые акты."""
    params = {"search": search, "page": page} if kind == "legal-acts" else None
    try:
        return {"items": await oxima.handbook(kind, params)}
    except OximaError as e:
        raise _fail(e)


# --- ИИ-ассистент ---


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
async def ask_oxima_ai(
    question: Annotated[str, Field(description="Юридический вопрос на русском или казахском")],
    chat_id: Annotated[
        str | None, Field(description="chat_id из прошлого ответа, чтобы задать уточняющий вопрос в том же чате")
    ] = None,
) -> dict:
    """Задаёт вопрос ИИ-ассистенту Оксимы. Он отвечает со ссылками на нормы.

    Каждый вызов расходует лимит запросов общего аккаунта Оксимы, поэтому для простого
    поиска документов или дел используй search_documents и search_cases.
    """
    try:
        res = await oxima.ask(question, chat_id)
    except OximaError as e:
        raise _fail(e)
    msg = res["message"]
    if msg.get("status") != "completed":
        raise ToolError(f"ИИ Оксимы не смог ответить (статус {msg.get('status')})")
    sources = []
    for ref in (msg.get("meta") or {}).get("source_refs") or []:
        doc = ref.get("document") or {}
        name = doc.get("name")
        sources.append(
            {
                "ref": ref.get("id"),
                "document_id": doc.get("id"),
                "document": name.get("ru") if isinstance(name, dict) else name,
                "article": ((ref.get("meta") or {}).get("info") or {}).get("article"),
                "text": ref.get("text"),
            }
        )
    return {"chat_id": res["chat_id"], "answer": msg.get("content"), "sources": sources}


if __name__ == "__main__":
    allowed_hosts = [HOST, "localhost", "127.0.0.1", PUBLIC_URL.split("://", 1)[-1]]
    mcp.run(transport="http", host=HOST, port=PORT, path=MCP_PATH, allowed_hosts=allowed_hosts)
