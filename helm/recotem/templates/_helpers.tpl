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
Chart label.
*/}}
{{- define "recotem.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "recotem.labels" -}}
helm.sh/chart: {{ include "recotem.chart" . }}
{{ include "recotem.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "recotem.selectorLabels" -}}
app.kubernetes.io/name: {{ include "recotem.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Selector labels narrowed to the serve pods.

recotem.selectorLabels carries no component dimension, so it matches the train
CronJob's pods as well as the serve Deployment's.  For a PodDisruptionBudget
that is not merely untidy: a PDB's allowed-disruption count is computed over
the pods it selects, so a running training pod counts as healthy and inflates
the budget — with minAvailable=1 and one serve replica, a concurrent training
run raises allowed disruptions from 0 to 1 and a node drain may evict the only
serve pod.  Selecting on the component keeps the budget about serve alone.

NOT for a Deployment's spec.selector: that field is immutable, so adding a
label to it would make `helm upgrade` fail on every already-installed release.
Pod-template labels, PDB selectors and Service selectors are all mutable.
*/}}
{{- define "recotem.serveSelectorLabels" -}}
{{ include "recotem.selectorLabels" . }}
app.kubernetes.io/component: serve
{{- end }}

{{/*
ServiceAccount name.
*/}}
{{- define "recotem.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "recotem.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Image reference.
*/}}
{{- define "recotem.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) }}
{{- end }}
