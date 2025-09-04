import os
import io
import json
import ssl
from unittest import result
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
ESP8266_IP = CONFIG["ESP8266_IP"]
ESP8266_NAME = CONFIG["ESP8266_NAME"]
ESP32_IP_1 = CONFIG["ESP32_IP_1"]
ESP32_MAC_1 = CONFIG["ESP32_MAC_1"]
    
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


#def is_token_file(filename):
#    return filename.startswith("tesla_token_") and filename.endswith(".json")

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


async def get_vehicle_data(access_token: str):

    url = f"https://fleet-api.prd.eu.vn.cloud.tesla.com/api/1/vehicles/{VIN}/vehicle_data"

    headers = {
        "Authorization": f"Bearer {access_token}"
    }
        

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                data = await resp.text()
                logger.info(f"📡 Risposta get_vehicle_data HTTP: {resp.status}")
                logger.debug(f"📥 Contenuto completo (raw):\n{data}")
                return resp.status, data

        except Exception as e:
            logger.error(f"❌ Errore durante richiesta vehicle_data: {e}")
            return None


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


def fetch_shelly_data():
    if not SHELLY_IP:
        logger.error("❌ Indirizzo IP di Shelly non configurato.")
        return default_shelly_data()
    
    url = f"http://{SHELLY_IP}/status"
    logger.info(f"Richiesta a Shelly: {url}")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Risposta Shelly: {response.status_code}")
        return data.get("emeters", [])
    except requests.RequestException as e:
        logger.error(f"Errore nella richiesta a Shelly: {e}")
        logger.error("⚠️ Utilizzo dati di default per Shelly:\n" + json.dumps(default_shelly_data(), indent=2))
        return default_shelly_data()

def default_shelly_data():
    return [
        {
            "power": 0.0,            # Fase 1 - Produzione (PV)
            "voltage": 230.0,
            "current": 0.0,
            "is_valid": False
        },
        {
            "power": 3000.0,         # Fase 2 - Assorbimento da rete (grid)
            "voltage": 230.0,
            "current": 13.0,
            "is_valid": False
        },
        {
            "power": 0.0,            # Fase 3 - Non utilizzata o riserva
            "voltage": 230.0,
            "current": 0.0,
            "is_valid": False
        }
    ]


def fetch_esp8266_data():

    url = f"http://{ESP8266_IP}/status"
    logger.info(f"Richiesta all'ESP8266: {url}")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Risposta ESP8266: {response.status_code}")

        if data.get("status") != "ok":
            logger.warning(f"⚠️ ESP8266 ha risposto ma con stato: {data.get('status')}")
            return None

        return data

    except requests.RequestException as e:
        logger.error(f"Errore nella richiesta all'ESP8266: {e}")
        return None



def fetch_esp32_data():

    if not ESP32_IP_1:
        logger.error("❌ Indirizzo IP di ESP32 non configurato.")
        return None

    url = f"http://{ESP32_IP_1}/amps?n=1600&sr=4000"
    logger.info(f"Richiesta all'ESP32: {url}")

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        #logger.info(f"Risposta ESP32: {response.status_code}")

        if response.status_code != 200:
            logger.warning(f"⚠️ ESP32 ha risposto con stato: {response.status_code}")
            return None
        
        return data

    except requests.RequestException as e:
        logger.error(f"Errore nella richiesta all'ESP32: {e}")
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
    """Verifica se l'IP appartiene a un dispositivo Shelly (controlla il campo 'mac')."""
    try:
        response = requests.get(f"http://{ip}/status", timeout=1)
        if response.status_code == 200:
            data = response.json()
            mac = data.get("mac", "").upper()
            if mac == SHELLY_MAC:
                return True
    except requests.RequestException as e:
        logger.debug(f"[Shelly] Nessuna risposta valida da {ip}: {e}")
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
            with open(config_path, "w") as f:
                json.dump(CONFIG, f, indent=2)
            logger.info(f"Shelly trovato all'indirizzo: {new_ip}")
        else:
            logger.error("Dispositivo Shelly non trovato sulla rete.")
    else:
        logger.info(f"Shelly già configurato all'indirizzo: {SHELLY_IP}")


def verify_and_update_esp32_mac_ip(ESP32_IP, ESP32_MAC):

    if not is_esp_mac_ip(ESP32_IP, ESP32_MAC):
        new_ip = find_esp_mac_ip(ESP32_MAC)
        if new_ip:
            logger.info(f"Trovato ESP32 con MAC: {ESP32_MAC} e IP: {new_ip}")
            return new_ip
        else:
            logger.error(f"Dispositivo con MAC: {ESP32_MAC} non trovato sulla rete.")
            return 0
    else:
        logger.info(f"Dispositivo con MAC: {ESP32_MAC} già configurato all'indirizzo: {ESP32_IP}")
        return 1


# ---- ESP8266 ----
def is_esp_mac_ip(ESP32_IP, ESP32_MAC):
    try:
        response = requests.get(f"http://{ESP32_IP}/status", timeout=2)
        if response.status_code == 200:
            data = response.json()
            mac = data.get("mac_sta", "").lower()
            if mac == ESP32_MAC.lower():
                return True
    except requests.RequestException as e:
        logger.debug(f"[ESP32] Nessuna risposta valida da {ESP32_IP}: {e}")
    return False

def find_esp_mac_ip(ESP32_MAC):
    for i in range(1, 255):
        IP = f"192.168.1.{i}"
        if is_esp_mac_ip(IP, ESP32_MAC):
            return IP
    return None


# ---- ESP8266 ----
def is_esp8266_ip(ip):
    """Verifica se l'IP appartiene al tuo ESP8266 personalizzato (campo 'name' == 'tesla_esp')."""
    try:
        response = requests.get(f"http://{ip}/status?token=Merca10tello", timeout=1)
        if response.status_code == 200:
            data = response.json()
            name = data.get("name", "").lower()
            if name == ESP8266_NAME.lower():
                return True
    except requests.RequestException as e:
        logger.debug(f"[ESP8266] Nessuna risposta valida da {ip}: {e}")
    return False
 

def find_esp8266_ip():
    for i in range(1, 255):
        ip = f"192.168.1.{i}"
        if is_esp8266_ip(ip):
            return ip
    return None

def verify_and_update_esp8266_ip():
    global ESP8266_IP
    if not is_esp8266_ip(ESP8266_IP):
        new_ip = find_esp8266_ip()
        if new_ip:
            CONFIG["ESP8266_IP"] = new_ip
            ESP8266_IP = new_ip
            with open(config_path, "w") as f:
                json.dump(CONFIG, f, indent=2)
            logger.info(f"ESP8266 trovato all'indirizzo: {new_ip}")
        else:
            logger.error("Dispositivo ESP8266 non trovato sulla rete.")
    else:
        logger.info(f"ESP8266 già configurato all'indirizzo: {ESP8266_IP}")
        
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


def process_shelly_phases(data):
    if len(data) < 2:
        raise ValueError("Incomplete data: at least phase 1 (PV) and phase 2 (grid) are required.")

    pv_power = data[0]['power']           # Solar production (usually >= 0)
    grid_power = data[1]['power']         # Positive = importing, Negative = exporting

    house_consumption = pv_power + grid_power  # Real house consumption

    return {
        'solar_production': pv_power,
        'grid_power': grid_power,
        'house_consumption': house_consumption,
        'pv_current': data[0]['current'],
        'grid_current': data[1]['current'],
        'pv_voltage': data[0]['voltage'],
        'grid_voltage': data[1]['voltage'],
        'measurements_valid': data[0]['is_valid'] and data[1]['is_valid']
    }                        

async def shelly_logger():
    if not SHELLY_IP or not ESP8266_IP:
        logger.error("❌ Indirizzi IP di Shelly o ESP8266 non configurati. Verifica il file di configurazione.")
        return

    

    period = 30  # secondi tra i cicli di polling

    while True:


        verify_and_update_shelly_ip()
        verify_and_update_esp8266_ip()

        # Verifica e aggiorna ESP32 
        global ESP32_IP_1
        global ESP32_MAC_1
        # result = verify_and_update_esp32_mac_ip(ESP32_IP_1, ESP32_MAC_1)
        # if result == 0:
        #     logger.error(f"❌ Non torvato indirizzo IP di ESP32 con MAC: {ESP32_MAC_1}")
        #     #return
        #     pass
        # elif result == 1:
        #     logger.info(f"Dispositivo con MAC: {ESP32_MAC_1} trovato all'indirizzo IP già configurato: {ESP32_IP_1}")
        #     pass
        # else:
        #     ESP32_IP_1 = result
        #     CONFIG["ESP32_IP_1"] = ESP32_IP_1
        #     with open(config_path, "w") as f:
        #         json.dump(CONFIG, f, indent=2)
        #         logger.info(f"ESP32 trovato nuovo indirizzo IP: {ESP32_IP_1}. Aggiornato file config.json")    

        # esp32_data = fetch_esp32_data()
        # if esp32_data:
        #     tesla_amps_esp32 = esp32_data.get("amps_rms")
        #     tesla_amps_esp32_int = round(tesla_amps_esp32) if tesla_amps_esp32 > 5.5 else 0
        #     logger.info(f"⚡ Corrente letta da ESP32: {tesla_amps_esp32:.3f} A")
        #     logger.info(f"⚡ Corrente letta da ESP32 (intero): {tesla_amps_esp32_int} A")


        esp8266_data = fetch_esp8266_data()
        if esp8266_data:
            tesla_amps = esp8266_data.get("irms_A")
            tesla_amps_int = round(tesla_amps) if tesla_amps > 5.5 else 0
            logger.info(f"⚡ Corrente letta da ESP8266: {tesla_amps:.3f} A")
            logger.info(f"⚡ Corrente letta da ESP8266 (intero): {tesla_amps_int} A")
            insert_tesla_status(tesla_amps_int)
        else:
            logger.warning("📡 Nessun dato ricevuto dall'ESP8266.")

        shelly_data = fetch_shelly_data()
        shelly_data_processed = process_shelly_phases(shelly_data)

        grid_voltage = shelly_data_processed["grid_voltage"]

        conf = get_conf()
        STATE = conf["STATE"]
        MAX_ENERGY_PRELEVABILE = float(conf["MAX_ENERGY_PRELEVABILE"])

        logger.info(f"⚙️ Stato configurazione: {STATE}")
        logger.info(f"⚡ Max energia prelevabile: {MAX_ENERGY_PRELEVABILE} W")

        if shelly_data and esp8266_data:
            logger.info("✅ Dati Shelly e ESP8266 acquisiti.")
            store_data_in_db(shelly_data)
            insert_tesla_status(tesla_amps_int)
            logger.info("✅ Dati Shelly e ESP8266 salvati correttamente.")
        else:
            logger.warning(f"⚠️ Dati Shelly o ESP8266 non disponibili. Riprovo tra {period} secondi...")
            await asyncio.sleep(period)
            continue


        if STATE == "ON":
            tesla_power_draw = tesla_amps_int * grid_voltage
            grid_power = shelly_data_processed["grid_power"]

            logger.info(f"⚡ Potenza prelevata da Enel: {grid_power} W")
            logger.info(f"⚡ Potenza assorbita da Tesla: {tesla_power_draw} W")

            max_allowed_amps = 0
            for amps in range(13, 5, -1):
                total_power = amps * grid_voltage + grid_power
                if total_power < MAX_ENERGY_PRELEVABILE:
                    max_allowed_amps = amps
                    break

            logger.info(f"🔧 Max corrente consentita: {max_allowed_amps} A")

            if max_allowed_amps == tesla_amps_int:
                logger.info(f"✅ Corrente Tesla già impostata a {tesla_amps_int} A. Nessuna azione necessaria.")
            elif max_allowed_amps == 0:
                logger.warning(f"⚠️ Nessuna corrente impostabile trovata che rispetti il limite di {MAX_ENERGY_PRELEVABILE} W.")
                logger.info("🔴 Invio comando charge_stop.")
                result_charge_stop = await run_tesla_command("charge_stop")
                if result_charge_stop.get("status") == "error":
                    logger.error("❌ Errore inviando il comando charge_stop.")
                    set_conf("STATE", "OFF")
                    logger.error("🛑 Sistema disattivato: STATE = OFF")
            else:
                if tesla_amps_int == 0:
                    logger.info(f"🔌 Corrente Tesla attuale = 0 A. Corrente da impostare: {max_allowed_amps} A")
                    logger.info("🔴 Invio comando charge_start.")
                    result_charge_start = await run_tesla_command("charge_start", retried=False)
                    if result_charge_start.get("status") == "error":
                        logger.error("❌ Errore inviando il comando charge_start.")
                        set_conf("STATE", "OFF")
                        logger.error("🛑 Sistema disattivato: STATE = OFF")

                else:
                    logger.info(f"🔌 Corrente Tesla attuale = {tesla_amps_int} A. Corrente da impostare: {max_allowed_amps} A")
                    logger.info("🔴 Invio comando set_charging_amps.")
                    result_set_charging_amps = await run_tesla_command("set_charging_amps", max_allowed_amps)
                    if result_set_charging_amps.get("status") == "error":
                        logger.error(f"❌ Errore inviando il comando set_charging_amps {max_allowed_amps} A.")
                        set_conf("STATE", "OFF")
                        logger.error("🛑 Sistema disattivato: STATE = OFF")
        else:
            logger.info("🚫 Stato = OFF. Sistema gestione ricarica disattivato. Nessun comando verrà inviato.")

        await asyncio.sleep(period)                     


async def run_tesla_command(command, charging_amps_value=None, retried=False):

    logger.info(f"🔧 Esecuzione comando Tesla: {command} (charging_amps_value={charging_amps_value}, retried={retried})"    
                )
    TESLA_TOKEN_FILE = "/app/data/tesla_token_latest.json"
    CERT_PATH = "/app/tesla-proxy-config/cert.pem"
    PROXY_URL_BASE = "https://tesla_http_proxy:4443/api/1/vehicles"

    # 📂 Verifica file token
    if not os.path.exists(TESLA_TOKEN_FILE):
        logger.error("❌ Token file non trovato.")
        return {"status": "error", "message": "Token file non trovato"}

    with open(TESLA_TOKEN_FILE) as f:
        token_data = json.load(f)
    access_token = token_data.get("access_token")

    if not access_token:
        logger.error("❌ Access token mancante.")
        return {"status": "error", "message": "Access token mancante"}

    # 📡 Preparazione URL e header
    url = f"{PROXY_URL_BASE}/{VIN}/command/{command}"
    payload = {"charging_amps": int(charging_amps_value)} if command == "set_charging_amps" and charging_amps_value is not None else {}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # 🔎 Esegui verifica stato veicolo (eccetto per 'wake_up')
    if command != "wake_up":
        status, data = await get_vehicle_data(access_token)

        # 🧼 Gestione data: può essere già dict o stringa JSON
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as e:
                logger.error(f"❌ JSON non valido: {e}")
                return {"status": "error", "message": "Risposta non valida"}
        elif not isinstance(data, dict):
            logger.error("❌ Tipo di risposta non riconosciuto.")
            return {"status": "error", "message": "Tipo di risposta non riconosciuto"}

        data_str = json.dumps(data).lower()

        # 🔐 Token scaduto
        if ("token expired" in data_str or "invalid bearer token" in data_str) and status != 200:
            if not retried:
                logger.info("🔄 Token non valido o scaduto. Avvio refresh...")
                if refresh_token():
                    logger.info("✅ Token aggiornato. Ritento comando.")
                    return await run_tesla_command(command, charging_amps_value, retried=True)
                else:
                    logger.error("❌ Refresh token fallito.")
                    return {"status": "error", "message": "Impossibile aggiornare il token"}
            else:
                logger.error("❌ Token ancora non valido dopo il refresh.")
                log_dict_pretty(data)
                return {"status": "error", "message": "Token non valido anche dopo il refresh"}

        # 💤 Veicolo offline
        if "vehicle unavailable" in data_str and status != 200:
            logger.warning("❌ Veicolo non disponibile o offline.")
            logger.info("🚗 Invio comando 'wake_up'...")
            return await run_tesla_command("wake_up")

        # ❌ Altro errore
        if status != 200:
            logger.error(f"❌ Errore da get_vehicle_data: {status}")
            return {"status": "error", "message": f"Errore get_vehicle_data {status}"}

        # ✅ Risposta valida: stampa dati veicolo
        logger.info("📦 Dati veicolo:")
        log_dict_pretty(data)

        vehicle = data.get("response", {})
        charge = vehicle.get("charge_state", {})

        # 🧲 Controllo stato ricarica

        if charge.get('charge_port_door_open') and charge.get('charge_port_latch') == "Engaged":
            logger.info("🔌 Sportello ricarica aperto e connettore agganciato.")

            if charge.get('charging_state') == "Stopped":
                
                logger.info("🔋 Ricarica interrotta con connettore agganciato. Provo a dare comando charge_start")
                command = "charge_start"

            elif charge.get('charging_state') == "Charging":
                logger.info("✅ Ricarica in corso.")
                
            else:
                logger.info("ℹ️ Connettore agganciato ma ricarica non in corso.")
                return {"status": "error", "message": "Ricarica non attiva con connettore agganciato"}

        else:
            logger.info("ℹ️ Connettore non agganciato o sportello chiuso.")
            return {"status": "error", "message": "Sportello chiuso o connettore non agganciato"}

    # 🚀 Esecuzione comando POST al proxy
    logger.info(f"🚀 Invio comando '{command}' al proxy Tesla...")
    ssl_ctx = ssl.create_default_context(cafile=CERT_PATH)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=headers, json=payload, ssl=ssl_ctx) as resp:
                status = resp.status
                text = await resp.text()

                try:
                    data_resp = json.loads(text)
                except json.JSONDecodeError:
                    logger.error(f"❌ Risposta non valida: {text}")
                    return {"status": "error", "message": f"Risposta non valida: {text}"}

                if status == 200:
                    logger.info(f"✅ Comando '{command}' eseguito con successo.")
                    logger.debug(f"📦 Risposta JSON:\n{json.dumps(data_resp, indent=2)}")
                    return {"status": "success", "data": data_resp}
                else:
                    logger.error(f"❌ Errore comando '{command}': {status} - {text}")
                    return {"status": "error", "message": f"Errore comando '{command}': {status} - {text}"}
        except Exception as e:
            logger.error(f"❌ Eccezione durante la richiesta: {e}")
            return {"status": "error", "message": str(e)}   
        

def log_dict_pretty(d, prefix="", level=0):
    indent = "  " * level
    if isinstance(d, dict):
        for key, value in d.items():
            if isinstance(value, (dict, list)):
                logger.info(f"{indent}🔸 {prefix}{key}:")
                log_dict_pretty(value, "", level + 1)
            else:
                logger.info(f"{indent}🔹 {prefix}{key}: {value}")
    elif isinstance(d, list):
        for i, item in enumerate(d):
            logger.info(f"{indent}🔸 {prefix}[{i}]:")
            log_dict_pretty(item, "", level + 1)
    else:
        logger.info(f"{indent}🔹 {prefix}{d}")

VOLTAGE_IP = "192.168.1.2"
VOLTAGE_URL = f"http://{VOLTAGE_IP}/voltage"

async def voltage_logger_loop():
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(VOLTAGE_URL, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        voltage = data.get("voltage")
                        name = data.get("name", "sconosciuto")

                        print(f"🔌 Tensione da {name}: {voltage:.2f} V")

                        # Inserimento nel database
                        conn, cursor = get_db_connection()
                        if conn and cursor:
                            try:
                                cursor.execute(
                                    "INSERT INTO litum_battery (voltage, sent_by) VALUES (%s, %s)",
                                    (voltage, name)
                                )
                                conn.commit()
                            except Exception as db_err:
                                print(f"❌ Errore durante INSERT: {db_err}")
                            finally:
                                cursor.close()
                                conn.close()
                    else:
                        print(f"⚠️ Risposta HTTP non OK: {resp.status}")
            except Exception as e:
                print(f"❌ Errore richiesta: {e}")

            await asyncio.sleep(30)