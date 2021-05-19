FROM python:3.8
WORKDIR /app/
COPY requirements.txt /app/
RUN pip install -r requirements.txt
COPY recotem/ /app
EXPOSE 80
CMD ["/app/celery.sh"]
