FROM python:3.11-slim-bookworm

ARG PIPER_SINHALA_MODEL_REVISION=18cd56d74b4860714193224d8a333ebb6f2958c4

# Set the working directory
WORKDIR /app

# Install dependencies separately so source changes do not repeat package downloads.
COPY requirements.txt /app/requirements.txt

# Upgrade pip and install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install the Sinhala phonemizer and provision the pinned Piper model at build time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends espeak-ng \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app/models \
    && python -c "import urllib.request; base='https://huggingface.co/chan4lk/piper-tts-sinhala/resolve/${PIPER_SINHALA_MODEL_REVISION}/'; files=('si_LK-sinhala-medium.onnx','si_LK-sinhala-medium.onnx.json','MODEL_CARD.md'); [urllib.request.urlretrieve(base + name, '/app/models/' + name) for name in files]"

# Copy the application into the container at /app
COPY . /app

# Expose port 5000 for the Flask application
EXPOSE 5000

# Define the command to run the Flask application using Gunicorn
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:5000", "-w", "4"]