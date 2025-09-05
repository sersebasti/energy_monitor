# tesla_proxy.py
import os, json, ssl, aiohttp, inspect

class TeslaProxy:
    """
    Incapsula:
      - lettura token da file
      - verifica stato veicolo (eccetto 'wake_up')
      - refresh token ONE-SHOT se scaduto/invalid
      - wake_up se veicolo 'unavailable'
      - normalizzazione 'charge_start' se latch engaged & charging_state='Stopped'
      - POST al proxy con SSL
    Le funzioni esterne get_vehicle_data/refresh_token/log_pretty sono iniettate dal chiamante.
    """
    def __init__(self, vin, proxy_base, token_file, cert_path, logger,
                 get_vehicle_data, refresh_token, log_pretty=None):
        self.vin = vin
        self.proxy_base = proxy_base.rstrip("/")
        self.token_file = token_file
        self.cert_path = cert_path
        self.logger = logger
        self.get_vehicle_data = get_vehicle_data          # async: (token) -> (status, data)
        self.refresh_token = refresh_token                # sync o async: () -> bool
        self.log_pretty = log_pretty                      # opzionale: (dict) -> None

    async def execute(self, command, charging_amps_value=None):
        max_refresh_retries = 1
        refresh_attempts = 0

        while True:
            access_token = self._load_access_token()
            if not access_token:
                return {"status": "error", "message": "Token file non trovato o access_token mancante"}

            # Pre-check stato veicolo per tutti i comandi tranne wake_up
            if command != "wake_up":
                status, data = await self.get_vehicle_data(access_token)
                ok, action, normalized_command, error_msg = self._evaluate_vehicle_state(status, data, command)

                if action == "refresh_token":
                    if refresh_attempts < max_refresh_retries:
                        ok_refresh = await self._maybe_async(self.refresh_token)
                        if ok_refresh:
                            refresh_attempts += 1
                            self.logger.info("✅ Token aggiornato. Ritento comando.")
                            continue
                    self.logger.error("❌ Token non valido anche dopo refresh.")
                    return {"status": "error", "message": "Token non valido anche dopo il refresh"}

                if action == "wake_up":
                    self.logger.warning("❌ Veicolo non disponibile. Invio wake_up...")
                    return await self._post_command("wake_up", access_token)

                if not ok:
                    return {"status": "error", "message": error_msg}

                command = normalized_command

            # Payload per set_charging_amps
            payload = {}
            if command == "set_charging_amps" and charging_amps_value is not None:
                payload = {"charging_amps": int(charging_amps_value)}

            return await self._post_command(command, access_token, payload)

    # -------------------- Helpers interni --------------------

    def _load_access_token(self):
        if not os.path.exists(self.token_file):
            self.logger.error("❌ Token file non trovato.")
            return None
        try:
            with open(self.token_file) as f:
                data = json.load(f)
            token = data.get("access_token")
            if not token:
                self.logger.error("❌ Access token mancante nel file token.")
            return token
        except Exception as e:
            self.logger.error(f"❌ Impossibile leggere il token: {e}")
            return None

    def _evaluate_vehicle_state(self, status, data, command):
        """
        Normalizza e valida la risposta di get_vehicle_data.
        Ritorna: (ok:bool, action:str|None, normalized_command:str, error_msg:str|None)
        action ∈ { None, 'refresh_token', 'wake_up' }
        """
        # Coercion JSON
        if isinstance(data, str):
            try:
                import json as _json
                data = _json.loads(data)
            except Exception as e:
                self.logger.error(f"❌ JSON non valido: {e}")
                return (False, None, command, "Risposta non valida")
        elif not isinstance(data, dict):
            self.logger.error("❌ Tipo di risposta non riconosciuto.")
            return (False, None, command, "Tipo di risposta non riconosciuto")

        data_str = json.dumps(data).lower()

        # Token scaduto/non valido (consideriamo solo se status != 200 come nel tuo codice)
        if ("token expired" in data_str or "invalid bearer token" in data_str) and status != 200:
            self.logger.info("🔄 Token non valido o scaduto. Avvio refresh...")
            return (False, "refresh_token", command, "Token non valido/scaduto")

        # Veicolo offline/unavailable
        if "vehicle unavailable" in data_str and status != 200:
            return (False, "wake_up", command, "Veicolo non disponibile")

        # Altri errori HTTP
        if status != 200:
            self.logger.error(f"❌ Errore da get_vehicle_data: {status}")
            return (False, None, command, f"Errore get_vehicle_data {status}")

        # Log “pretty” opzionale
        if self.log_pretty:
            try:
                self.log_pretty(data)
            except Exception:
                pass

        # Normalizzazione stato di carica / porta
        vehicle = data.get("response", {})
        charge = vehicle.get("charge_state", {})

        if charge.get('charge_port_door_open') and charge.get('charge_port_latch') == "Engaged":
            self.logger.info("🔌 Sportello ricarica aperto e connettore agganciato.")
            if charge.get('charging_state') == "Stopped":
                self.logger.info("🔋 Ricarica interrotta con connettore agganciato → uso 'charge_start'.")
                command = "charge_start"
            elif charge.get('charging_state') == "Charging":
                self.logger.info("✅ Ricarica in corso.")
            else:
                self.logger.info("ℹ️ Connettore agganciato ma ricarica non in corso.")
                return (False, None, command, "Ricarica non attiva con connettore agganciato")
        else:
            self.logger.info("ℹ️ Connettore non agganciato o sportello chiuso.")
            return (False, None, command, "Sportello chiuso o connettore non agganciato")

        return (True, None, command, None)

    async def _post_command(self, command, access_token, payload=None):
        payload = payload or {}
        url = f"{self.proxy_base}/{self.vin}/command/{command}"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        self.logger.info(f"🚀 Invio comando '{command}' al proxy Tesla...")

        ssl_ctx = ssl.create_default_context(cafile=self.cert_path)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, ssl=ssl_ctx) as resp:
                    status = resp.status
                    text = await resp.text()

                    try:
                        data_resp = json.loads(text)
                    except Exception:
                        self.logger.error(f"❌ Risposta non valida: {text}")
                        return {"status": "error", "message": f"Risposta non valida: {text}"}

                    if status == 200:
                        self.logger.info(f"✅ Comando '{command}' eseguito con successo.")
                        # self.logger.debug("📦 Risposta JSON:\n%s", json.dumps(data_resp, indent=2))
                        return {"status": "success", "data": data_resp}
                    else:
                        self.logger.error(f"❌ Errore comando '{command}': {status} - {text}")
                        return {"status": "error", "message": f"Errore comando '{command}': {status} - {text}"}
        except Exception as e:
            self.logger.error(f"❌ Eccezione durante la richiesta: {e}")
            return {"status": "error", "message": str(e)}

    async def _maybe_async(self, fn):
        """Accetta funzione sync o async, ritorna bool (successo)."""
        try:
            res = fn()
            if inspect.isawaitable(res):
                res = await res
            return bool(res)
        except Exception as e:
            self.logger.error(f"❌ Errore in refresh_token: {e}")
            return False
