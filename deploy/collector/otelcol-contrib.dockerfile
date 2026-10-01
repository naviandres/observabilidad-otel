FROM public.ecr.aws/aws-observability/aws-otel-collector:v0.40.0
COPY otel-collector-aws.yaml /etc/otelcol/config.yaml
CMD ["--config=/etc/otelcol/config.yaml"]