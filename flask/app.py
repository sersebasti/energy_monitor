import os
import io
import json
import requests
import traceback
import logging
import asyncio
import asyncssh # type: ignore
import mysql.connector # type: ignore
import aiohttp
import time
import socket

from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify, Response
from datetime import datetime
from mysql.connector import Error # type: ignore
from datetime import datetime, timedelta
#from flask_cors import CORS # type: ignore
#from flask_cors import cross_origin # type: ignore
#from scapy.all import ARP, Ether, srp # type: ignore


app = Flask(__name__)


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


@app.route('/config_tesla', methods=['GET', 'POST'])
def handle_config():
    path = "/app/config.json"

    if request.method == 'GET':
        with open(path) as f:
            config = json.load(f)

        # Restituisce tutte le chiavi come lista di dizionari {key, value}
        return jsonify([{"key": k, "value": v} for k, v in config.items()])



@app.route('/set_config', methods=['GET'])
#@cross_origin()
def config_tesla_get():
    key = request.args.get('key')
    value = request.args.get('value')
    token = request.args.get('token')

    if token != "27I6hQ5aW20v":
        return jsonify({"error": "Unauthorized"}), 403

    if not key or not value:
        return jsonify({"error": "Missing key or value"}), 400

    # Leggi il file JSON esistente
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        config = {}

    # Aggiorna il valore
    config[key] = value

    # Salva il file aggiornato
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    return jsonify({"status": "success", "updated": {key: value}})


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

async def run_remote_command(command="wake_up", value=None, retry_on_fail=True):
    remote_cmd = f'php /home/sergio/Scrivania/docker/shelly_monitoring/tesla-proxy-scripts/tesla_commands.php {command}'
    if value is not None:
        remote_cmd += f' {value}'

    async def exec_cmd():
        try:
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

                # Estrae il blocco JSON principale
                full_output = "\n".join(output_lines)
                json_start = full_output.find('{')
                if json_start == -1:
                    logger.warning("⚠️ Nessun blocco JSON trovato nell’output.")
                    return None

                try:
                    data = json.loads(full_output[json_start:])
                except json.JSONDecodeError:
                    logger.warning("⚠️ JSON principale non valido.")
                    return None

                command_sent = data.get("command_sent")
                status = data.get("status")
                code = data.get("code")
                logger.info(f"✅ Comando: {command_sent}, Stato: {status}, Codice: {code}")

                # Parsing dell'output interno
                output_list = data.get("output")
                if not output_list or not isinstance(output_list, list):
                    return data

                try:
                    inner_data = json.loads(output_list[0])
                except json.JSONDecodeError:
                    logger.warning("⚠️ JSON interno non valido.")
                    return data

                response = inner_data.get("response")
                logger.info(f"📦 Risposta Tesla: {response}")

                charging_amps_to_log = None

                if command_sent == "charge_stop" and response:
                    if response.get("result") or "not_charging" in response.get("string", ""):
                        charging_amps_to_log = 0

                elif command_sent == "set_charging_amps" and response and response.get("result"):
                    try:
                        charging_amps_to_log = int(value)
                    except Exception:
                        logger.warning("⚠️ Valore amperaggio non valido per inserimento DB.")

                if charging_amps_to_log is not None:
                    insert_tesla_status(charging_amps_to_log)

                if inner_data.get("error"):
                    logger.warning(f"⚠️ Errore Tesla: {inner_data.get('error_description')}")

                return data

        except (OSError, asyncssh.Error) as e:
            logger.error(f"❌ Errore nella connessione SSH o nell'esecuzione: {e}")
            return None

    result = await exec_cmd()

    if not result and retry_on_fail:
        logger.info("🔁 Comando fallito o risposta vuota. Provo a svegliare la Tesla...")
        wake_result = await ensure_vehicle_awake()
        if wake_result:
            logger.info("🔋 Tesla svegliata. Riprovo il comando...")
            return await run_remote_command(command, value, retry_on_fail=False)
        else:
            logger.error("⛔ Impossibile svegliare la Tesla.")
            return None

    return result

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




async def check_and_charge_tesla():
    
    current_minute = datetime.now().minute
    if current_minute not in [0, 15, 30, 45]:
        logger.info(f"⏱ Minuto {current_minute}: esecuzione parziale.")
        aggiorna_log_media_mobile(5)
        verify_and_update_shelly_ip()
        return
    else:
        logger.info(f"⏱ Minuto {current_minute}: esecuzione completa.")
    
    
    if STATE.upper() != "ON":
        logger.info("🚫 Stato = OFF. Ricarica disattivata. Nessun comando verrà inviato.")
        return
    
    
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

    differenza = get_last_logged_difference()
    if differenza is None:
        logger.warning("⚠️ Differenza non calcolabile dal DB, nessuna azione eseguita.")
        return

    logger.info(f"📊 Differenza energetica più recente: {differenza:.2f} W")

    max_energy_prelevabile = float(MAX_ENERGY_PRELEVABILE)
    logger.info(f"🔧 max_energy_prelevabile configurato: {max_energy_prelevabile} W")

    soglia_minima = 5 * 220
    energia_effettiva = differenza + max_energy_prelevabile
    
    conn, cursor = get_db_connection()
    if not conn:
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
            try:
                insert_tesla_status(charging_amps=int(current_amps))
                logger.info(f"💾 Stato Tesla salvato comunque nel DB: {current_amps} A")
            except Exception as e:
                logger.error(f"❌ Errore salvataggio corrente nel DB: {e}")
            return
        
        
            
        # ⚡ Serve inviare un comando → verifica stato veicolo
        tesla_data = await ensure_vehicle_awake()
        if not tesla_data:
            logger.warning("⚠️ Impossibile verificare stato Tesla. Comando annullato.")
            return

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

    except mysql.connector.Error as e:
        logger.error(f"❌ Errore MySQL nel recupero o aggiornamento stato Tesla: {e}")
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
        if datetime.now() - ts_media > timedelta(minutes=x_minuti_media_mobile):
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


async def ensure_vehicle_awake(max_attempts=3, delay=10):
    for attempt in range(1, max_attempts + 1):
        logger.info(f"🔍 Tentativo {attempt}/{max_attempts} per ottenere dati dal veicolo...")
        data = await get_vehicle_data()

        if data and isinstance(data, dict):
            response = data.get("response")
            if not response or not isinstance(response, dict):
                logger.warning("⚠️ 'response' non trovato o non valido nella risposta dell'API.")
                logger.debug(f"📦 Risposta grezza ricevuta: {data}")
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

                # 🔌 Log dettagliato di tutti i parametri
                logger.debug(f"🔧 Stato carica: current={actual_current}, latch={port_latch}, stato={charging_state}, cavo={conn_cable}, batteria={battery_level}%, lat={latitude}, lon={longitude}")

                # ✅ Scrive sempre il dato nel DB
                if actual_current is not None:
                    try:
                        insert_tesla_status(
                            charging_amps=int(actual_current),
                            latitude=latitude,
                            longitude=longitude,
                            battery_level=battery_level
                        )
                        logger.info(f"💾 Stato Tesla salvato nel DB: {actual_current} A, batteria: {battery_level}%, posizione: ({latitude}, {longitude})")
                    except Exception as e:
                        logger.error(f"❌ Errore salvataggio nel DB: {e}")



                # ✅ Criterio completo per cavo collegato
                cavo_collegato = (
                    port_latch == "Engaged" and
                    charging_state != "Disconnected" and
                    conn_cable != "<invalid>"
                )

                # ℹ️ Log riassuntivo
                if vehicle_name is not None and actual_current is not None:
                    cable_status = "collegato ✅" if cavo_collegato else "non collegato ❌"
                    logger.info(f"✅ Veicolo online: nome = {vehicle_name}, corrente = {actual_current} A, cavo = {cable_status}")

                    if not cavo_collegato:
                        logger.warning("🔌 Cavo non collegato correttamente! Nessuna ricarica possibile.")
                        return None

                    if battery_level == 100:
                        logger.info("🔋 Batteria al 100%! Imposto STATE a OFF.")
                        try:
                            # Legge la configurazione attuale
                            with open("config.json", "r") as f:
                                config = json.load(f)

                            # Modifica il parametro STATE
                            config["STATE"] = "OFF"

                            # Riscrive la configurazione
                            with open("config.json", "w") as f:
                                json.dump(config, f, indent=2)

                            logger.info("✅ Configurazione aggiornata: STATE impostato a OFF.")
                        except Exception as e:
                            logger.error(f"❌ Errore aggiornando config.json: {e}")
                        
                        # NON mandare comandi se batteria è già carica
                        logger.info("🔋 Batteria già carica al 100%! Nessun comando inviato.")
                        return None    

                    return data
                else:
                    logger.debug(f"🕵️‍♂️ Dati ricevuti ma incompleti. Nome: {vehicle_name}, Corrente: {actual_current}, Latch: {port_latch}")
        else:
            logger.warning("⚠️ Nessuna risposta valida dal veicolo.")
            logger.debug(f"📦 Risposta grezza ricevuta: {data}")

        if attempt < max_attempts:
            logger.info("🚨 Invio comando 'wake_up' via SSH...")
            await run_remote_command("wake_up")
            await asyncio.sleep(delay)
        else:
            logger.error("❌ Tentativi esauriti. Impossibile ottenere dati dal veicolo.")
            return None


async def get_vehicle_data():
    token = get_access_token_from_file()
    if not token:
        logger.error("❌ Token non disponibile. Impossibile fare richiesta a Tesla.")
        return None

    url = "https://fleet-api.prd.eu.vn.cloud.tesla.com/api/1/vehicles/LRW3E7FA9MC345603/vehicle_data"
    headers = {"Authorization": f"Bearer {token}"}
    #logger.debug(f"🔑 Access token (inizio): {token[:40]}...")

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

def get_last_logged_difference():
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


async def safety_check_tesla():
    logger.info("🔁 Avvio controllo di sicurezza Tesla...")

    differenza = get_last_logged_difference()
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
    
        
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
    
    
    


#if __name__ == "__main__":
#    asyncio.run(check_and_charge_tesla())