FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Ensure storage & export directories exist
RUN mkdir -p /app/dist/web_assets

ENV PORT=8080
EXPOSE 8080

CMD ["python", "main.py"]