"""El recordatorio de la cita que manda el CRM raíz (Ajustes → Agenda).

Vocero raíz le escribe al lead N minutos antes de cada cita, solo. Nea no
manda nada, pero tiene que saberlo: con el «aquí no hay recordatorios» de
siempre, el agente le negaba al lead algo que sí le iba a llegar.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from app.profile import BusinessProfile, ProfileProvider, profile_from_payload
from app.prompt import _minutos_es, build_system_prompt
from app.state import Conversation
from tests.conftest import CRM_URL, despachar, mock_crm_basics

AHORA = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)
SIN_RECORDATORIOS = "Este negocio NO manda recordatorios por aquí"
CON_RECORDATORIO = "recordatorio por WhatsApp 30 minutos antes de cada cita"


def _payload(reminder) -> dict:
    return {
        "profile": {"name": "José", "instructions": "Vendo departamentos en Trujillo."},
        "kb": "",
        "resources": [],
        "llm": None,
        "welcomeMedia": None,
        "appointmentReminder": reminder,
    }


def _prompt(profile: BusinessProfile, **kw) -> str:
    return build_system_prompt(
        profile=profile, context=None, conv=Conversation(id=1, wa_identity="x"), now=AHORA, **kw
    )


def test_el_perfil_lee_los_minutos():
    assert profile_from_payload(_payload({"minutesBefore": 30}), "Nea").reminder_minutes == 30


def test_sin_bloque_apagado_o_con_basura_no_hay_recordatorio():
    assert profile_from_payload(_payload(None), "Nea").reminder_minutes is None
    sin_bloque = {k: v for k, v in _payload(None).items() if k != "appointmentReminder"}
    assert profile_from_payload(sin_bloque, "Nea").reminder_minutes is None
    for basura in ({"minutesBefore": 0}, {"minutesBefore": "30"}, {"minutesBefore": True}, 30):
        assert profile_from_payload(_payload(basura), "Nea").reminder_minutes is None


def test_con_recordatorio_el_prompt_lo_dice_y_ya_no_lo_niega():
    prompt = _prompt(BusinessProfile(reminder_minutes=30), agenda=True)
    assert CON_RECORDATORIO in prompt
    assert SIN_RECORDATORIOS not in prompt


def test_sin_recordatorio_sigue_diciendo_que_no_hay():
    prompt = _prompt(BusinessProfile(), agenda=True)
    assert SIN_RECORDATORIOS in prompt
    assert CON_RECORDATORIO not in prompt


def test_sin_agenda_no_se_habla_de_recordatorios():
    prompt = _prompt(BusinessProfile(reminder_minutes=30), agenda=False)
    assert CON_RECORDATORIO not in prompt and SIN_RECORDATORIOS not in prompt


def test_los_minutos_en_palabras():
    assert _minutos_es(30) == "30 minutos"
    assert _minutos_es(1) == "1 minuto"
    assert _minutos_es(60) == "1 hora"
    assert _minutos_es(90) == "1 hora y 30 minutos"
    assert _minutos_es(1440) == "24 horas"


async def test_el_turno_le_cuenta_al_modelo_el_recordatorio_del_crm(ctx, client, respx_mock):
    ctx.profile = ProfileProvider(ctx.crm)
    mock_crm_basics(respx_mock)
    respx_mock.get(f"{CRM_URL}/api/bot/profile").mock(
        return_value=httpx.Response(200, json=_payload({"minutesBefore": 30}))
    )
    await despachar(client, ctx, text="quiero agendar una llamada")
    await asyncio.sleep(0.25)
    system = ctx.llm.calls[0]["messages"][0]["content"]
    assert CON_RECORDATORIO in system
    assert SIN_RECORDATORIOS not in system
