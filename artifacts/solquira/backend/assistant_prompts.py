"""System prompt and context builders for the Solquira agri-assistant."""

from __future__ import annotations

import json

from assistant_schema import DiagnosisReport, FieldContext

SYSTEM_PROMPT = """[ROL Y PROPÓSITO]
Eres Solquira-AI, asistente de orientación ambiental y cuidado territorial. Trabajas únicamente sobre la ubicación que la persona haya indicado o autorizado compartir. No asumas región, país, tipo de suelo, cultivo ni condiciones climáticas cuando no estén acreditados en la evidencia.

[REGLAS DE DOMINIO — OBLIGATORIAS]
1. NUNCA diagnostiques desde la foto aislada: cruza siempre la imagen con la telemetría climática, el sensor y el motor determinista entregados en el JSON de contexto.
2. Si el contexto marca un campo como laguna o sin datos, dilo explícitamente; no rellenes huecos con supuestos.
3. Todo valor del motor (demandas, scores, coberturas) es SINTÉTICO de demostración: no lo presentes como medición ni como viabilidad agronómica.
4. No recomiendes plaguicidas, fertilizantes de síntesis ni dosis: sugiere buenas prácticas agrícolas (BPA), monitoreo, ajustes de riego y bioinsumos caseros de bajo costo.
5. No prometas rendimientos, curas ni reversión de desertificación. Diagnósticos diferenciales, no sentencias.
6. Responde en el idioma solicitado: texto breve, claro, sin jerga y con cautela ante información faltante.

[ENTRADA]
Recibirás un JSON con ontology=solquira.field-observation/1: parcela, clima, foto, sensor, motor_determinista, lagunas, evidencias.

[SALIDA — JSON PURO, SIN MARKDOWN]
Devuelve SOLO un objeto JSON válido con esta forma exacta:
{
  "diagnostico_preliminar": "texto claro que cruza foto + clima + sensor",
  "contexto_parcela": "1-2 frases de contexto territorial y climático",
  "acciones": ["acción inmediata", "observación de suelo o cobertura, si corresponde al caso", "seguimiento"],
  "nota_seguridad": "advertencia amigable y responsable",
  "confianza": "alta | media | baja",
  "lagunas_datos": ["dato faltante que condiciona el diagnóstico"],
  "fuentes": ["ids de evidencia usados, p.ej. open-meteo, sensor-fft, foto-productor"]
}
Usa confianza=baja siempre que falte clima, foto o historial."""


LANGUAGE_DIRECTIVES = {
    "es": "Idioma de la respuesta: español claro y breve; no atribuyas una ubicación no confirmada.",
    "en": "Response language: clear, concise English; do not infer an unconfirmed location.",
}


def build_user_prompt(context: FieldContext, text_query: str) -> str:
    payload = json.loads(context.model_dump_json())
    return (
        "Analiza este caso de campo y responde con el JSON de diagnóstico exigido.\n\n"
        f"Consulta del productor: {text_query}\n\n"
        f"Contexto (JSON):\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_messages(
    context: FieldContext,
    text_query: str,
    image_data_uri: str | None,
    language: str = "es",
) -> list[dict]:
    user_content: object
    text = build_user_prompt(context, text_query)
    if image_data_uri:
        user_content = [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image_data_uri}},
        ]
    else:
        user_content = text
    return [
        {
            "role": "system",
            "content": f"{SYSTEM_PROMPT}\n\n{LANGUAGE_DIRECTIVES.get(language, LANGUAGE_DIRECTIVES['es'])}",
        },
        {"role": "user", "content": user_content},
    ]


def parse_report(
    raw: str,
    *,
    model: str,
    inference_mode: str,
    context: FieldContext,
    disclaimer: str,
    language: str = "es",
) -> DiagnosisReport:
    """Parse the model answer into a report; never trust the model with missing required fields."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    try:
        data = json.loads(text)
        report = DiagnosisReport.model_validate(
            {**data, "inference_mode": inference_mode, "model": model}
        )
    except Exception:  # noqa: BLE001 - model free-text fallback
        report = DiagnosisReport(
            diagnostico_preliminar=raw.strip()[:1200] or "Sin diagnóstico disponible.",
            contexto_parcela=context.clima.note or "Contexto climático no disponible.",
            acciones=[
                "Verifica manualmente la humedad en la base de la raíz.",
                "Registra una foto nueva con mejor luz.",
            ],
            nota_seguridad="Esto es orientación educativa, no asesoría agronómica certificada.",
            confianza="baja",
            lagunas_datos=context.lagunas,
            fuentes=context.evidencias,
            inference_mode=inference_mode,  # type: ignore[arg-type]
            model=model,
        )
    # Honesty clamp (live path): the model cannot claim more confidence than the
    # verified context allows. Fuentes must be traceable evidence ids; system
    # lagunas are always merged so a non-empty model list cannot hide them.
    allowed = set(context.evidencias)
    report.fuentes = [source for source in report.fuentes if source in allowed] or list(
        context.evidencias
    )
    report.lagunas_datos = list(
        dict.fromkeys([*report.lagunas_datos, *context.lagunas])
    )
    if context.clima.status != "ok":
        report.confianza = "baja"
    elif report.lagunas_datos and report.confianza == "alta":
        report.confianza = "media"
    report.language = language  # type: ignore[assignment]
    report.disclaimer = disclaimer
    return report
