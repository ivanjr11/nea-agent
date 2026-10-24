"""Video de bienvenida: una sola vez por contacto, justo después del saludo.

El CRM guarda el video (pantalla Agente) y lo anuncia en el perfil
(`welcomeMedia`); Nea decide cuándo pedirlo: en su primera respuesta a cada
contacto, nunca después, y nunca si el turno termina en manos de una persona.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from app.llm import LlmReply, ToolCall
from app.profile import ProfileProvider, profile_from_payload
from tests.conftest import CRM_URL, despachar, mock_crm_basics

CAPTION = "¿Cuentas con un presupuesto de S/ 88,000?"


def _perfil(welcome: dict | None) -> dict:
    return {
        "profile": {"name": "José", "instructions": "Vendo departamentos en Trujillo."},
        "kb": "",
        "resources": [],
        "llm": None,
        "welcomeMedia": welcome,
    }


def _rutas(ctx, respx_mock, welcome: dict | None = None) -> dict:
    ctx.profile = ProfileProvider(ctx.crm)
    routes = mock_crm_basics(respx_mock)
    routes["profile"] = respx_mock.get(f"{CRM_URL}/api/bot/profile").mock(
        return_value=httpx.Response(200, json=_perfil(welcome))
    )
    routes["bienvenida"] = respx_mock.post(f"{CRM_URL}/api/bot/messages/bienvenida").mock(
        return_value=httpx.Response(200, json={"messageId": "msg_video"})
    )
    return routes


def test_el_perfil_lee_el_video_y_su_texto():
    p = profile_from_payload(_perfil({"kind": "video", "caption": f" {CAPTION} "}), "Nea")
    assert p.welcome_media_kind == "video"
    assert p.welcome_media_caption == CAPTION


def test_sin_bloque_o_con_basura_no_hay_video():
    assert profile_from_payload(_perfil(None), "Nea").welcome_media_kind is None
    sin_bloque = {k: v for k, v in _perfil(None).items() if k != "welcomeMedia"}
    assert profile_from_payload(sin_bloque, "Nea").welcome_media_kind is None
    assert profile_from_payload(_perfil({"kind": 3}), "Nea").welcome_media_kind is None


async def test_primer_turno_saludo_y_despues_el_video(ctx, client, respx_mock):
    routes = _rutas(ctx, respx_mock, {"kind": "video", "caption": CAPTION})
    ctx.llm.replies = [LlmReply(content="¡Hola! Soy José 👋 Te comparto un video corto donde te explico todo.")]

    await despachar(client, ctx, text="hola")
    await asyncio.sleep(0.25)

    assert routes["messages"].call_count == 1
    assert routes["bienvenida"].call_count == 1
    llamadas = [
        str(c.request.url.path) for c in respx_mock.calls if "/api/bot/messages" in str(c.request.url)
    ]
    assert llamadas == ["/api/bot/messages", "/api/bot/messages/bienvenida"]
    # El prompt del primer turno sabe que el video sale solo, y con qué texto.
    system = ctx.llm.calls[0]["messages"][0]["content"]
    assert "video" in system and CAPTION in system
    # Y el historial guarda lo que el lead vio: el texto del video.
    conv = next(iter(ctx.store.conversations.values()))
    del_agente = [m.content for m in ctx.store.messages if m.role == "assistant"]
    assert del_agente[-1] == CAPTION
    assert conv.greeted is True


async def test_el_segundo_turno_ya_no_manda_el_video(ctx, client, respx_mock):
    routes = _rutas(ctx, respx_mock, {"kind": "video", "caption": CAPTION})
    ctx.llm.replies = [
        LlmReply(content="¡Hola! Te comparto un video corto."),
        LlmReply(content="Antes de seguir, ¿cuentas con los S/ 88,000?"),
    ]
    await despachar(client, ctx, text="hola")
    await asyncio.sleep(0.25)
    await despachar(client, ctx, text="¿dónde quedan?", wamid="wamid.2")
    await asyncio.sleep(0.25)

    assert routes["messages"].call_count == 2
    assert routes["bienvenida"].call_count == 1


async def test_sin_video_en_el_perfil_no_se_pide(ctx, client, respx_mock):
    routes = _rutas(ctx, respx_mock, None)
    await despachar(client, ctx, text="hola")
    await asyncio.sleep(0.25)
    assert routes["messages"].call_count == 1
    assert routes["bienvenida"].call_count == 0


async def test_si_el_primer_turno_termina_en_una_persona_no_hay_video(ctx, client, respx_mock):
    routes = _rutas(ctx, respx_mock, {"kind": "video", "caption": CAPTION})
    ctx.llm.replies = [
        LlmReply(
            content=None,
            tool_calls=[ToolCall(id="tc1", name="handoff", arguments={"reason": "pidió humano"})],
        ),
        LlmReply(content="Va, te paso con José ahora mismo."),
    ]
    await despachar(client, ctx, text="quiero hablar con una persona")
    await asyncio.sleep(0.25)
    assert routes["handoff"].call_count == 1
    assert routes["bienvenida"].call_count == 0


async def test_si_el_crm_falla_el_saludo_ya_salio_y_no_se_reintenta_despues(
    ctx, client, respx_mock
):
    routes = _rutas(ctx, respx_mock, {"kind": "video", "caption": CAPTION})
    routes["bienvenida"].mock(return_value=httpx.Response(502, json={}))
    ctx.llm.replies = [
        LlmReply(content="¡Hola! Te comparto un video corto."),
        LlmReply(content="¿En qué zona buscas?"),
    ]
    await despachar(client, ctx, text="hola")
    await asyncio.sleep(1.5)
    assert routes["messages"].call_count == 1
    assert routes["bienvenida"].call_count == 2  # un reintento en el mismo turno

    await despachar(client, ctx, text="ok", wamid="wamid.2")
    await asyncio.sleep(0.25)
    assert routes["bienvenida"].call_count == 2
    del_agente = [m.content for m in ctx.store.messages if m.role == "assistant"]
    assert CAPTION not in del_agente
    body = json.loads(routes["bienvenida"].calls[0].request.content)
    assert body == {"conversationId": body["conversationId"]} and body["conversationId"]
