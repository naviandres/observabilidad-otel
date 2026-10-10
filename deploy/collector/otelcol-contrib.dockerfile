# ADOT tag pinned and validated with the ADOT binary itself (PLAN §2.C, R-09).
FROM public.ecr.aws/aws-observability/aws-otel-collector:v0.46.0
COPY otel-collector-aws.yaml /etc/otelcol/config.yaml
CMD ["--config=/etc/otelcol/config.yaml"]