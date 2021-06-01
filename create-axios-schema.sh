#!/bin/bash
ROOT=$(pwd)
SCHEMA_FILE="${ROOT}/schema.yml"
cd backend/recotem && python manage.py spectacular --file "${SCHEMA_FILE}";
cd "${ROOT}/frontend" && \
   npx openapi-typescript "${SCHEMA_FILE}" \
   --output src/api/schema.ts
