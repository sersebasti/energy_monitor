import os
import io
import json
import requests
import traceback
import logging
import asyncio
import asyncssh # type: ignore
import mysql.connector # type: ignore

from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify, Response
from datetime import datetime
from mysql.connector import Error # type: ignore


app = Flask(__name__)

# Parametri Tesla
CLIENT_ID = "ba139392-c1d5-436e-b8cf-7c64cb52e537"
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "default_secret")       
REDIRECT_URI = "https://flask.sersebasti.com/callback"
TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"

VIN = "LRW3E7FA9MC345603"
    
# Verifica se la directory esiste, altrimenti la crea
log_directory = "/app/logs"
os.makedirs(log_directory, exist_ok=True)

# Directory per i dati
data_directory = "/app/data"
os.makedirs(data_directory, exist_ok=True)

# Configura il logger
logger = logging.getLogger("TeslaProxy")
logger.setLevel(logging.DEBUG)

# Imposta il gestore per creare un nuovo file ogni giorno
handler = TimedRotatingFileHandler(
    os.path.join(log_directory, "tesla_proxy.log"),
    when="midnight",
    interval=1,
    backupCount=30
)

# Formato dei log
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)

# Aggiungi il gestore al logger
logger.addHandler(handler)

logger.info("Logger configurato con rotazione giornaliera.")

def save_to_file(data, filename):
    """Salva i dati in un file JSON."""
    try:
        file_path = os.path.join(data_directory, filename)
        with open(file_path, 'w') as outfile:
            json.dump(data, outfile, indent=4)
        logger.info(f"Dati salvati correttamente in {file_path}")
        
        cleanup_old_files(data_directory, max_files=3)
        
        
    except Exception as e:
        logger.error(f"Errore nel salvataggio dei dati: {e}")

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
        logger.info(f"Ricevuto codice: {code}, stato: {state}")

        if not code:
            logger.error("Errore: codice non trovato nella risposta")
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

        logger.info("Invio richiesta POST a Tesla...")
        response = requests.post(TOKEN_URL, data=payload)
        logger.info(f"Risposta ricevuta da Tesla: {response.status_code}")
        logger.debug(f"Risposta Tesla: {response.text}")

        # Prova a estrarre i dati come JSON
        try:
            response_data = response.json()
            logger.info("Risposta JSON correttamente analizzata.")
        except ValueError:
            logger.error("Errore nel parsing JSON.")
            return jsonify({"success": False, "error": "Risposta non in formato JSON", "content": response.text}), 400

        # Controlla lo stato della risposta
        if response.status_code == 200:
            logger.info("Token ottenuto con successo.")

            # Salva i dati nel file JSON
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"tesla_token_{timestamp}.json"
            save_to_file(response_data, filename)
            
            # Salva anche con nome fisso per accesso rapido
            save_to_file(response_data, "tesla_token_latest.json")

            return jsonify({
                "success": True,
                "message": "Token salvato correttamente",
                "file": filename,
                "access_token": response_data.get("access_token"),
                "refresh_token": response_data.get("refresh_token"),
                "id_token": response_data.get("id_token"),
                "expires_in": response_data.get("expires_in"),
                "token_type": response_data.get("token_type"),
                "state": state
            })
        else:
            logger.error(f"Errore: {response_data.get('error', 'Errore sconosciuto')}")
            return jsonify({
                "success": False,
                "error": response_data.get("error", "Errore sconosciuto"),
                "status_code": response.status_code
            }), 400

    except Exception as e:
        logger.error("Eccezione catturata durante la gestione della richiesta.")
        logger.error(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


def cleanup_old_files(directory, max_files=5, filter_func=None):
    """
    Mantiene al massimo 'max_files' file nella directory specificata.
    Puoi fornire una funzione 'filter_func' per filtrare i file da mantenere.
    """
    try:
        all_files = os.listdir(directory)

        # Applica filtro, se fornito
        if filter_func:
            all_files = list(filter(filter_func, all_files))

        # Ordina i file per data di modifica
        all_files = sorted(
            all_files,
            key=lambda f: os.path.getmtime(os.path.join(directory, f))
        )

        # Rimuove i più vecchi se necessario
        if len(all_files) > max_files:
            to_delete = all_files[:-max_files]
            for file_name in to_delete:
                full_path = os.path.join(directory, file_name)
                os.remove(full_path)
                logger.info(f"File rimosso: {full_path}")

    except Exception as e:
        logger.error(f"Errore nella pulizia dei file: {e}")


def is_token_file(filename):
    return filename.startswith("tesla_token_") and filename.endswith(".json")


def refresh_token():
    latest_file = os.path.join(data_directory, "tesla_token_latest.json")
    try:
        with open(latest_file, "r") as f:
            token_data = json.load(f)

        refresh_token_value = token_data.get("refresh_token")
        if not refresh_token_value:
            logger.error("❌ refresh_token non trovato.")
            return False

        payload = {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": refresh_token_value,
            "scope": "openid vehicle_device_data vehicle_cmds vehicle_charging_cmds offline_access user_data",
            "audience": "https://fleet-api.prd.eu.vn.cloud.tesla.com"
        }

        logger.info("🔄 Invio richiesta di refresh token a Tesla...")
        response = requests.post(TOKEN_URL, data=payload)
        logger.info(f"✅ Risposta ricevuta: {response.status_code}")

        if response.status_code == 200:
            new_token_data = response.json()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_to_file(new_token_data, f"tesla_token_{timestamp}.json")
            save_to_file(new_token_data, "tesla_token_latest.json")
            logger.info("✅ Token aggiornato e salvato con successo.")
            return True
        else:
            logger.error(f"❌ Errore nel refresh: {response.text}")
            return False

    except Exception as e:
        logger.error(f"❌ Eccezione durante il refresh: {str(e)}")
        return False



async def run_remote_command(command="wake_up", value=None):
    try:
        remote_cmd = f'php /home/sergio/Scrivania/docker/shelly_monitoring/tesla-proxy-scripts/tesla_commands.php {command}'
        if value is not None:
            remote_cmd += f' {value}'

        async with asyncssh.connect(
            host='host.docker.internal',
            port=22,
            username='sergio',
            client_keys=['/app/id_rsa_esprimo'],
            known_hosts=None
        ) as conn:
            logger.info(f"🚀 Eseguo comando remoto: {remote_cmd}")
            result = await conn.run(remote_cmd, check=True)

            output_lines = result.stdout.strip().splitlines()
            for line in output_lines:
                logger.info(f"📤 Output: {line}")

            full_output = "\n".join(output_lines)
            json_start = full_output.find('{')
            if json_start != -1:
                json_str = full_output[json_start:]
                try:
                    data = json.loads(json_str)
                    command_sent = data.get("command_sent")
                    status = data.get("status")
                    code = data.get("code")
                    logger.info(f"✅ Comando: {command_sent}, Stato: {status}, Codice: {code}")

                    # Parsing risposta interna
                    charging_amps_to_log = None
                    if isinstance(data.get("output"), list) and data["output"]:
                        inner_output = data["output"][0]
                        try:
                            inner_data = json.loads(inner_output)
                            response = inner_data.get("response")
                            logger.info(f"📦 Risposta Tesla: {response}")

                            # Condizioni per salvare 0
                            if command_sent == "charge_stop":
                                if (response and response.get("result")) or \
                                   (response and not response.get("result") and "not_charging" in response.get("string", "")):
                                    charging_amps_to_log = 0

                            # Condizione per salvare amperaggio
                            elif command_sent == "set_charging_amps" and response and response.get("result"):
                                try:
                                    charging_amps_to_log = int(value)
                                except Exception:
                                    logger.warning("⚠️ Valore amperaggio non valido per inserimento DB.")

                            # Se abbiamo un valore valido, scriviamolo
                            if charging_amps_to_log is not None:
                                try:
                                    conn_db = mysql.connector.connect(
                                        host=os.getenv("MYSQL_HOST", "mysql"),
                                        user=os.getenv("MYSQL_USER", "root"),
                                        password=os.getenv("MYSQL_PASSWORD", "local"),
                                        database=os.getenv("MYSQL_DATABASE", "dati")
                                    )
                                    cursor = conn_db.cursor()
                                    insert_query = "INSERT INTO tesla_status (timestamp, charging_amps) VALUES (%s, %s)"
                                    cursor.execute(insert_query, (datetime.now(), charging_amps_to_log))
                                    conn_db.commit()
                                    logger.info(f"💾 Stato ricarica registrato: {charging_amps_to_log} A")
                                except Error as db_err:
                                    logger.error(f"❌ Errore inserimento DB: {db_err}")
                                finally:
                                    if cursor:
                                        cursor.close()
                                    if conn_db:
                                        conn_db.close()

                            if inner_data.get("error"):
                                logger.warning(f"⚠️ Errore Tesla: {inner_data.get('error_description')}")

                        except json.JSONDecodeError:
                            logger.warning("⚠️ JSON interno non valido.")
                    return data

                except json.JSONDecodeError:
                    logger.warning("⚠️ JSON principale non valido.")
            else:
                logger.warning("⚠️ Nessun blocco JSON trovato nell’output.")

    except (OSError, asyncssh.Error) as e:
        logger.error(f"❌ Errore nella connessione SSH o nell'esecuzione: {e}")





def log_last_power_data():
    try:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "mysql"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "local"),
            database=os.getenv("MYSQL_DATABASE", "dati")
        )

        cursor = conn.cursor(dictionary=True)

        query = """
            SELECT * FROM media_mobile_5min
            ORDER BY timestamp DESC
            LIMIT 1
        """
        cursor.execute(query)
        row = cursor.fetchone()

        if row:
            produzione = float(row['media_produzione_foto'])
            assorbimento = float(row['media_assorbimento_casa'])
            differenza = produzione - assorbimento

            logger.info("🔋 Ultimo dato registrato:")
            logger.info(f"🕒 Timestamp: {row['timestamp']}")
            logger.info(f"⚡ Produzione fotovoltaico: {produzione:.2f} W")
            logger.info(f"🏠 Assorbimento casa: {assorbimento:.2f} W")
            logger.info(f"🔄 Differenza produzione - assorbimento: {differenza:.2f} W")

            return differenza
        else:
            logger.warning("⚠️ Nessun dato trovato nella tabella media_mobile_5min.")
            return None

    except Error as e:
        logger.error(f"❌ Errore MySQL: {e}")
        return None


async def check_and_charge_tesla():
    differenza = log_last_power_data()
    #differenza = 3200

    if differenza is None:
        logger.warning("⚠️ Differenza non calcolabile, nessuna azione eseguita.")
        return

    # Connetti al database
    try:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "mysql"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "local"),
            database=os.getenv("MYSQL_DATABASE", "dati")
        )
        cursor = conn.cursor(dictionary=True)

        # Recupera ultimo valore di charging_amps
        cursor.execute("SELECT charging_amps FROM tesla_status ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        current_amps = row["charging_amps"] if row else 0
        logger.info(f"🔌 Stato attuale Tesla: {current_amps} A")

    except mysql.connector.Error as e:
        logger.error(f"❌ Errore MySQL durante il recupero dello stato: {e}")
        return

    # Se energia insufficiente → STOP
    if differenza < 5 * 220:
        if current_amps == 0:
            logger.info(f"🔁 Tesla non sta caricando (già 0 A), nessun comando 'charge_stop' inviato.")
        else:
            logger.info(f"⚠️ Energia insufficiente ({differenza:.2f} W), invio comando 'charge_stop'")
            result = await run_remote_command(command="charge_stop")
            
            # Se eseguito con successo, salva 0 nel DB
            if result:
                inner = result.get("output", [{}])[0]
                try:
                    inner_data = json.loads(inner)
                    response = inner_data.get("response", {})
                    if result["status"] == "success" and (
                        response.get("result") is True or 
                        response.get("string") == "car could not execute command: not_charging"
                    ):
                        cursor.execute("INSERT INTO tesla_status (charging_amps) VALUES (0)")
                        conn.commit()
                        logger.info("💾 Stato Tesla aggiornato nel DB (0 A).")
                except Exception as e:
                    logger.warning(f"⚠️ Errore nel parsing JSON interno: {e}")
        cursor.close()
        conn.close()
        return

    # Se già carica → non inviare charge_start
    if current_amps == 0:
        logger.info(f"🔌 Energia sufficiente ({differenza:.2f} W), invio comando 'charge_start'")
        await run_remote_command(command="charge_start")
    else:
        logger.info(f"⚡ Tesla sta già caricando ({current_amps} A), salto 'charge_start'")

    # Imposta amperaggio corretto solo se cambia
    for amps in range(13, 4, -1):
        soglia = amps * 220
        logger.debug(f"👉 Controllo se {differenza:.2f} >= {soglia} (per {amps} A)")

        if differenza >= soglia:
            if current_amps == amps:
                logger.info(f"🔁 Amperaggio già impostato a {amps} A, nessun comando inviato.")
            else:
                logger.info(f"⚡ Energia abbondante ({differenza:.2f} W), invio 'set_charging_amps' con value={amps}")
                result = await run_remote_command(command="set_charging_amps", value=str(amps))

                # Salva nuovo valore nel DB solo se il comando è andato a buon fine
                if result and result.get("status") == "success":
                    cursor.execute("INSERT INTO tesla_status (charging_amps) VALUES (%s)", (amps,))
                    conn.commit()
                    logger.info(f"💾 Stato Tesla aggiornato nel DB ({amps} A).")
            break

    cursor.close()
    conn.close()



def insert_tesla_status(charging_amps: int):
    try:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "mysql"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "local"),
            database=os.getenv("MYSQL_DATABASE", "dati")
        )
        cursor = conn.cursor()
        cursor.execute("INSERT INTO tesla_status (charging_amps) VALUES (%s)", (charging_amps,))
        conn.commit()
        logger.info(f"📥 Stato Tesla registrato nel DB: charging_amps = {charging_amps}")
        cursor.close()
        conn.close()
    except Error as e:
        logger.error(f"❌ Errore durante l'inserimento nel DB: {e}")

        
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)


#if __name__ == "__main__":
#    asyncio.run(check_and_charge_tesla())