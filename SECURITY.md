# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | Yes                |

## Reporting a Vulnerability

If you discover a security vulnerability in Recotem, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please send an email or create a private security advisory through GitHub:

1. Go to the repository's **Security** tab
2. Click **Report a vulnerability**
3. Fill in the details of the vulnerability

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response timeline

- **Acknowledgment**: Within 48 hours
- **Initial assessment**: Within 1 week
- **Fix release**: Depends on severity, typically within 2 weeks for critical issues

### Scope

The following are in scope:
- Backend API authentication and authorization bypasses
- SQL injection, XSS, CSRF vulnerabilities
- Insecure deserialization
- Path traversal in file upload/download
- Docker container escape vectors

The following are out of scope:
- Vulnerabilities in dependencies (please report to the upstream project)
- Social engineering attacks
- Denial of service attacks
