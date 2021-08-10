FROM python:3.8
WORKDIR /app/
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt
ARG RECOTEM_TESTING=false
RUN bash -c "if \"$RECOTEM_TESTING\"; then pip install pytest pytest-celery pytest-cov pytest-django && python -c \"from irspack.dataset import MovieLens100KDataManager; MovieLens100KDataManager(force_download=True)\"; fi"

ARG RECOTEM_DEV=false
RUN bash -c "if \"$RECOTEM_DEV\"; then pip install ipython; fi"

COPY recotem/ /app
EXPOSE 80
CMD ["/app/start.sh"]

HEALTHCHECK --interval=5s --timeout=5s --start-period=20s  --retries=10 \
    CMD curl -f http://localhost:80/api/ping/ || exit 1
