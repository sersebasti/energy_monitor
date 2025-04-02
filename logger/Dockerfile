FROM python:3.11

# Imposta la directory di lavoro nel container
WORKDIR /app

# Installa i pacchetti necessari
RUN apt update && apt install -y lm-sensors

# Copia il file requirements.txt e installa le dipendenze Python
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copia gli script nella cartella dell'app
COPY scripts /app/scripts

# Espone la porta per Flask
EXPOSE 5000

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
CMD ["/app/entrypoint.sh"]