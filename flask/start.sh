#!/bin/bash

# Avvia logger tensione in background
echo "▶️ Avvio voltage_logger_runner.py..."
python voltage_logger_runner.py &

# Avvia logger Shelly in background
echo "▶️ Avvio shelly_logger.py..."
python shelly_logger.py &

# Avvia il server Flask (in primo piano)
echo "▶️ Avvio Flask..."
flask run --host=0.0.0.0 --port=5000
