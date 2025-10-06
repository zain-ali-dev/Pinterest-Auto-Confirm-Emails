FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files
COPY . .

# Expose Flask port
EXPOSE 5000

# Add curl for healthcheck
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Health check for Coolify
HEALTHCHECK CMD curl --fail http://localhost:5000/health || exit 1

# Start the app
CMD ["python", "app.py"]
