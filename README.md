# Recotem

## Overview

Recotem is an easy to use interface to recommender systems;
Recotem can be launched on any platform with Docker.
It ships with a Web-base UI, and you can train and (qualitatively) evaluate the recommendation engine solely using UI.

![Sample usage of recotem](./recotem-sample-image.png)

Recotem is licensed under Apache 2.0.

## Getting Started

## Development

### Backend & Worker

To run the backend (and worker) in Django development mode, use `docker-compose-dev.yml`.

```
docker-compose -f docker-compose-dev.yml build
docker-compose -f docker-compose-dev.yml up
```

### frontend

To run the frontend webpack-dev-sever, you will need a descent version of yarn.

After `yarn` under `frontend/` directory to install the dependency, run

```sh
cd frontend
yarn serve
```

In order for the frontend to work with the API, you first have to launch the backend following the above instruction.
