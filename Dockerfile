# Use the official lightweight Python image.
# https://hub.docker.com/_/python
FROM python:3.11-slim

# Allow statements and log messages to immediately appear in the Knative logs
ENV PYTHONUNBUFFERED True
ENV PORT 8080

# Set the working directory
WORKDIR /app

# Install system dependencies (needed for psycopg2 if compiling from source, but we use binary so usually fine)
RUN apt-get update && apt-get install -y libpq-dev && rm -rf /var/lib/apt/lists/*

# Copy local code to the container image
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the web service on container startup
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 0 app:app
