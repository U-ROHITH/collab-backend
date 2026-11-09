from rest_framework import serializers


class ReportsOverviewOutput(serializers.Serializer):
    role = serializers.CharField()
    metrics = serializers.DictField(child=serializers.JSONField())
