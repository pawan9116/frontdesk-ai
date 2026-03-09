FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY prompts/ prompts/

# Download turn detector model files
RUN python src/agent.py download-files

EXPOSE 8000

CMD ["python", "src/agent.py", "start"]
