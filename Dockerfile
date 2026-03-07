# Dockerfile for Bid-Assist

# Use official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set Python path to include src directory
ENV PYTHONPATH=/app

# Run the application
CMD ["python", "run.py"]
