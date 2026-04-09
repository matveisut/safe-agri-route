FROM python:3.12-slim

# Install system dependencies for psycopg2 and postgis
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        gdal-bin \
        libgdal-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ensure standard output is not buffered
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# The application code will be mounted as a volume in development,
# but it's good practice to copy it anyway for possible production builds.
COPY . .

# Setting the command in docker-compose.yml for local development
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
