#!/bin/bash

echo "Aspetto che MySQL sia disponibile su $MYSQL_HOST..."

until python3 -c "
import mysql.connector
import time
try:
    conn = mysql.connector.connect(
        host='$MYSQL_HOST',
        user='$MYSQL_USER',
        password='$MYSQL_PASSWORD',
        database='$MYSQL_DATABASE'
    )
    conn.close()
except:
    raise SystemExit(1)
"; do
  echo "MySQL non ancora disponibile, riprovo tra 5 secondi..."
  sleep 5
done

echo "MySQL disponibile, lancio lo script Python..."
python3 /app/scripts/fotovoltaico.py