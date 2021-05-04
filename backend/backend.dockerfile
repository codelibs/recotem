FROM python:3.8
WORKDIR /app/
COPY requirements.txt manage.py /app/
RUN pip install -r requirements.txt
COPY recotem/ /app/recotem
CMD ["python", "manage.py", "runserver"]