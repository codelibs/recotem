FROM nginx:1.15

COPY ./nginx.conf /etc/nginx/conf.d/default.conf
