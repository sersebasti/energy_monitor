import os
import time
import requests
import mysql.connector

# Ottieni l'IP del dispositivo Shelly dalle variabili d'ambiente
#SHELLY_IP = os.getenv("SHELLY_IP", "192.168.11.208")
SHELLY_IP = os.getenv("SHELLY_IP", "shelly_device") 
#SHELLY_IP = "shelly_device"

def connect_to_db():
    """Crea la connessione al database MySQL."""
    return mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "shelly_mysql"),  # Nome del servizio MySQL nel docker-compose
        user=os.getenv("MYSQL_USER", "myuser"),
        password=os.getenv("MYSQL_PASSWORD", "mypassword"),
        database=os.getenv("MYSQL_DATABASE", "dati")
    )

def fetch_shelly_data():
   

    url = f"http://{SHELLY_IP}/status"
    print(f"Richiesta a Shelly: {url}")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("emeters", [])  # Estratto direttamente dalla API locale
    except requests.RequestException as e:
        print(f"Errore nella richiesta a Shelly: {e}")
        return None

def store_data_in_db(emeters):
    """Salva i dati di tutte le fasi in un'unica riga nel database MySQL."""
    db = connect_to_db()
    cursor = db.cursor()

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

    cursor.execute(query, tuple(values))
    db.commit()
    cursor.close()
    db.close()
    print("Dati Shelly salvati in una sola riga.")


def main():
    """Ciclo che raccoglie i dati periodicamente."""
    interval = 60  # Tempo in secondi tra le richieste
    while True:
        emeters = fetch_shelly_data()
        if emeters:
            store_data_in_db(emeters)
        time.sleep(interval)

if __name__ == "__main__":
    main()