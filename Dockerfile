FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source (exclude local-only files)
COPY server.py simulator.py cloud_bot.py strategy.py exchange.py config.py ./

# Railway injects PORT env var
ENV PORT=8000

EXPOSE 8000

CMD ["python", "server.py"]
