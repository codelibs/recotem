# Recotem

An easy-to-use interface to recommender systems.

![Sample usage of recotem](./recotem-sample-image.png)

## Features

- Launch on any platform with Docker
- Web-based UI for training and evaluating recommendation engines
- No coding required for basic usage
- Supports various recommendation algorithms via [irspack](https://github.com/tohtsky/irspack)

## Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (with Docker Compose)

### Option 1: Using Pre-built Images (Recommended)

1. Download the latest release from [GitHub Releases](https://github.com/codelibs/recotem/releases/latest)
2. Unzip and run:

```bash
docker compose up
```

3. Open http://localhost:8000 in your browser

### Option 2: Building from Source

```bash
git clone https://github.com/codelibs/recotem.git
cd recotem
docker compose up
```

## Architecture

```
                    +----------------+
                    |     Proxy      |
                    |    (nginx)     |
                    +-------+--------+
                            |
            +---------------+---------------+
            |                               |
    +-------v--------+             +--------v-------+
    |    Frontend    |             |    Backend     |
    |   (Vue.js)     |             |   (Django)     |
    +----------------+             +--------+-------+
                                           |
                       +-------------------+-------------------+
                       |                   |                   |
               +-------v------+    +-------v-------+   +-------v-------+
               |   Database   |    |     Queue     |   | Celery Worker |
               | (PostgreSQL) |    |  (RabbitMQ)   |   |   (irspack)   |
               +--------------+    +---------------+   +---------------+
```

## Production Deployment

For production deployments, use `compose-production.yaml`:

```bash
# 1. Configure environment variables
cp .env.example .env
# Edit .env with your settings

# 2. Start services
docker compose -f compose-production.yaml up -d
```

See [.env.example](.env.example) for required configuration.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for development setup and guidelines.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

## Related Tools

- [recotem-cli](https://github.com/codelibs/recotem-cli) - Command-line interface for Recotem
- [recotem-batch-example](https://github.com/codelibs/recotem-batch-example) - Batch execution on Amazon ECS

## Resources

- Website: [recotem.org](https://recotem.org)
- Documentation: [recotem.org/guide/](https://recotem.org/guide/)
- Community: [discuss.codelibs.org](https://discuss.codelibs.org/c/recotemen/11)

## License

Apache 2.0
