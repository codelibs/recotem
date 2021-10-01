version: "3.7"
services:
  queue:
    image: bitnami/rabbitmq:3.9
  db:
    image: postgres:12
    volumes:
      - db-data:/var/lib/postgresql/data/pgdata
    env_file:
      - production.env
  backend:
    depends_on:
      - db
      - queue
    image: ghcr.io/codelibs/recotem-backend:{version}
    volumes:
      - data-location:/app/data
      - static-files:/app/dist/static
    env_file:
      - production.env
  celery_worker:
    depends_on:
      - backend
    image: ghcr.io/codelibs/recotem-worker:{version}
    volumes:
      - data-location:/app/data
    env_file:
      - production.env
  frontend:
    depends_on:
      backend:
        condition: service_healthy
    image: ghcr.io/codelibs/recotem-frontend:{version}
    env_file:
      - production.env
  proxy:
    depends_on:
      - frontend
    image: nginx:1.21.1
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf
      - static-files:/app/dist/static
    ports:
      - 8000:80
volumes:
  data-location:
    driver: local
  db-data:
    driver: local
  static-files:
    driver: local
