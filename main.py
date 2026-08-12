import os
import logging
from typing import Any, Dict, Tuple

import requests
from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from todoist_api_python.api import TodoistAPI
from supabase import create_client, Client
import google.generativeai as genai

# --- SETUP E LOGS ---
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
app = Flask(__name__)

# --- VARIÁVEIS DE AMBIENTE ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# --- CÉREBRO: CONFIGURAÇÃO GEMINI ---
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    system_instruction = (
        "Você é Alfred, o assistente pessoal do Caio. O Caio é estudante de "
        "Ciências Econômicas na UNICAMP e atua com Operações e Processos Fiscais na WWT. "
        "Seu papel é ser um mentor técnico e ajudar a monitorar a rotina de estudos, "
        "trabalho, nutrição, finanças e treinos físicos dele. Seja direto, prático, "
        "use tom sênior e levemente bem-humorado. Nunca faça introduções longas. "
        "Responda formatado para leitura rápida em tela de celular."
    )
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=system_instruction
    )
else:
    model = None

# --- CLIENTES ---
todoist_api = TodoistAPI(TODOIST_API_KEY) if TODOIST_API_KEY else None
supabase: Client | None = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

def send_telegram_msg(text: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)

def job_cobranca() -> None:
    if not todoist_api:
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
        logging.error(f"Erro Todoist: {e}")

# --- SCHEDULER ---
scheduler = BackgroundScheduler()
scheduler.add_job(job_cobranca, "cron", hour=11, minute=0)
scheduler.start()

# --- ROTAS (WEBHOOKS) ---
@app.route("/", methods=["GET"])
def home() -> str:
    return "Alfred is online."

@app.route("/webhook", methods=["POST"])
def webhook() -> Tuple[Dict[str, Any], int]:
    payload = request.json
    if not payload or "message" not in payload:
        return jsonify({"status": "ignored"}), 200

    msg_data = payload.get("message", {})
    chat_id_in = str(msg_data.get("chat", {}).get("id", ""))
    text = msg_data.get("text", "").strip()

    if chat_id_in != CHAT_ID:
        return jsonify({"status": "unauthorized"}), 403

    if not text:
        return jsonify({"status": "ok"}), 200

    if model:
        try:
            resposta_ia = model.generate_content(text).text
        except Exception as e:
            logging.error(f"Erro Gemini: {e}")
            resposta_ia = "Falha de conexão com os servidores neurais, senhor."
    else:
        resposta_ia = "API do Gemini ausente."

    send_telegram_msg(resposta_ia)
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
