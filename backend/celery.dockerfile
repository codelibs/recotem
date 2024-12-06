FROM python:3.10
WORKDIR /app/
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY recotem/ /app
EXPOSE 80
CMD ["/app/celery.sh"]
