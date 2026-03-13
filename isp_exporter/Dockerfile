FROM python:3.11-slim

WORKDIR /app

# add curl/jq for debugging
RUN apt-get update && apt-get install -y curl jq && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# copy the exporter script from the build context.  since the host
# directory is a mounted Unraid share, an empty or truncated file will result
# in an image containing no code.  fail early if the file is missing or empty.
COPY app.py ./
RUN test -s app.py

ENV UNIFI_API_KEY=""
ENV UNIFI_CONTROLLER=""
ENV UNIFI_API_URL=""
ENV POLL_INTERVAL=60
ENV VERIFY_SSL=false

CMD ["python", "app.py"]
