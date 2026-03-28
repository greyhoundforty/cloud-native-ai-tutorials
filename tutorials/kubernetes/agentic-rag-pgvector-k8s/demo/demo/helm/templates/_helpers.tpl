{{/*
Expand the name of the chart.
*/}}
{{- define "rag-service.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "rag-service.fullname" -}}
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

{{- define "rag-service.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "rag-service.labels" -}}
helm.sh/chart: {{ include "rag-service.chart" . }}
{{ include "rag-service.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "rag-service.selectorLabels" -}}
app.kubernetes.io/name: {{ include "rag-service.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Return the Anthropic secret name
*/}}
{{- define "rag-service.anthropicSecretName" -}}
{{- if .Values.anthropic.existingSecret }}
{{- .Values.anthropic.existingSecret }}
{{- else }}
{{- include "rag-service.fullname" . }}-anthropic
{{- end }}
{{- end }}

{{/*
Return the database secret name
*/}}
{{- define "rag-service.dbSecretName" -}}
{{- if .Values.postgresql.enabled }}
{{- include "rag-service.fullname" . }}-postgresql
{{- else if .Values.externalDatabase.existingSecret }}
{{- .Values.externalDatabase.existingSecret }}
{{- else }}
{{- include "rag-service.fullname" . }}-db
{{- end }}
{{- end }}
