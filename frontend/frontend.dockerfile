# stage 0
FROM node:16 as build-stage

RUN apt-get update && apt-get install -y wget --no-install-recommends
WORKDIR /app

# cache yarn
COPY ./package.json /app/package.json
RUN npm install yarn && yarn

COPY ./ /app/

RUN yarn run build

FROM nginx:1.21.1

COPY --from=build-stage /app/dist/ /usr/share/nginx/html
COPY --from=build-stage /app/nginx.conf /etc/nginx/conf.d/default.conf

#COPY ./nginx-backend-not-found.conf /etc/nginx/extra-conf.d/backend-not-found.conf
