import random

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from recotem.api.exceptions import ResourceNotFoundError
from recotem.api.models import ItemMetaData, TrainedModel
from recotem.api.serializers import TrainedModelSerializer
from recotem.api.services.model_service import fetch_item_metadata, fetch_mapped_rec

from .filemixin import FileDownloadRemoveMixin
from .mixins import OwnedResourceMixin
from .pagination import StandardPagination


class IDAndScore(serializers.Serializer):
    item_id = serializers.CharField()
    score = serializers.FloatField()


class UserProfileInteractionSerializer(serializers.Serializer):
    item_ids = serializers.ListField(child=serializers.CharField())
    cutoff = serializers.IntegerField()


class RecommendationResultUsingProfileSerializer(serializers.Serializer):
    recommendations = serializers.ListField(child=IDAndScore())


class RawRecommendationSerializer(serializers.Serializer):
    user_id = serializers.CharField()
    user_profile = serializers.ListField(child=serializers.CharField())
    recommendations = serializers.ListField(child=IDAndScore())


class RecommendationWithMetaDataSerializer(serializers.Serializer):
    user_id = serializers.CharField()
    user_profile = serializers.CharField()
    recommendations = serializers.CharField()


class TrainedModelViewset(OwnedResourceMixin, viewsets.ModelViewSet, FileDownloadRemoveMixin):
    permission_classes = [IsAuthenticated]
    serializer_class = TrainedModelSerializer
    filterset_fields = ["id", "data_loc", "data_loc__project"]
    pagination_class = StandardPagination
    owner_lookup = "data_loc__project__owner"

    def get_queryset(self):
        return (
            TrainedModel.objects.select_related(
                "configuration", "configuration__project", "data_loc", "data_loc__project"
            )
            .filter(self.get_owner_filter())
            .order_by("-ins_datetime")
        )

    @extend_schema(responses={200: RawRecommendationSerializer})
    @action(detail=True, methods=["get"])
    def sample_recommendation_raw(self, request, pk=None):
        mapped_rec = fetch_mapped_rec(pk)
        X = mapped_rec.recommender.X_train_all
        sample_user_index = random.randint(0, X.shape[0] - 1)
        sample_user_id: str = mapped_rec.user_ids[sample_user_index]
        ublock_begin = X.indptr[sample_user_index]
        ublock_end = X.indptr[sample_user_index + 1]
        user_history = [
            str(mapped_rec.item_ids[i]) for i in X.indices[ublock_begin:ublock_end]
        ]
        recs = mapped_rec.get_recommendation_for_known_user_id(sample_user_id)
        return Response(
            status=200,
            data=dict(
                user_id=sample_user_id,
                user_profile=user_history,
                recommendations=[dict(item_id=str(x[0]), score=x[1]) for x in recs],
            ),
        )

    @extend_schema(responses={200: RecommendationWithMetaDataSerializer})
    @action(
        detail=True,
        methods=["get"],
        url_path=r"sample_recommendation_metadata/(?P<metadata_id>\d+)",
    )
    def sample_recommendation_metadata(self, request, metadata_id: int, pk=None):
        model = self.get_object()
        if not ItemMetaData.objects.filter(
            id=metadata_id,
            project_id=model.data_loc.project_id,
        ).exists():
            raise ResourceNotFoundError(detail=f"Item metadata {metadata_id} not found.")

        mapped_rec = fetch_mapped_rec(model.id)
        metadata = fetch_item_metadata(metadata_id)

        X = mapped_rec.recommender.X_train_all
        sample_user_index = random.randint(0, X.shape[0] - 1)
        sample_user_id: str = mapped_rec.user_ids[sample_user_index]
        ublock_begin = X.indptr[sample_user_index]
        ublock_end = X.indptr[sample_user_index + 1]
        user_history = (
            metadata.reindex(
                [
                    str(mapped_rec.item_ids[i])
                    for i in X.indices[ublock_begin:ublock_end]
                ]
            )
            .reset_index()
            .to_json(orient="records")
        )
        recs = mapped_rec.get_recommendation_for_known_user_id(sample_user_id)

        recommendations = metadata.reindex([str(x[0]) for x in recs])
        recommendations["score"] = [x[1] for x in recs]
        recommendations_json = (
            recommendations.round(2).reset_index().to_json(orient="records")
        )
        return Response(
            status=200,
            data=dict(
                user_id=sample_user_id,
                user_profile=user_history,
                recommendations=recommendations_json,
            ),
        )

    @extend_schema(
        parameters=[
            serializers.CharField(help_text="User ID"),
            serializers.IntegerField(help_text="Number of recommendations"),
        ],
        responses={200: IDAndScore(many=True)},
    )
    @action(detail=True, methods=["get"])
    def recommendation(self, request, pk=None):
        """Get recommendations for a known user by user ID."""
        user_id = request.query_params.get("user_id")
        cutoff_raw = request.query_params.get("cutoff", 10)
        if not user_id:
            return Response(status=400, data={"detail": "user_id is required."})
        try:
            cutoff = int(cutoff_raw)
        except (TypeError, ValueError):
            return Response(status=400, data={"detail": "cutoff must be an integer."})
        if cutoff < 1:
            return Response(status=400, data={"detail": "cutoff must be >= 1."})

        mapped_rec = fetch_mapped_rec(pk)
        try:
            recs = mapped_rec.get_recommendation_for_known_user_id(
                user_id, cutoff=cutoff
            )
        except KeyError:
            return Response(status=404, data={"detail": f"User '{user_id}' not found."})
        return Response(
            data=[dict(item_id=str(x[0]), score=x[1]) for x in recs],
        )

    @extend_schema(
        responses={200: RecommendationResultUsingProfileSerializer},
        request=UserProfileInteractionSerializer,
    )
    @action(detail=True, methods=["post"])
    def recommend_using_profile_interaction(self, request, pk=None):
        mapped_rec = fetch_mapped_rec(pk)
        serializer = UserProfileInteractionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recs = mapped_rec.get_recommendation_for_new_user(
            serializer.validated_data["item_ids"],
            cutoff=serializer.validated_data["cutoff"],
        )
        return Response(
            status=200,
            data=dict(
                recommendations=[dict(item_id=str(x[0]), score=x[1]) for x in recs],
            ),
        )
