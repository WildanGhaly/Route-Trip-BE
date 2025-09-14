from rest_framework import serializers

class PlanTripRequestSerializer(serializers.Serializer):
    current_location = serializers.CharField()
    pickup_location = serializers.CharField()
    dropoff_location = serializers.CharField()
    current_cycle_used_hours = serializers.FloatField(min_value=0)
    assume_distance_mi = serializers.FloatField(min_value=0, required=False)

class SegmentSerializer(serializers.Serializer):
    t0 = serializers.CharField()
    t1 = serializers.CharField()
    status = serializers.ChoiceField(choices=['off', 'sleeper', 'driving', 'on_duty'])
    label = serializers.CharField(required=False, allow_blank=True)

class DayPlanSerializer(serializers.Serializer):
    index = serializers.IntegerField()
    date = serializers.DateField()
    segments = SegmentSerializer(many=True)
    notes = serializers.CharField()

class StopSerializer(serializers.Serializer):
    type = serializers.CharField()
    eta = serializers.DateTimeField()
    duration_min = serializers.IntegerField()

class PlanTripResponseSerializer(serializers.Serializer):
    route = serializers.DictField()
    stops = StopSerializer(many=True)
    days = DayPlanSerializer(many=True)