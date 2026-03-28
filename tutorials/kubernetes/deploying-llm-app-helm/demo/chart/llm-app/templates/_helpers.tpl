{{/*
Expand the name of the chart.
*/}}
{{- define "llm-app.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "llm-app.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "llm-app.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "llm-app.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
app.kubernetes.io/name: {{ include "llm-app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "llm-app.selectorLabels" -}}
app.kubernetes.io/name: {{ include "llm-app.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
