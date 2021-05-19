FROM python:3.8
WORKDIR /app/
COPY requirements.txt /app/
RUN pip install -r requirements.txt
ARG RECOTEM_TESTING=false
RUN bash -c "if \"$RECOTEM_TESTING\"; then pip install pytest celery[pytest] pytest-cov pytest-django && python -c \"from irspack.dataset import MovieLens100KDataManager; MovieLens100KDataManager(force_download=True)\"; fi"

COPY recotem/ /app
EXPOSE 80
CMD ["/app/start.sh"]
