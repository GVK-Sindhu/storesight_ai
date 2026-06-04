
FROM python:3.12-slim-bookworm

# Set work directory
WORKDIR /store-intelligence

# Install system dependencies for OpenCV (even headless requires some basic glib dependencies)
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY app/ ./app/
COPY dashboard/ ./dashboard/
COPY pipeline/ ./pipeline/
# Copy metadata datasets
COPY "Brigade Road - Store layoutc5f5d56.xlsx" .
COPY "Brigade_Bangalore_10_April_26 (1)bc6219c.csv" .
COPY updated_problemstatement/ ./updated_problemstatement/

# Create data folder for database and events
RUN mkdir -p data

# Environment variables
ENV PYTHONPATH=/store-intelligence/app

# Port configuration
EXPOSE 8000
EXPOSE 8501

# Default action is to run API, but compose can override this
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
