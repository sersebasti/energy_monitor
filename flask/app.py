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

from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify, Response
from datetime import datetime
from mysql.connector import Error # type: ignore


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


async def ensure_vehicle_awake(max_attempts=3, delay=10):
    for attempt in range(1, max_attempts + 1):
        logger.info(f"🔍 Tentativo {attempt}/{max_attempts} per ottenere dati dal veicolo...")
        data = await get_vehicle_data()

        if data and isinstance(data, dict):
            response = data.get("response", {})
            vehicle_name = response.get("vehicle_state", {}).get("vehicle_name")
            charge_state = response.get("charge_state", {})
            actual_current = charge_state.get("charger_actual_current")
            port_latch = charge_state.get("charge_port_latch")

            # Log dettagliato con lo stato del cavo
            if vehicle_name is not None and actual_current is not None:
                cable_status = "collegato ✅" if port_latch == "Engaged" else "non collegato ❌"
                logger.info(f"✅ Veicolo online: nome = {vehicle_name}, corrente = {actual_current} A, cavo = {cable_status}")

                if port_latch != "Engaged":
                    logger.warning("🔌 Cavo non collegato! Nessuna ricarica possibile.")
                    return None

                return data

            else:
                logger.debug(f"🕵️‍♂️ Dati ricevuti ma incompleti. Nome: {vehicle_name}, Corrente: {actual_current}, Latch: {port_latch}")

        else:
            logger.warning("⚠️ Nessuna risposta valida dal veicolo.")

        if attempt < max_attempts:
            logger.info("🚨 Invio comando 'wake_up' via SSH...")
            await run_remote_command("wake_up")
            await asyncio.sleep(delay)
        else:
            logger.error("❌ Tentativi esauriti. Impossibile ottenere dati dal veicolo.")
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

                        charging_amps_to_log = None
                        if isinstance(data.get("output"), list) and data["output"]:
                            inner_output = data["output"][0]
                            try:
                                inner_data = json.loads(inner_output)
                                response = inner_data.get("response")
                                logger.info(f"📦 Risposta Tesla: {response}")

                                if command_sent == "charge_stop":
                                    if (response and response.get("result")) or \
                                       (response and not response.get("result") and "not_charging" in response.get("string", "")):
                                        charging_amps_to_log = 0

                                elif command_sent == "set_charging_amps" and response and response.get("result"):
                                    try:
                                        charging_amps_to_log = int(value)
                                    except Exception:
                                        logger.warning("⚠️ Valore amperaggio non valido per inserimento DB.")

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
    try:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "mysql"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "local"),
            database=os.getenv("MYSQL_DATABASE", "dati")
        )
        cursor = conn.cursor(dictionary=True)

        cursor.callproc("get_media_mobile", [minuti])

        for result in cursor.stored_results():
            row = result.fetchone()
            if row:
                produzione = float(row['media_produzione_foto'])
                assorbimento = float(row['media_assorbimento_casa'])
                differenza = produzione - assorbimento
                timestamp = row['timestamp']

                insert_query = """
                    INSERT INTO log_media_mobile (timestamp, media_produzione_foto, media_assorbimento_casa, differenza)
                    VALUES (%s, %s, %s, %s)
                """
                cursor = conn.cursor()
                cursor.execute(insert_query, (timestamp, produzione, assorbimento, differenza))
                conn.commit()

                logger.info("✅ Inserita media mobile in log_media_mobile.")
            else:
                logger.warning("⚠️ Nessun dato restituito dalla procedura.")
    except Error as e:
        logger.error(f"❌ Errore durante l'inserimento della media mobile: {e}")



def log_last_power_data():
    try:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "mysql"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "local"),
            database=os.getenv("MYSQL_DATABASE", "dati")
        )

        cursor = conn.cursor(dictionary=True)

        # Preleva l'ultima riga inserita nel log
        query = """
            SELECT timestamp, media_produzione_foto, media_assorbimento_casa, differenza
            FROM log_media_mobile
            ORDER BY timestamp DESC
            LIMIT 1
        """
        cursor.execute(query)
        row = cursor.fetchone()

        if row:
            produzione = float(row['media_produzione_foto'])
            assorbimento = float(row['media_assorbimento_casa'])
            differenza = float(row['differenza'])
            timestamp = row['timestamp']

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




async def check_and_charge_tesla():
    differenza = get_last_logged_difference()
    if differenza is None:
        logger.warning("⚠️ Differenza non calcolabile dal DB, nessuna azione eseguita.")
        return

    logger.info(f"📊 Differenza energetica più recente: {differenza:.2f} W")

    # 🔍 Recupera ultimo valore corrente da tesla_status
    try:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "mysql"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "local"),
            database=os.getenv("MYSQL_DATABASE", "dati")
        )
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT charging_amps FROM tesla_status ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        current_amps = row["charging_amps"] if row else 0
        logger.info(f"🔌 Corrente Tesla secondo il DB: {current_amps} A")

    except mysql.connector.Error as e:
        logger.error(f"❌ Errore MySQL nel recupero stato Tesla: {e}")
        return

    # ⚠️ Energia insufficiente → charge_stop se serve
    if differenza < 5 * 220:
        if current_amps == 0:
            logger.info("🛑 Tesla già ferma, nessuna azione necessaria.")
        else:
            logger.info(f"⚠️ Energia insufficiente ({differenza:.2f} W), invio 'charge_stop'")
            result = await run_remote_command("charge_stop")
            if result:
                inner = result.get("output", [{}])[0]
                try:
                    inner_data = json.loads(inner)
                    resp = inner_data.get("response", {})
                    if result["status"] == "success" and (
                        resp.get("result") is True or 
                        resp.get("string") == "car could not execute command: not_charging"
                    ):
                        cursor.execute("INSERT INTO tesla_status (charging_amps) VALUES (0)")
                        conn.commit()
                        logger.info("💾 Stato Tesla aggiornato nel DB (0 A).")
                except Exception as e:
                    logger.warning(f"⚠️ Errore parsing risposta comando stop: {e}")
        cursor.close()
        conn.close()
        return

    # ⚡ Se energia disponibile → aggiorna solo se necessario
    for amps in range(13, 4, -1):
        soglia = amps * 220
        if differenza >= soglia:
            if current_amps == 0:
                logger.info(f"🟢 Energia sufficiente, invio 'charge_start' + set a {amps} A")
                await run_remote_command("charge_start")
            elif current_amps == amps:
                logger.info(f"✅ Amperaggio già impostato a {amps} A. Nessuna azione.")
                break

            logger.info(f"⚡ Energia disponibile, invio 'set_charging_amps' a {amps} A")
            result = await run_remote_command("set_charging_amps", value=str(amps))
            if result and result.get("status") == "success":
                cursor.execute("INSERT INTO tesla_status (charging_amps) VALUES (%s)", (amps,))
                conn.commit()
                logger.info(f"💾 Stato Tesla aggiornato nel DB ({amps} A).")
            break

    cursor.close()
    conn.close()

def get_last_logged_difference():
    try:
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "mysql"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "local"),
            database=os.getenv("MYSQL_DATABASE", "dati")
        )
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT differenza FROM log_media_mobile ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        row["differenza"] = 1200
        return float(row["differenza"]) if row else None
    except Exception as e:
        logger.error(f"❌ Errore nel recupero differenza dal DB: {e}")
        return None




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