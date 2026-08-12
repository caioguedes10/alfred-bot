"""
Camada de ingestão do Alfred.

Fluxo: Telegram (via Gemini 3.6, fora deste arquivo) -> POST /logs
       -> validação Pydantic -> INSERT em logs_metricas (Supabase).

Este arquivo cobre SÓ a ingestão. O cron job de agregação (pandas) e o
push do relatório periódico são um módulo separado, consumindo a mesma
tabela.
"""

from __future__ import annotations

import os

from flask import Flask, jsonify, request
from pydantic import ValidationError
from supabase import Client, create_client

from models import LogMetricaCreate

app = Flask(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # service role: só o backend grava
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TABELA = "logs_metricas"


@app.post("/logs")
def criar_log():
    """
    Body esperado (já estruturado pelo Gemini 3.6 a partir do texto livre):
    {
        "data_referencia": "2026-08-12",
        "modulo": "treino",
        "raw_input": "texto original do Telegram",
        "payload": { ... schema do módulo ... }
    }
    """
    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"erro": "JSON inválido ou ausente"}), 400

    try:
        log = LogMetricaCreate.model_validate(body)
    except ValidationError as e:
        # Payload incompleto/mal formado -> 422, nunca chega ao banco.
        # É este retorno que, no orquestrador do bot, deve virar uma
        # mensagem de "faltou X, me manda de novo" pro usuário no Telegram.
        return jsonify({"erro": "payload inválido", "detalhes": e.errors()}), 422

    resultado = (
        supabase.table(TABELA)
        .insert(
            {
                "data_referencia": log.data_referencia.isoformat(),
                "modulo": log.modulo.value,
                "raw_input": log.raw_input,
                "payload": log.payload,
            }
        )
        .execute()
    )

    return jsonify({"status": "ok", "registro": resultado.data}), 201


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    # Em produção: gunicorn/uwsgi. Isto é só para desenvolvimento local.
    app.run(host="0.0.0.0", port=5000, debug=False)
