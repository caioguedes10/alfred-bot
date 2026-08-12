import os
import logging
from typing import Any, Dict, Tuple

import requests
from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from todoist_api_python.api import TodoistAPI
from supabase import create_client, Client

# --- SETUP E LOGS ---
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
app = Flask(__name__)

# --- VARIÁVEIS DE AMBIENTE ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# --- CLIENTES ---
todoist_api = TodoistAPI(TODOIST_API_KEY) if TODOIST_API_KEY else None

# Previne crash no Render caso o Supabase ainda não esteja configurado
if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client | None = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None

def send_telegram_msg(text: str) -> None:
    """Envia mensagem ativa para o seu chat no Telegram."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logging.error("Faltam variáveis do Telegram.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except requests.RequestException as e:
        logging.error(f"Erro Request Telegram: {e}")

def job_cobranca() -> None:
    """Job agendado: consulta tarefas pendentes de hoje e cobra via Telegram."""
    if not todoist_api:
        logging.warning("API do Todoist ausente. Pulando job de cobrança.")
        return

    try:
        tasks = todoist_api.get_tasks(filter="today")
        if not tasks:
            return

        msg = "🚨 *Cobrança Alfred - Tarefas Pendentes:*\n\n"
        for task in tasks:
            msg += f"• {task.content}\n"

        send_telegram_msg(msg)
    except Exception as e:
        logging.error(f"Erro ao consultar Todoist: {e}")

# --- SCHEDULER ---
scheduler = BackgroundScheduler()
# CRÍTICO: Servidores do Render operam em UTC. 
# Para disparar às 8h no horário de Brasília (UTC-3), o job deve ser agendado para as 11h.
scheduler.add_job(job_cobranca, "cron", hour=11, minute=0)
scheduler.start()

# --- ROTAS (WEBHOOKS) ---
@app.route("/", methods=["GET"])
def home() -> str:
    return "Alfred is online."

@app.route("/webhook", methods=["POST"])
def webhook() -> Tuple[Dict[str, Any], int]:
    """Endpoint que recebe mensagens e comandos do Telegram."""
    payload = request.json
    if not payload or "message" not in payload:
        return jsonify({"status": "ignored"}), 200

    msg_data = payload.get("message", {})
    chat_id_in = str(msg_data.get("chat", {}).get("id", ""))
    text = msg_data.get("text", "").strip()

    # Bloqueia processamento de qualquer outro usuário
    if chat_id_in != CHAT_ID:
        logging.warning(f"Acesso bloqueado do chat_id: {chat_id_in}")
        return jsonify({"status": "unauthorized"}), 403

    # Lógica de roteamento de comandos
    text_lower = text.lower()
    if text_lower.startswith("/start") or text_lower == "boa tarde alfre":
        resposta = (
            "Boa tarde, Sr. Caio. À disposição.\n\n"
            "Nenhum dado enviado para registro ainda. Qual o foco agora: "
            "treino, nutrição, estudos ou finanças?"
        )
        send_telegram_msg(resposta)
    else:
        # Espaço reservado para os próximos módulos do sistema (WWT, Finanças, etc)
        pass

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
