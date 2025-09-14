from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import PlanTripRequestSerializer, PlanTripResponseSerializer
from .hos import HOSPlanner
from .route import get_route_summary

class PlanTripView(APIView):
    def post(self, request):
        req = PlanTripRequestSerializer(data=request.data)
        if not req.is_valid():
            return Response(req.errors, status=status.HTTP_400_BAD_REQUEST)

        data = req.validated_data
        route = get_route_summary(
            data['current_location'],
            data['pickup_location'],
            data['dropoff_location'],
            data.get('assume_distance_mi')
        )

        planner = HOSPlanner(
            distance_mi=route['distance_mi'],
            duration_hr=route['duration_hr'],
            current_cycle_used_hours=data['current_cycle_used_hours'],
            pre_pickup_drive_min=route.get('pre_pickup_min', 0)  # <-- key wire-up
        )
        plan = planner.plan()

        resp = {
            'route': {
                'distance_mi': route['distance_mi'],
                'duration_hr': route['duration_hr'],
                'polyline': route.get('polyline')
            },
            'stops': plan['stops'],
            'days': plan['days'],
        }
        return Response(PlanTripResponseSerializer(resp).data)
