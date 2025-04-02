import os
import io
import mysql.connector
import matplotlib.pyplot as plt
import requests
import traceback
import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify, render_template, Response
from datetime import datetime

app = Flask(__name__)

# Parametri Tesla
CLIENT_ID = "ba139392-c1d5-436e-b8cf-7c64cb52e537"
#CLIENT_SECRET = "ta-secret.EIOXZ12%eEACq43R"
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "default_secret")       
REDIRECT_URI = "https://flask.sersebasti.com/callback"
TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"


# Verifica se la directory esiste, altrimenti la crea
log_directory = "/app/logs"
os.makedirs(log_directory, exist_ok=True)


# Configura il logger
logger = logging.getLogger("TeslaProxy")
logger.setLevel(logging.DEBUG)

# Imposta il gestore per creare un nuovo file ogni giorno
handler = TimedRotatingFileHandler(
    os.path.join(log_directory, "tesla_proxy.log"),
    when="midnight",     # Rotazione ogni mezzanotte
    interval=1,          # Intervallo di rotazione: 1 giorno
    backupCount=30        # Mantieni fino a 7 file di log vecchi
)

# Formato dei log
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)

# Aggiungi il gestore al logger
logger.addHandler(handler)

# Esempio di log
logger.info("Logger configurato con rotazione giornaliera.")


@app.route('/.well-known/appspecific/com.tesla.3p.public-key.pem', methods=['GET'])
def serve_public_key():
    try:
        with open(".well-known/appspecific/com.tesla.3p.public-key.pem", "r") as f:
            key_content = f.read()
        return Response(key_content, mimetype='application/x-pem-file')
    except Exception as e:
        return jsonify({"status": "error", "message": f"Chiave non trovata: {e}"}), 404


@app.route('/callback')
def callback():
    try:
        # Ottieni il codice dalla query string
        code = request.args.get('code')
        state = request.args.get('state')
        logging.info(f"Ricevuto codice: {code}, stato: {state}")

        if not code:
            logging.error("Errore: codice non trovato nella risposta")
            return jsonify({"success": False, "message": "Codice non trovato nella risposta."}), 400

        # Parametri per la richiesta del token
        payload = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid vehicle_device_data vehicle_cmds vehicle_charging_cmds offline_access user_data",
            "audience": "https://fleet-api.prd.eu.vn.cloud.tesla.com"
        }

        logging.info("Invio richiesta POST a Tesla...")
        response = requests.post(TOKEN_URL, data=payload)
        logging.info(f"Risposta ricevuta da Tesla: {response.status_code}")
        logging.debug(f"Risposta Tesla: {response.text}")

        # Prova a estrarre i dati come JSON
        try:
            response_data = response.json()
            logging.info("Risposta JSON correttamente analizzata.")
        except ValueError:
            logging.error("Errore nel parsing JSON.")
            return jsonify({"success": False, "error": "Risposta non in formato JSON", "content": response.text}), 400

        # Controlla lo stato della risposta
        if response.status_code == 200:
            logging.info("Token ottenuto con successo.")
            return jsonify({
                "success": True,
                "access_token": response_data.get("access_token"),
                "refresh_token": response_data.get("refresh_token"),
                "id_token": response_data.get("id_token"),
                "expires_in": response_data.get("expires_in"),
                "token_type": response_data.get("token_type"),
                "state": state
            })
        else:
            logging.error(f"Errore: {response_data.get('error', 'Errore sconosciuto')}")
            return jsonify({
                "success": False,
                "error": response_data.get("error", "Errore sconosciuto"),
                "status_code": response.status_code
            }), 400

    except Exception as e:
        logging.error("Eccezione catturata durante la gestione della richiesta.")
        logging.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
