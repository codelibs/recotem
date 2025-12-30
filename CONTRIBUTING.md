# Contributing to Recotem

Thank you for your interest in contributing to Recotem! This document provides guidelines for contributing.

## Getting Started

1. Fork the repository
2. Clone your fork
3. Set up the development environment (see [DEVELOPMENT.md](DEVELOPMENT.md))
4. Create a feature branch

## How to Contribute

### Reporting Issues

- Search existing issues before creating a new one
- Include steps to reproduce the issue
- Include expected and actual behavior
- Include environment details (OS, Docker version, etc.)

### Submitting Pull Requests

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes following the code style guidelines

3. Write or update tests as needed

4. Run tests locally:
   ```bash
   # Backend tests
   docker compose -f compose-test.yaml up --exit-code-from backend

   # Frontend tests
   cd frontend && yarn test:unit
   ```

5. Commit your changes with a descriptive message

6. Push to your fork and create a Pull Request

### Pull Request Guidelines

- Keep PRs focused on a single change
- Include a clear description of what the PR does
- Reference related issues if applicable
- Ensure all CI checks pass

## Code Style

### Python (Backend)

- Format with [Black](https://black.readthedocs.io/)
- Sort imports with [isort](https://pycqa.github.io/isort/)
- Follow PEP 8 guidelines

```bash
# Pre-commit hooks handle formatting automatically
pre-commit install
pre-commit run --all-files
```

### JavaScript/Vue (Frontend)

- Use ESLint for linting
- Use Prettier for formatting

```bash
cd frontend
yarn lint --fix
```

## Commit Messages

Use clear, descriptive commit messages:

```
type: short description

Longer description if needed.

Fixes #123
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

## Questions?

- Open an issue for questions
- Join the discussion at [discuss.codelibs.org](https://discuss.codelibs.org/c/recotemen/11)

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
