FROM python:3.11-slim-bookworm

ARG PIPER_SINHALA_MODEL_REVISION=18cd56d74b4860714193224d8a333ebb6f2958c4
ARG PIPER_ENGLISH_VOICE_REVISION=v1.0.0
ARG PIPER_ENGLISH_VOICE_PATH=en/en_US/lessac/medium/en_US-lessac-medium
# Any CTranslate2 Whisper model on Hugging Face works here (e.g. a Sinhala fine-tune).
ARG WHISPER_MODEL_REPO=Systran/faster-whisper-small

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

# Provision the local Piper English voice so English replies do not use Gemini TTS.
RUN python -c "import urllib.request; base='https://huggingface.co/rhasspy/piper-voices/resolve/${PIPER_ENGLISH_VOICE_REVISION}/${PIPER_ENGLISH_VOICE_PATH}'; [urllib.request.urlretrieve(base + ext, '/app/models/en_US-lessac-medium' + ext) for ext in ('.onnx', '.onnx.json')]"

# Provision the local Whisper speech-to-text model so transcription does not use Gemini.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('${WHISPER_MODEL_REPO}', local_dir='/app/models/whisper')"

# Headless Chromium (plus its system libraries) lets the crawler read websites that build
# their content with JavaScript. Only the small headless shell is installed.
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

# Copy the application into the container at /app
COPY . /app

# Expose port 5000 for the Flask application
EXPOSE 5000

# Define the command to run the Flask application using Gunicorn
# Local speech models run on the CPU, so allow requests longer than gunicorn's 30 s default.
CMD ["gunicorn", "app:app", "-b", "0.0.0.0:5000", "-w", "4", "--timeout", "120"]