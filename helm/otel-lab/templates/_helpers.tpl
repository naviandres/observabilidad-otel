{{- define "otel-lab.labels" -}}
app.kubernetes.io/name: otel-lab
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: otel-lab
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "otel-lab.spotPlacement" -}}
nodeSelector:
{{ toYaml .Values.spot.nodeSelector | indent 2 }}
tolerations:
{{ toYaml .Values.spot.tolerations | indent 2 }}
{{- end -}}

{{- define "otel-lab.pgSecretVolume" -}}
- name: pg-password
  csi:
    # Nombre del driver desde values (secrets.pgPassword.csiDriver). El add-on
    # de GKE --enable-secret-manager registra "secrets-store-gke.csi.k8s.io";
    # el driver comunitario por Helm registra "secrets-store.csi.k8s.io".
    # Ambos usan el mismo SecretProviderClass CRD (secrets-store.csi.x-k8s.io/v1).
    driver: {{ .Values.secrets.pgPassword.csiDriver }}
    readOnly: true
    volumeAttributes:
      secretProviderClass: {{ .Values.secrets.pgPassword.providerClass }}
{{- end -}}

{{- define "otel-lab.pgSecretMount" -}}
- name: pg-password
  mountPath: {{ .Values.secrets.pgPassword.mountPath }}
  readOnly: true
{{- end -}}
