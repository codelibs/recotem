# Recotem

## Overview

Recotem is an easy to use interface to recommender systems;
Recotem can be launched on any platform with Docker.
It ships with a Web-base UI, and you can train and (qualitatively) evaluate the recommendation engine solely using UI.

![Sample usage of recotem](./recotem-sample-image.png)

Recotem is licensed under Apache 2.0.

## Website

[recotem.org](https://recotem.org)

## Issues/Questions

[discuss.codelibs.org](https://discuss.codelibs.org/c/recotemen/11)

## Getting Started

There are two ways to start using Recotem. Both requires [latest docker](https://docs.docker.com/get-docker/).

### 1. Using pre-built image.

1. Visit [latest release](https://github.com/codelibs/recotem/releases/latest)
1. Download "Docker resources to try out" from Assets
1. Unzip it and
   - (Windows) Click "recotem-compose" script
   - (Linux & MacOS) Run `docker compose` there.
     ```sh
        docker compose up
     ```

See [https://recotem.org/guide/installation.html]([https://recotem.org/guide/installation.html]) for a friendlier introduction.

### 2. Building the image

1. Clone this repository.
2. In the repository top directory, simply run
   ```sh
       docker compose up
   ```

## Development

### Backend & Worker

To run the backend (and worker) in Django development mode, use `compose-dev.yaml`.

```
docker compose -f compose-dev.yaml build
docker compose -f compose-dev.yaml up
```

### frontend

To run the frontend webpack-dev-sever, you will need a descent version of yarn.

After `yarn` under `frontend/` directory to install the dependency, run

```sh
cd frontend
yarn serve
```

In order for the frontend to work with the API, you first have to launch the backend following the above instruction.

## Command-line tool

[recotem-cli](https://github.com/codelibs/recotem-cli) allows you to

- tune & train recommender systems
- obtain the recommendation result

via command-line interface.

## Batch execution on ECS

There is [an example project](https://github.com/codelibs/recotem-batch-example) which uses recotem to batch-execute recommendation task on Amazon ECS.
