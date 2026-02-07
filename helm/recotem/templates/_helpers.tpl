{{/*
Expand the name of the chart.
*/}}
{{- define "recotem.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "recotem.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "recotem.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "recotem.labels" -}}
helm.sh/chart: {{ include "recotem.chart" . }}
{{ include "recotem.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "recotem.selectorLabels" -}}
app.kubernetes.io/name: {{ include "recotem.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Backend selector labels
*/}}
{{- define "recotem.backendSelectorLabels" -}}
{{ include "recotem.selectorLabels" . }}
app.kubernetes.io/component: backend
{{- end }}

{{/*
Worker selector labels
*/}}
{{- define "recotem.workerSelectorLabels" -}}
{{ include "recotem.selectorLabels" . }}
app.kubernetes.io/component: worker
{{- end }}

{{/*
Proxy selector labels
*/}}
{{- define "recotem.proxySelectorLabels" -}}
{{ include "recotem.selectorLabels" . }}
app.kubernetes.io/component: proxy
{{- end }}

{{/*
Database URL: external or in-cluster
*/}}
{{- define "recotem.databaseUrl" -}}
{{- if .Values.postgresql.external }}
{{- .Values.secrets.databaseUrl }}
{{- else }}
{{- printf "postgresql://%s@%s-postgresql:%d/%s" .Values.postgresql.username (include "recotem.fullname" .) (.Values.postgresql.port | int) .Values.postgresql.database }}
{{- end }}
{{- end }}

{{/*
Redis host
*/}}
{{- define "recotem.redisHost" -}}
{{- if .Values.redis.external }}
{{- .Values.redis.host }}
{{- else }}
{{- printf "%s-redis" (include "recotem.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Image pull secrets
*/}}
{{- define "recotem.imagePullSecrets" -}}
{{- with .Values.image.pullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end }}
