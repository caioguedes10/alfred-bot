import os
import json
import requests
import psycopg2
from typing import Any
from flask import Flask, request, jsonify
from google import genai

app = Flask(__name__)

# Configuração de Variáveis de Ambiente
TELEGRAM_TOKEN: str | None = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY")
DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

def salvar_no_banco(categoria: str, dados: dict[str, Any]) -> None:
    if not DATABASE_URL:
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO logs_metricas (categoria, dados) VALUES (%s, %s);",
            (categoria, json.dumps(dados))
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Erro DB: {e}") # Log de erro simplificado

@app.route("/", methods=["GET"])
def home() -> tuple[str, int]:
    return "Alfred Engine Online!", 200

@app.route("/webhook", methods=["POST"])
def telegram_webhook() -> tuple[Any, int]:
    update: dict[str, Any] | None = request.get_json()
    if not update or "message" not in update:
        return jsonify({"status": "ignored"}), 200

    chat_id: int = update["message"]["chat"]["id"]
    text: str = update["message"].get("text", "")

    if not text:
        return jsonify({"status": "no text"}), 200

    prompt: str = f"""
    Você é o Alfred, assistente pessoal e mentor do Caio Guedes.
    O Caio enviou a seguinte mensagem no Telegram: "{text}".
    
    Analise a mensagem (treino, nutrição, estudos ou finanças) e responda de forma ultra-concisa, prática e direta, confirmando os dados gravados.
    """

    try:
        # Migrado para a nova Interactions API usando o Gemini 3.6 Flash
        response = ai_client.interactions.create(
            model="gemini-3.6-flash",
            input=prompt,
        )
        reply_text: str = response.output_text
        salvar_no_banco("geral", {"texto_original": text, "resposta_alfred": reply_text})
    except Exception as e:
        reply_text = f"Erro no processamento: {str(e)}"

    send_url: str = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(send_url, json={"chat_id": chat_id, "text": reply_text})

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
