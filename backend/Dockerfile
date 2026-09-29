# Build from the REPOSITORY ROOT so the local segmentation scripts can be copied in:
#   docker build -f backend/Dockerfile -t ui-agent-backend .
#   docker build -f backend/Dockerfile --build-arg LOCAL_CV=1 -t ui-agent-backend-cv .   # models in-process
FROM python:3.11-slim

ARG LOCAL_CV=0
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PORT=8000

WORKDIR /srv/backend

# libgl/libglib are needed by OpenCV only in the LOCAL_CV image.
RUN if [ "$LOCAL_CV" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*; \
    fi

COPY backend/requirements.txt backend/requirements-local-cv.txt ./
RUN if [ "$LOCAL_CV" = "1" ]; then pip install -r requirements-local-cv.txt; else pip install -r requirements.txt; fi

COPY backend/app ./app
COPY backend/flows ./flows
COPY tap-to-flow-pipeline/3_ui_segmentation /srv/tap-to-flow-pipeline/3_ui_segmentation
# Voice assistant, mounted at /api/voice and /mobile/ (only the code, UI and seed workflow memory).
COPY assistant/voice_assistant_app /srv/assistant/voice_assistant_app
COPY assistant/mobile_assistant_app/assets /srv/assistant/mobile_assistant_app/assets

ENV LOCAL_SEGMENTATION_DIR=/srv/tap-to-flow-pipeline/3_ui_segmentation     VOICE_ASSISTANT_DIR=/srv/assistant
RUN useradd --create-home app && chown -R app /srv
USER app

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
