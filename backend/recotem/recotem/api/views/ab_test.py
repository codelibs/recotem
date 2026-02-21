import logging

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from recotem.api.authentication import RequireManagementScope
from recotem.api.models import ABTest, ConversionEvent
from recotem.api.serializers.ab_test import ABTestResultSerializer, ABTestSerializer
from recotem.api.services.ab_testing_service import compute_ab_results
from recotem.api.views.mixins import OwnedResourceMixin
from recotem.api.views.pagination import StandardPagination

logger = logging.getLogger(__name__)

# Maps target_metric_name to the event types that count as conversions
METRIC_CONVERSION_TYPES = {
    "ctr": [ConversionEvent.EventType.CLICK],
    "purchase_rate": [ConversionEvent.EventType.PURCHASE],
    "conversion_rate": [
        ConversionEvent.EventType.CLICK,
        ConversionEvent.EventType.PURCHASE,
    ],
}


class ABTestViewSet(OwnedResourceMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, RequireManagementScope]
    serializer_class = ABTestSerializer
    filterset_fields = ["project", "status"]
    pagination_class = StandardPagination
    owner_lookup = "project__owner"

    def get_queryset(self):
        return (
            ABTest.objects.select_related(
                "project", "control_slot", "variant_slot", "winner_slot"
            )
            .filter(self.get_owner_filter())
            .order_by("-ins_datetime")
        )

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        """Start the A/B test."""
        test = self.get_object()
        if test.status != ABTest.Status.DRAFT:
            return Response(
                {"error": "Can only start tests in DRAFT status"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        test.status = ABTest.Status.RUNNING
        test.started_at = timezone.now()
        test.save(update_fields=["status", "started_at", "updated_at"])
        return Response(ABTestSerializer(test).data)

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        """Stop the A/B test."""
        test = self.get_object()
        if test.status != ABTest.Status.RUNNING:
            return Response(
                {"error": "Can only stop RUNNING tests"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        test.status = ABTest.Status.COMPLETED
        test.ended_at = timezone.now()
        test.save(update_fields=["status", "ended_at", "updated_at"])
        return Response(ABTestSerializer(test).data)

    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        """Get A/B test statistical results."""
        test = self.get_object()

        # Count events per slot
        def _get_counts(slot_id):
            events = ConversionEvent.objects.filter(
                project=test.project,
                deployment_slot_id=slot_id,
            )
            if test.started_at:
                events = events.filter(timestamp__gte=test.started_at)
            if test.ended_at:
                events = events.filter(timestamp__lte=test.ended_at)

            # Deduplicate impressions by recommendation_request_id so that
            # auto-recorded and manually-recorded events for the same request
            # are only counted once.
            impression_qs = events.filter(
                event_type=ConversionEvent.EventType.IMPRESSION
            )
            impressions = (
                impression_qs.filter(recommendation_request_id__isnull=False)
                .values("recommendation_request_id")
                .distinct()
                .count()
            )
            # Also count impressions without a request_id (legacy manual events)
            impressions += impression_qs.filter(
                recommendation_request_id__isnull=True
            ).count()
            conversion_types = METRIC_CONVERSION_TYPES.get(test.target_metric_name)
            if conversion_types is None:
                logger.warning(
                    "Unknown target_metric_name '%s' for ABTest %s, "
                    "falling back to click+purchase",
                    test.target_metric_name,
                    test.id,
                )
                conversion_types = [
                    ConversionEvent.EventType.CLICK,
                    ConversionEvent.EventType.PURCHASE,
                ]
            conversions = events.filter(event_type__in=conversion_types).count()
            return impressions, conversions

        ctrl_imp, ctrl_conv = _get_counts(test.control_slot_id)
        var_imp, var_conv = _get_counts(test.variant_slot_id)

        results = compute_ab_results(
            ctrl_imp, ctrl_conv, var_imp, var_conv, test.confidence_level
        )
        results["control_impressions"] = ctrl_imp
        results["control_conversions"] = ctrl_conv
        results["variant_impressions"] = var_imp
        results["variant_conversions"] = var_conv

        # min_sample_size enforcement
        total_impressions = ctrl_imp + var_imp
        results["min_sample_size"] = test.min_sample_size
        results["sufficient_data"] = total_impressions >= test.min_sample_size

        serializer = ABTestResultSerializer(results)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def promote_winner(self, request, pk=None):
        """Promote the winning slot."""
        test = self.get_object()
        if test.status != ABTest.Status.COMPLETED:
            return Response(
                {"error": "Can only promote winner of COMPLETED tests"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        slot_id = request.data.get("slot_id")
        if slot_id not in [test.control_slot_id, test.variant_slot_id]:
            return Response(
                {"error": "slot_id must be one of the test slots"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from recotem.api.models import DeploymentSlot

        winner = DeploymentSlot.objects.get(id=slot_id)
        test.winner_slot = winner
        test.save(update_fields=["winner_slot", "updated_at"])

        # Set winner to 100% weight, deactivate loser
        loser_id = (
            test.variant_slot_id
            if slot_id == test.control_slot_id
            else test.control_slot_id
        )
        winner.weight = 100
        winner.save(update_fields=["weight", "updated_at"])
        DeploymentSlot.objects.filter(id=loser_id).update(is_active=False)

        return Response({"status": "promoted", "winner_slot_id": slot_id})
