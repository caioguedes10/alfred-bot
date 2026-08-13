"""
Alfred — assistente pessoal via Telegram (Flask + APScheduler + Supabase).
LLM multimodal via OpenRouter (Qwen-VL), substituindo a Groq (texto-only).
"""

import logging
import os
from typing import Any, Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request
from openai import OpenAI
from supabase import Client, create_client
from todoist_api_python.api import TodoistAPI

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
app = Flask(__name__)

# --- Variáveis de ambiente ---------------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
TODOIST_API_KEY = os.getenv("TODOIST_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# --- Constantes -----------------------------------------------------------
TELEGRAM_API_BASE = "https://api.telegram.org"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
VISION_MODEL = "google/gemini-1.5-flash:free"

# --- Clients (setup único no bootstrap) ------------------------------------
llm_client: Optional[OpenAI] = (
    OpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)
    if OPENROUTER_API_KEY
    else None
)

system_instruction = (
    "Você é Alfred, o assistente pessoal do Caio. O Caio é estudante de "
    "Ciências Econômicas na UNICAMP e atua com Operações e Processos Fiscais na WWT. "
    "Seu papel é ser um mentor técnico e ajudar a monitorar a rotina de estudos, "
    "trabalho, nutrição, finanças e treinos físicos dele. Seja direto, prático, "
    "use tom sênior e levemente bem-humorado. Nunca faça introduções longas. "
    "Responda formatado para leitura rápida em tela de celular."
)

todoist_api = TodoistAPI(TODOIST_API_KEY) if TODOIST_API_KEY else None
supabase: Optional[Client] = (
    create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
)


# --- Telegram helpers -------------------------------------------------------
def send_telegram_msg(text: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"{TELEGRAM_API_BASE}/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(
        url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10
    )


def get_telegram_file_url(file_id: str) -> Optional[str]:
    """Resolve file_id -> file_path (getFile) -> URL de download direta."""
    try:
        resp = requests.get(
            f"{TELEGRAM_API_BASE}/bot{TELEGRAM_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=10,
        )
        resp.raise_for_status()
        file_path: str = resp.json()["result"]["file_path"]
        return f"{TELEGRAM_API_BASE}/file/bot{TELEGRAM_TOKEN}/{file_path}"
    except (requests.RequestException, KeyError) as exc:
        logging.error(f"Erro ao resolver file_path do Telegram: {exc}")
        return None


def extract_photo_file_id(msg_data: dict[str, Any]) -> Optional[str]:
    """Extrai o file_id da imagem de maior resolução (último item do array)."""
    photos = msg_data.get("photo")
    if not photos:
        return None
    return photos[-1]["file_id"]


# --- LLM (OpenRouter / Qwen-VL) --------------------------------------------
def build_user_content(text: str, image_url: Optional[str]) -> Any:
    """Monta o content da mensagem do usuário: string simples (texto puro)
    ou array multimodal (texto + image_url), conforme presença de imagem."""
    if not image_url:
        return text
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]


def query_llm(text: str, image_url: Optional[str] = None) -> str:
    if not llm_client:
        return "API do OpenRouter ausente."
    try:
        completion = llm_client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": build_user_content(text, image_url)},
            ],
            temperature=0.7,
        )
        return completion.choices[0].message.content or "Sem resposta do modelo."
    except Exception as e:  # SDK da OpenAI levanta subclasses variadas
        logging.error(f"Erro OpenRouter detalhado: {e}")
        return f"Erro técnico: {str(e)}"


# --- Scheduler (Todoist) ---------------------------------------------------
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


scheduler = BackgroundScheduler()
scheduler.add_job(job_cobranca, "cron", hour=11, minute=0)
scheduler.start()


# --- Rotas Flask ------------------------------------------------------------
@app.route("/", methods=["GET"])
def home() -> str:
    return "Alfred is online."


@app.route("/webhook", methods=["POST"])
def webhook() -> tuple[dict[str, Any], int]:
    payload = request.json
    if not payload or "message" not in payload:
        return jsonify({"status": "ignored"}), 200

    msg_data = payload.get("message", {})
    chat_id_in = str(msg_data.get("chat", {}).get("id", ""))

    if chat_id_in != CHAT_ID:
        return jsonify({"status": "unauthorized"}), 403

    # Texto vem de message.text (texto puro) ou message.caption (foto + legenda)
    text = (msg_data.get("text") or msg_data.get("caption") or "").strip()

    image_url: Optional[str] = None
    if file_id := extract_photo_file_id(msg_data):
        image_url = get_telegram_file_url(file_id)
        if image_url is None:
            send_telegram_msg("Não consegui acessar a imagem enviada.")
            return jsonify({"status": "ok"}), 200

    if not text and not image_url:
        return jsonify({"status": "ok"}), 200

    resposta_ia = query_llm(text or "Descreva a imagem.", image_url)
    send_telegram_msg(resposta_ia)
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
