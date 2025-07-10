import os
import io
import json
import ssl
import requests
import traceback
import logging
import asyncio
import asyncssh # type: ignore
import mysql.connector # type: ignore
import aiohttp
import time
import socket
import threading

from filelock import FileLock, Timeout
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify, Response
from datetime import datetime
from mysql.connector import Error # type: ignore
from datetime import datetime, timedelta
from flask_cors import CORS # type: ignore
from flask_cors import cross_origin # type: ignore
#from scapy.all import ARP, Ether, srp # type: ignore


app = Flask(__name__)
CORS(app, origins=["https://esprimo-grafana.sersebasti.com"])

# Percorso al file di configurazione
config_path = "/app/config.json"

# Carica la configurazione
with open(config_path, "r") as f:
    CONFIG = json.load(f)
    
    
    
CLIENT_ID = CONFIG["CLIENT_ID"]
CLIENT_SECRET = CONFIG["CLIENT_SECRET"]
REDIRECT_URI = CONFIG["REDIRECT_URI"]
TOKEN_URL = CONFIG["TOKEN_URL"]
VIN = CONFIG["VIN"]
MAX_ENERGY_PRELEVABILE = CONFIG["MAX_ENERGY_PRELEVABILE"]
STATE = CONFIG["STATE"]
SHELLY_MAC = CONFIG["SHELLY_MAC"]
SHELLY_IP = CONFIG["SHELLY_IP"]
    
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
    backupCount=5
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

CONFIG_PATH = "/app/config.json"
LOCK_PATH = CONFIG_PATH + ".lock"

@app.route('/config_tesla', methods=['GET'])
def handle_config():
    """
    Restituisce la configurazione come lista di dict {key, value}.
    """
    lock = FileLock(LOCK_PATH, timeout=5)
    try:
        with lock:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH) as f:
                    config = json.load(f)
            else:
                config = {}
    except Timeout:
        return jsonify({"error": "Could not acquire lock"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify([{"key": k, "value": v} for k, v in config.items()])


@app.route('/set_config', methods=['GET'])
@cross_origin()
def config_tesla_get():
    """
    Aggiorna una chiave della configurazione.
    """
    key = request.args.get('key')
    value = request.args.get('value')
    token = request.args.get('token')

    if token != "27I6hQ5aW20v":
        return jsonify({"error": "Unauthorized"}), 403

    if not key or not value:
        return jsonify({"error": "Missing key or value"}), 400

    lock = FileLock(LOCK_PATH, timeout=5)
    try:
        with lock:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r") as f:
                    config = json.load(f)
            else:
                config = {}

            config[key] = value

            with open(CONFIG_PATH, "w") as f:
                json.dump(config, f, indent=2)

    except Timeout:
        return jsonify({"error": "Could not acquire lock"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "success", "updated": {key: value}})


SECRET_TOKEN = "27I6hQ5aW20v"

@app.route("/update_conf", methods=["GET"])
def update_conf():
    key = request.args.get("key")
    value = request.args.get("value")
    token = request.args.get("token")

    # validazione token
    if token != SECRET_TOKEN:
        return jsonify({"error": "Token non valido"}), 403

    # validazione chiave
    if key not in ["STATE", "MAX_ENERGY_PRELEVABILE"]:
        return jsonify({"error": "Chiave non valida"}), 400

    # validazione valore
    if key == "STATE":
        if value not in ["ON", "OFF"]:
            return jsonify({"error": "Valore per STATE deve essere 'ON' o 'OFF'"}), 400
    else:  # MAX_ENERGY_PRELEVABILE
        try:
            value = float(value)
        except ValueError:
            return jsonify({"error": "Valore per MAX_ENERGY_PRELEVABILE deve essere numerico"}), 400

    # aggiorna DB
    conn, cursor = get_db_connection()
    try:
        sql = f"""
            UPDATE conf
            SET {key} = %s
            WHERE id = 1
        """
        cursor.execute(sql, (value,))
        conn.commit()

        return jsonify({"message": f"{key} aggiornato a {value}"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()


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



def get_access_token_from_file():
    token_file = "/app/data/tesla_token_latest.json"
    try:
        with open(token_file, "r") as f:
            data = json.load(f)
            token = data.get("access_token")
            if token:
                return token
            else:
                logger.warning("⚠️ Nessun access_token trovato nel file.")
    except Exception as e:
        logger.error(f"❌ Errore lettura token da file: {e}")
    return None

# async def run_remote_command(command="wake_up", value=None, retry_on_fail=True):
#     remote_cmd = f'php /home/sergio/Scrivania/docker/shelly_monitoring/tesla-proxy-scripts/tesla_commands.php {command}'
#     if value is not None:
#         remote_cmd += f' {value}'

#     async def exec_cmd():
#         try:
#             async with asyncssh.connect(
#                 host='host.docker.internal',
#                 port=22,
#                 username='sergio',
#                 client_keys=['/app/id_rsa_esprimo'],
#                 known_hosts=None
#             ) as conn:
#                 logger.info(f"🚀 Eseguo comando remoto: {remote_cmd}")
#                 result = await conn.run(remote_cmd, check=True)

#                 output_lines = result.stdout.strip().splitlines()
#                 for line in output_lines:
#                     logger.info(f"📤 Output: {line}")

#                 # Estrae il blocco JSON principale
#                 full_output = "\n".join(output_lines)
#                 json_start = full_output.find('{')
#                 if json_start == -1:
#                     logger.warning("⚠️ Nessun blocco JSON trovato nell’output.")
#                     return None

#                 try:
#                     data = json.loads(full_output[json_start:])
#                 except json.JSONDecodeError:
#                     logger.warning("⚠️ JSON principale non valido.")
#                     return None

#                 command_sent = data.get("command_sent")
#                 status = data.get("status")
#                 code = data.get("code")
#                 logger.info(f"✅ Comando: {command_sent}, Stato: {status}, Codice: {code}")

#                 # Parsing dell'output interno
#                 output_list = data.get("output")
#                 if not output_list or not isinstance(output_list, list):
#                     return data

#                 try:
#                     inner_data = json.loads(output_list[0])
#                 except json.JSONDecodeError:
#                     logger.warning("⚠️ JSON interno non valido.")
#                     return data

#                 response = inner_data.get("response")
#                 logger.info(f"📦 Risposta Tesla: {response}")

#                 charging_amps_to_log = None

#                 if command_sent == "charge_stop" and response:
#                     if response.get("result") or "not_charging" in response.get("string", ""):
#                         charging_amps_to_log = 0

#                 elif command_sent == "set_charging_amps" and response and response.get("result"):
#                     try:
#                         charging_amps_to_log = int(value)
#                     except Exception:
#                         logger.warning("⚠️ Valore amperaggio non valido per inserimento DB.")

#                 if charging_amps_to_log is not None:
#                     insert_tesla_status(charging_amps_to_log)

#                 if inner_data.get("error"):
#                     logger.warning(f"⚠️ Errore Tesla: {inner_data.get('error_description')}")

#                 return data

#         except (OSError, asyncssh.Error) as e:
#             logger.error(f"❌ Errore nella connessione SSH o nell'esecuzione: {e}")
#             return None

#     result = await exec_cmd()

#     if not result and retry_on_fail:
#         logger.info("🔁 Comando fallito o risposta vuota. Provo a svegliare la Tesla...")
#         wake_result = await ensure_vehicle_awake()
#         if wake_result:
#             logger.info("🔋 Tesla svegliata. Riprovo il comando...")
#             return await run_remote_command(command, value, retry_on_fail=False)
#         else:
#             logger.error("⛔ Impossibile svegliare la Tesla.")
#             return None

#     return result




def aggiorna_log_media_mobile(minuti=60):
    conn, _ = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.callproc("get_media_mobile", [minuti])

        for result in cursor.stored_results():
            row = result.fetchone()
            if row:
                timestamp = row["timestamp"]
                produzione = float(row["media_produzione_foto"])
                assorbimento = float(row["media_assorbimento_casa"])
                differenza = produzione - assorbimento

                insert_query = """
                    INSERT INTO log_media_mobile (timestamp, media_produzione_foto, media_assorbimento_casa, differenza)
                    VALUES (%s, %s, %s, %s)
                """
                insert_cursor = conn.cursor()
                insert_cursor.execute(insert_query, (timestamp, produzione, assorbimento, differenza))
                conn.commit()
                insert_cursor.close()

                logger.info("✅ Inserita media mobile in log_media_mobile.")
            else:
                logger.warning("⚠️ Nessun dato restituito dalla procedura.")

    except Error as e:
        logger.error(f"❌ Errore durante l'inserimento della media mobile: {e}")
    finally:
        cursor.close()
        conn.close()



def log_last_power_data():
    conn, cursor = get_db_connection()
    if not conn:
        return None

    try:
        query = """
            SELECT timestamp, media_produzione_foto, media_assorbimento_casa, differenza
            FROM log_media_mobile
            ORDER BY timestamp DESC
            LIMIT 1
        """
        cursor.execute(query)
        row = cursor.fetchone()

        if row:
            timestamp, produzione, assorbimento, differenza = row

            logger.info("🔋 Ultimo dato da log_media_mobile:")
            logger.info(f"🕒 Timestamp: {timestamp}")
            logger.info(f"⚡ Produzione fotovoltaico: {produzione:.2f} W")
            logger.info(f"🏠 Assorbimento casa: {assorbimento:.2f} W")
            logger.info(f"🔄 Differenza produzione - assorbimento: {differenza:.2f} W")

            return differenza
        else:
            logger.warning("⚠️ Nessun dato trovato nella tabella log_media_mobile.")
            return None

    except Error as e:
        logger.error(f"❌ Errore MySQL durante la lettura: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

async def partial_execution(differenza, current_amps):
    
    logger.info("🔁 Start partial execution")
    
    assorbimento_totale = differenza - current_amps * 220
    logger.info(f"Actual power draw value: {assorbimento_totale}")
    
    if assorbimento_totale < -3000:
        logger.info(f"⚡ ower draw value < -3000: {assorbimento_totale} - send charge_stop command")
        esito, messaggio = await validate_execution(x_minuti_media_mobile=5)
        if not esito:
            logger.warning("⛔ Validazione fallita. Non riesco a bloccare la ricarica")
        else:
            logger.info(f"Validazione per partial execution: {messaggio} - interrompo ricarica")
            await run_tesla_command("charge_stop") 
    else:
            logger.info(f"✅ Assorbimento totale >= -3000: {assorbimento_totale}, nessuna azione necessaria.")
    return
    


async def check_and_charge_tesla():
    
    logger.info("🔁 Avvio check_and_charge_tesla")
    
    verify_and_update_shelly_ip()
    emeters = fetch_shelly_data()
    if emeters:
        store_data_in_db(emeters)
        logger.info("✅ Dati Shelly salvati correttamente.")
    else:
        logger.warning("⚠️ Dati Shelly non disponibili.")

    aggiorna_log_media_mobile(5)
    
    
    if STATE.upper() != "ON":
        logger.info("🚫 Stato = OFF. Sistema ricarica disattivato. Nessun comando verrà inviato.")
        return
    
    differenza = get_last_db_energy_difference()
    if differenza is None:
        logger.warning("⚠️ energy difference unknown in db  - no action.")
        return
    logger.info(f"📊 Differenza energetica più recente: {differenza:.2f} W")
    
    current_amps = get_last_db_amps()
    if current_amps is None:
        logger.warning("⚠️ current difference unknown in db - no action.")
        return  
    
    
    await partial_execution(differenza, current_amps)
    
    # Verifica se il minuto corrente è uno dei valori specificati per continuare l'esecuzione
    current_minute = datetime.now().minute
    if current_minute not in [0, 15, 30, 45]:
        logger.info(f"⏱ Minuto {current_minute}: esecuzione parziale. Interrompo esecuzione.")
        return 
           
    logger.info(f"⏱ Minuto {current_minute}: provcedo con esecuzione completa.")
    
    

    if partial_execution:
            partial_execution(differenza, current_amps)
            return
        
    else:
        
        esito, messaggio = await validate_execution(x_minuti_media_mobile=5)
        logger.info(f"Validazione: {messaggio}")
    
        if not esito:
            logger.warning("⛔ Validazione fallita. Blocco dell’esecuzione.")
            
            # 👉 Aggiorna STATE = OFF e MAX_POWER_DRAW = -5000 nel config.json
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)

                config["STATE"] = "OFF"
                config["MAX_ENERGY_PRELEVABILE"] = "-5000"  # stringa se coerente con il resto del file

                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)

                logger.info("🛑 Stato disattivato automaticamente: STATE = OFF, MAX_POWER_DRAW = -5000")
            except Exception as e:
                logger.error(f"❌ Errore aggiornando lo stato in config.json: {e}")
            return



        logger.info(f"📊 Differenza energetica più recente: {differenza:.2f} W")
    
        max_energy_prelevabile = float(MAX_ENERGY_PRELEVABILE)
    
        logger.info(f"🔧 max_energy_prelevabile configurato: {max_energy_prelevabile} W")

        soglia_minima = 5 * 220
        energia_effettiva = differenza + max_energy_prelevabile
    
        conn, cursor = get_db_connection()
        if not conn:
            logger.error("❌ Connessione al database fallita.") 
            return

        try:            
            cursor.execute("SELECT charging_amps FROM tesla_status ORDER BY timestamp DESC LIMIT 1")
            row = cursor.fetchone()
            current_amps = row[0] if row else 0
            logger.info(f"🔌 Corrente Tesla secondo il DB: {current_amps} A")
        
            azione_richiesta = None
            amps_da_impostare = None
               
            if energia_effettiva < soglia_minima:
                if current_amps > 0:
                    azione_richiesta = "charge_stop"
            else:
                for amps in range(13, 4, -1):
                    soglia = amps * 220
                    if energia_effettiva >= soglia:
                        if current_amps == 0:
                            azione_richiesta = "charge_start"
                            amps_da_impostare = amps
                        elif current_amps != amps:
                            azione_richiesta = "set_charging_amps"
                            amps_da_impostare = amps
                        break
                    
                    
            if not azione_richiesta:
                logger.info("✅ Nessuna azione necessaria: stato già coerente con l’energia disponibile.")
                return
        
            
            # ⚡ Serve inviare un comando → verifica stato veicolo
            #tesla_data = await ensure_vehicle_awake()
            #if not tesla_data:
            #    logger.warning("⚠️ Impossibile verificare stato Tesla. Comando annullato.")
            #    return

            # 🧠 Manda il comando appropriato
            if azione_richiesta == "charge_stop":
                result = await run_remote_command("charge_stop")
                # parsing identico a prima...

            elif azione_richiesta == "charge_start":
                await run_remote_command("charge_start")
                result = await run_remote_command("set_charging_amps", value=str(amps_da_impostare))
                # parsing per set_charging_amps...

            elif azione_richiesta == "set_charging_amps":
                result = await run_remote_command("set_charging_amps", value=str(amps_da_impostare))
                # parsing per set_charging_amps...      

        except:
            logger.error(f"❌ Errore invio comando : {azione_richiesta}")
        finally:
            cursor.close()
            conn.close()


async def validate_execution(x_minuti_media_mobile):
    logger.info(f"min mobile: {x_minuti_media_mobile}")
  
    try:
        conn, cursor = get_db_connection()

        # ✅ Verifica media mobile
        cursor.execute("SELECT timestamp FROM log_media_mobile ORDER BY timestamp DESC LIMIT 1")
        row_media = cursor.fetchone()
        if not row_media:
            return False, "Nessun dato presente in log_media_mobile"

        ts_media = row_media[0]
        if datetime.now() - ts_media > timedelta(minutes=x_minuti_media_mobile) and x_minuti_media_mobile != -1:
            return False, f"Media mobile troppo vecchia: {ts_media}"


        data = await ensure_vehicle_awake()
        if not data:
            return False, "❌ Veicolo non pronto o cavo non collegato"

        return True, "✅ Tutto ok: media mobile aggiornata e veicolo pronto"

    except Exception as e:
        return False, f"❌ Errore durante la verifica: {e}"

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



async def check_vehicle_status(max_attempts=3, delay=10):
    """
    Verifica lo stato del veicolo Tesla, provando a svegliarlo se necessario.

    Tenta di ottenere i dati dal veicolo fino a `max_attempts` volte, con un intervallo di `delay` secondi.
    Se il veicolo non risponde, invia un comando `wake_up` e riprova.

    Restituisce un dizionario con:
        - reachable: True/False -> se il veicolo ha risposto
        - vehicle_name: nome del veicolo o None
        - cable_connected: True/False -> se il cavo è collegato correttamente
        - battery_level: int (percentuale) o None
        - charging_state: stato della carica (string) o None
        - actual_current: int o None
        - ready: True/False -> se pronto a ricevere comandi (cavo collegato e batteria < 100%)
        - message: messaggio descrittivo
    """
    for attempt in range(1, max_attempts + 1):
        logger.info(f"🔍 Tentativo {attempt}/{max_attempts} per ottenere dati dal veicolo...")
        data = await get_vehicle_data()

        if data and isinstance(data, dict):
            response = data.get("response")
            if not response or not isinstance(response, dict):
                logger.warning("⚠️ 'response' non trovato o non valido.")
                logger.debug(f"📦 Risposta grezza: {data}")
            else:
                vehicle_name = response.get("vehicle_state", {}).get("vehicle_name")
                drive_state = response.get("drive_state", {})
                charge_state = response.get("charge_state", {})

                actual_current = charge_state.get("charger_actual_current")
                port_latch = charge_state.get("charge_port_latch")
                charging_state = charge_state.get("charging_state")
                conn_cable = charge_state.get("conn_charge_cable")
                battery_level = charge_state.get("battery_level")

                latitude = drive_state.get("latitude")
                longitude = drive_state.get("longitude")

                logger.debug(
                    f"🔧 Stato carica: current={actual_current}, latch={port_latch}, "
                    f"stato={charging_state}, cavo={conn_cable}, batteria={battery_level}%, "
                    f"lat={latitude}, lon={longitude}"
                )

                # Valuta se il cavo è correttamente collegato
                cavo_collegato = (
                    port_latch == "Engaged" and
                    charging_state != "Disconnected" and
                    conn_cable != "<invalid>"
                )

                result = {
                    "reachable": True,
                    "vehicle_name": vehicle_name,
                    "cable_connected": cavo_collegato,
                    "battery_level": battery_level,
                    "charging_state": charging_state,
                    "actual_current": actual_current,
                    "ready": cavo_collegato and battery_level is not None and battery_level < 100,
                    "message": ""
                }

                # Imposta il messaggio in base alla condizione
                if not cavo_collegato:
                    result["message"] = "🔌 Cavo non collegato correttamente."
                elif battery_level == 100:
                    result["message"] = "🔋 Batteria al 100%."
                else:
                    result["message"] = "✅ Veicolo pronto a ricevere comandi."

                logger.info(result["message"])
                return result

        else:
            logger.warning("⚠️ Nessuna risposta valida dal veicolo.")
            logger.debug(f"📦 Risposta grezza: {data}")

        # Se non ha risposto, prova a svegliarlo
        if attempt < max_attempts:
            logger.info("🚨 Invio comando 'wake_up'...")
            await run_tesla_command("wake_up")
            await asyncio.sleep(delay)
        else:
            logger.error("❌ Tentativi esauriti. Impossibile ottenere dati dal veicolo.")
            return {
                "reachable": False,
                "vehicle_name": None,
                "cable_connected": False,
                "battery_level": None,
                "charging_state": None,
                "actual_current": None,
                "ready": False,
                "message": "🚫 Impossibile raggiungere il veicolo dopo i tentativi."
            }


async def get_vehicle_data():
    token = get_access_token_from_file()
    if not token:
        logger.error("❌ Token non disponibile. Impossibile fare richiesta a Tesla.")
        return None

    url = "https://fleet-api.prd.eu.vn.cloud.tesla.com/api/1/vehicles/LRW3E7FA9MC345603/vehicle_data"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                status = resp.status
                text = await resp.text()

                logger.info(f"📡 Risposta HTTP: {status}")
                #logger.debug(f"📥 Contenuto completo (raw):\n{text}")

                try:
                    json_data = json.loads(text)
                    #logger.debug(f"📦 JSON decodificato:\n{json.dumps(json_data, indent=2)}")
                    return json_data
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Errore nel parsing JSON: {e}")
                    return None

        except Exception as e:
            logger.error(f"❌ Errore richiesta HTTP verso Tesla: {e}")
            return None

def get_last_db_energy_difference():
    conn, cursor = get_db_connection()
    if not conn:
        return None

    try:
        cursor.execute("SELECT differenza FROM log_media_mobile ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        return float(row[0]) if row else None
    except Exception as e:
        logger.error(f"❌ Errore nel recupero differenza dal DB: {e}")
        return None
    finally:
        cursor.close()
        conn.close()
        

def get_last_db_amps():
    conn, cursor = get_db_connection()
    if not conn:
        logger.error("❌ Connessione al database fallita.") 
        return
    try:            
        cursor.execute("SELECT charging_amps FROM tesla_status ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        current_amps = row[0] if row else 0
        logger.info(f"🔌 Corrente Tesla secondo il DB: {current_amps} A")
        return current_amps
    except Exception as e:
        logger.error(f"❌ Errore nel recupero differenza dal DB: {e}")
        return None
    finally:
        cursor.close()
        conn.close()   
             
    
        


def insert_tesla_status(charging_amps: int, latitude: float = None, longitude: float = None, battery_level: int = None):
    conn, cursor = get_db_connection()
    if not conn:
        return

    try:
        # Costruzione dinamica della query
        columns = ["charging_amps"]
        values = [charging_amps]
        placeholders = ["%s"]

        if latitude is not None:
            columns.append("latitude")
            values.append(latitude)
            placeholders.append("%s")

        if longitude is not None:
            columns.append("longitude")
            values.append(longitude)
            placeholders.append("%s")

        if battery_level is not None:
            columns.append("battery_level")
            values.append(battery_level)
            placeholders.append("%s")

        query = f"INSERT INTO tesla_status ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
        cursor.execute(query, values)
        conn.commit()

        logger.info(f"📥 Stato Tesla registrato nel DB: {dict(zip(columns, values))}")
    except Error as e:
        logger.error(f"❌ Errore durante l'inserimento nel DB: {e}")
    finally:
        cursor.close()
        conn.close()

'''
async def safety_check_tesla():
    logger.info("🔁 Avvio controllo di sicurezza Tesla...")

    differenza = get_last_db_energy_difference()
    if differenza is None:
        logger.warning("⚠️ Differenza non disponibile, controllo annullato.")
        return

    logger.info(f"📊 Differenza energetica più recente: {differenza:.2f} W")

    max_fissa = 3000
    soglia_minima = 5 * 220
    energia_effettiva = differenza + max_fissa

    logger.info(f"🔧 Energia effettiva simulata con MAX fisso ({max_fissa} W): {energia_effettiva:.2f} W")
    logger.info(f"🔒 Soglia minima di sicurezza: {soglia_minima} W")

    conn, cursor = get_db_connection()
    if not conn:
        logger.error("❌ Connessione al database fallita.")
        return

    try:
        cursor.execute("SELECT charging_amps FROM tesla_status ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        current_amps = row[0] if row else 0

        logger.info(f"🔌 Corrente Tesla attuale: {current_amps} A")

        if energia_effettiva < soglia_minima and current_amps > 0:
            logger.warning("⚡ Energia insufficiente, invio comando STOP alla Tesla.")
            await run_remote_command("charge_stop")
        else:
            logger.info("✅ Nessuna azione necessaria. Condizioni sicure.")
    except mysql.connector.Error as e:
        logger.error(f"❌ Errore MySQL durante il controllo di sicurezza: {e}")
    finally:
        cursor.close()
        conn.close()
'''


def fetch_shelly_data():
   
    if not SHELLY_IP:
        logger.error("❌ Indirizzo IP di Shelly non configurato.")
        return None 
    


    url = f"http://{SHELLY_IP}/status"
    logger.info(f"Richiesta a Shelly: {url}")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Risposta Shelly: {response.status_code}")
        #logger.debug(f"Risposta Shelly: {data}")
        
        return data.get("emeters", [])  # Estratto direttamente dalla API locale
    except requests.RequestException as e:
        logger.error(f"Errore nella richiesta a Shelly: {e}")

        

        return None

def store_data_in_db(emeters):
    #logger.debug(f"Emeters Shelly: {emeters}")
    
    #Salva i dati di tutte le fasi in un'unica riga nel database MySQL.
    logger.info("Salvataggio dati Shelly nel DB...")
    #logger.debug(f"Dati Shelly: {emeters}")
    if not emeters: 
        logger.warning("⚠️ Nessun dato Shelly disponibile per il salvataggio.")
        return None
    
    # Connessione al database
    conn, cursor = get_db_connection()
    if not conn:
        logger.error("❌ Connessione al database fallita.")
        return None
    
    
    try:
        query = """
            INSERT INTO shelly_emeters (timestamp, 
                                        power_1, pf_1, current_1, voltage_1, total_1, total_returned_1,
                                        power_2, pf_2, current_2, voltage_2, total_2, total_returned_2,
                                        power_3, pf_3, current_3, voltage_3, total_3, total_returned_3)
            VALUES (NOW(), %s, %s, %s, %s, %s, %s, 
                        %s, %s, %s, %s, %s, %s, 
                        %s, %s, %s, %s, %s, %s)
        """
        
        
        
        # Prende i valori delle tre fasi (o mette 0 se non disponibili)
        values = []
        for i in range(3):
            emeter = emeters[i] if i < len(emeters) else {"power": 0, "pf": 0, "current": 0, "voltage": 0, "total": 0, "total_returned": 0}
            values.extend([emeter["power"], emeter["pf"], emeter["current"], emeter["voltage"], emeter["total"], emeter["total_returned"]])
        
        #logger.debug(f"Query: {query}")
        
        cursor.execute(query, tuple(values))
        conn.commit()
        
        logger.info("✅ Dati Shelly inseriti correttamente nel DB.")
    except Exception as e:
        logger.error(f"❌ Errore durante l'inserimento dei dati Shelly: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

    

def get_db_connection(dictionary=False):
    try:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "mysql"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "local"),
            database=os.getenv("MYSQL_DATABASE", "dati")
        )
        cursor = conn.cursor(dictionary=dictionary)
        return conn, cursor
    except mysql.connector.Error as e:
        logger.error(f"❌ Errore connessione al DB: {e}")
        return None, None


def is_shelly_ip(ip):
    try:
        response = requests.get(f"http://{ip}/status", timeout=1)
        if response.status_code == 200:
            data = response.json()
            return data.get("mac", "").upper() == SHELLY_MAC
    except requests.RequestException:
        pass
    return False
    
def find_shelly_ip():
    for i in range(1, 255):
        ip = f"192.168.1.{i}"
        if is_shelly_ip(ip):
            return ip
    return None    
    
def verify_and_update_shelly_ip():
    global SHELLY_IP

    if not is_shelly_ip(SHELLY_IP):
        new_ip = find_shelly_ip()
        if new_ip:
            CONFIG["SHELLY_IP"] = new_ip
            SHELLY_IP = new_ip
            # Salva la configurazione aggiornata
            with open(config_path, "w") as f:
                json.dump(CONFIG, f, indent=2)
            logger.info(f"Dispositivo Shelly trovato all'indirizzo: {new_ip}")
        else:
            logger.error("Dispositivo Shelly non trovato sulla rete.")
    else:
        logger.info(f"Dispositivo Shelly già configurato all'indirizzo: {SHELLY_IP}")            
    
        

def get_conf():
    conn, cursor = get_db_connection(dictionary=True)
    try:
        cursor.execute("""
            SELECT STATE, MAX_ENERGY_PRELEVABILE
            FROM conf
            WHERE ID = 1
        """)
        row = cursor.fetchone()
        return row  # restituisce un dict, es: {'STATE': 'ON', 'MAX_ENERGY_PRELEVABILE': -1}

    except Exception as e:
        print(f"Errore: {e}")
        return None

    finally:
        cursor.close()
        conn.close()


def set_conf(key, value):
    if key not in ["STATE", "MAX_ENERGY_PRELEVABILE"]:
        raise ValueError("Chiave non valida: deve essere 'STATE' o 'MAX_ENERGY_PRELEVABILE'")

    conn, cursor = get_db_connection()
    try:
        sql = f"""
            UPDATE conf
            SET {key} = %s
            WHERE ID = 1
        """
        cursor.execute(sql, (value,))
        conn.commit()
        return True

    except Exception as e:
        print(f"Errore: {e}")
        return False

    finally:
        cursor.close()
        conn.close()

    
async def shelly_logger():
    

    if not SHELLY_IP:
        logger.error("❌ Indirizzo IP di Shelly non configurato. Impossibile avviare il logger.")
        return
    
    verify_and_update_shelly_ip()
    
    tesla_charging_amps = None
    charging_state = None
    first_loop = True

    while True:
        
            emitters = fetch_shelly_data()
            if emitters:
                store_data_in_db(emitters)
                logger.info("✅ Dati Shelly salvati correttamente.")
                
                enel_power_value = float(emitters[1]['power'])
                logger.info(f"⚡ Potenza prelevata da Enel: {enel_power_value} W")


                conf = get_conf()    
                STATE = conf["STATE"]
                MAX_ENERGY_PRELEVABILE = conf["MAX_ENERGY_PRELEVABILE"]

                if STATE == "ON":
                     
                    if first_loop:

                        first_loop = False
                        result = await check_vehicle_status()
                        
                        if result.get("reachable") and result.get("cable_connected") and result.get("ready"):
                            logger.info("🚀 Veicolo pronto a ricevere comandi!")
                            insert_tesla_status(int(result.get("actual_current", 0)), latitude=result.get("latitude"), longitude=result.get("longitude"), battery_level=result.get("battery_level"))
                            tesla_charging_amps = int(result.get("actual_current", 0))
                            logger.info(f"🔌 Corrente di carica Tesla attuale: {tesla_charging_amps} A")
                            
                            charging_state = result.get("charging_state")
                            logger.info(f"🔋 Stato di carica Tesla: {charging_state}")
                            

                        else:
                            logger.info("⛔ Veicolo NON pronto.")
                            if not result.get("reachable"):
                                logger.info("🔴 Il veicolo non è raggiungibile.")
                            elif not result.get("cable_connected"):
                                logger.info("🔌 Il cavo di ricarica non è collegato.")
                            elif not result.get("ready") and result.get("battery_level") == 100:
                                logger.info("🔋 La batteria è già carica al 100%.")
                            elif not result.get("ready"):
                                logger.info("⚠️ Il veicolo non è pronto a ricevere comandi - not ready.")
                            else:
                                logger.info("⚠️ Il veicolo non è pronto a ricevere comandi per altra ragione sconosciuta.")

                            try:
                                set_conf("STATE", "OFF")
                                logger.info("🛑 Stato disattivato automaticamente: STATE = OFF")
                                first_loop = True
                                continue
                            except Exception as e:
                                logger.error(f"❌ Errore aggiornando configurazione: {e}")

                    
                    max_allowed_amps = None
                    for amps in range(13, 5, -1):  # da 13 a 6 inclusi
                        total_power = (amps * 220) + enel_power_value
                        if total_power < float(MAX_ENERGY_PRELEVABILE):
                            max_allowed_amps = amps
                            break

                    if max_allowed_amps == tesla_charging_amps:
                        logger.info(f"✅ Corrente di carica Tesla già impostata a {tesla_charging_amps} A. Nessuna azione necessaria.")
                        insert_tesla_status(max_allowed_amps)
                        

                    elif max_allowed_amps is None:
                        logger.warning("⚠️ Nessuna corrente impostabile trovata che rispetti il limite di {MAX_ENERGY_PRELEVABILE} W.")
                        logger.info("🔴 Interrompo la ricarica.")
                        # Se non è possibile impostare una corrente, interrompi la ricarica
                        result_charge_stop = await run_tesla_command("charge_stop")
                        
                            
                        if result_charge_stop.get("status") == "success":
                            logger.info("✅ Comando charge_stop inviato con successo.")
                            insert_tesla_status(0)
                            
                        else:
                            logger.error("❌ Errore inviando il comando charge_stop.")
                            set_conf("STATE", "OFF")
                            first_loop = True
                               

                    elif max_allowed_amps != tesla_charging_amps:
                            
                            result_set_charging_amps = await run_tesla_command("set_charging_amps", max_allowed_amps)

                            if result_set_charging_amps.get("status") == "error":   
                                logger.error("❌ Errore inviando il comando set_charging_amps.")
                                
                            else:                                                                                                                                                                       
                                logger.info(f"✅ Comando set_charging_amps inviato con successo: {max_allowed_amps} A")
                                tesla_charging_amps = max_allowed_amps
                                result_2 = await check_vehicle_status()
                                if result_2.get("battery_level") == 100:
                                    logger.info("🔋 La batteria è già carica al 100%. Interrompo la ricarica.")
                                    set_conf("STATE", "OFF")
                                    insert_tesla_status(0)
                                    first_loop = True

                                if result_2.get("charging_state") != "Charging":
                                    logger.info("🔋 La Tesla non è in carica. Avvio la ricarica.")
                                    if await run_tesla_command("charge_start"):
                                        logger.info("✅ Comando charge_start inviato con successo.")
                                        charging_state = "Charging"
                                    else:
                                        logger.error("❌ Errore inviando il comando charge_start.")
                                        set_conf("STATE", "OFF")
                                        first_loop = True
                                        tesla_charging_amps = None
                                        charging_state = None


                                    
                else:
                    logger.info("🚫 Stato = OFF. Sistema gestione ricarica disattivato. Nessun comando verrà inviato.")
                    tesla_charging_amps = None
                    charging_state = None

            else:
                logger.warning("⚠️ Dati Shelly non disponibili. Riprovo tra 10 secondi...")
                set_conf("STATE", "OFF")
                first_loop = True

            time.sleep(30)       

                                

                       

            
        



async def run_tesla_command(command, charging_amps_value=None, retried=False):
    TESLA_TOKEN_FILE = "/app/data/tesla_token_latest.json"
    CERT_PATH = "/app/tesla-proxy-config/cert.pem"
    PROXY_URL_BASE = "https://tesla_http_proxy:4443/api/1/vehicles"

    if not os.path.exists(TESLA_TOKEN_FILE):
        logger.error("Token file non trovato.")
        return {"status": "error", "message": "Token file non trovato"}

    with open(TESLA_TOKEN_FILE) as f:
        token_data = json.load(f)
    access_token = token_data.get("access_token")
    if not access_token:
        logger.error("Access token mancante.")
        return {"status": "error", "message": "Access token mancante"}

    url = f"{PROXY_URL_BASE}/{VIN}/command/{command}"
    payload = {}
    if command == "set_charging_amps" and charging_amps_value is not None:
        payload = {"charging_amps": int(charging_amps_value)}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    ssl_ctx = ssl.create_default_context(cafile=CERT_PATH)

    logger.info(f"🚀 Invio comando {command} al proxy…")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload, ssl=ssl_ctx) as resp:
                text = await resp.text()
                logger.debug(f"📥 Risposta grezza: {text}")
                try:
                    output = json.loads(text)
                except json.JSONDecodeError:
                    output = text

                result = {
                    "status": "success" if resp.status == 200 else "error",
                    "command_sent": command,
                    "value": charging_amps_value,
                    "output": output,
                    "code": resp.status
                }

                if resp.status == 401:
                    if retried:
                        logger.error("❌ Errore con token.")
                        result["message"] = "Errore con token"
                        return result

                    logger.warning("🔄 Provo a aggiornare il token")
                    from app import refresh_token
                    if refresh_token():
                        logger.info("✅ Token aggiornato, ritento il comando.")
                        return await run_tesla_command(command, charging_amps_value, retried=True)
                    else:
                        logger.error("❌ Impossibile aggiornare il token.")
                        result["message"] = "TErrore con token e refresh fallito"
                        return result

                return result

        except Exception as e:
            logger.error(f"❌ Errore durante la richiesta al proxy: {e}")
            return {"status": "error", "message": str(e)}