#!/bin/bash
ROOT=$(pwd)
SCHEMA_FILE="${ROOT}/schema.yml"
cd backend/recotem && python manage.py spectacular --file "${SCHEMA_FILE}";
cd "${ROOT}/frontend" && \
   npx @openapitools/openapi-generator-cli generate -i "${SCHEMA_FILE}" \
   -g typescript-axios -o src/api/client
