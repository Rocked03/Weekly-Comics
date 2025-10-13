# Use official Python image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Set environment variables (override with -e or .env)
ENV PYTHONUNBUFFERED=1

# Default command to run the bot
CMD ["python", "main.py"]
