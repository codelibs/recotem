import pickle
import random
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from recotem.api.models import ItemMetaData, Project, TrainedModel
from recotem.api.serializers import TrainedModelSerializer
from recotem.api.tasks import IDMappedRecommender
from recotem.api.utils import read_dataframe

from .filemixin import FileDownloadRemoveMixin


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


@lru_cache(maxsize=1)
def fetch_mapped_rec(pk: int) -> IDMappedRecommender:
    try:
        model_record = TrainedModel.objects.get(pk=pk)
        return pickle.load(model_record.file)["id_mapped_recommender"]
    except:
        raise APIException(detail=f"Could not find model {pk}", code=404)


@lru_cache(maxsize=1)
def fetch_item_metadata(pk: int) -> Optional[pd.DataFrame]:
    try:
        model_record: ItemMetaData = ItemMetaData.objects.get(pk=pk)
        project: Project = model_record.project
        item_column: str = project.item_column
        df: pd.DataFrame = read_dataframe(
            Path(model_record.file.name), model_record.file
        )
        df[item_column] = [str(x) for x in df[item_column]]
        return df.drop_duplicates(item_column).set_index(
            model_record.project.item_column
        )
    except:
        raise APIException(detail=f"Could not load item metadata {pk}", code=404)


class TrainedModelViewset(viewsets.ModelViewSet, FileDownloadRemoveMixin):
    permission_classes = [IsAuthenticated]
    queryset = TrainedModel.objects.all().order_by("-ins_datetime")
    serializer_class = TrainedModelSerializer
    filterset_fields = ["id", "data_loc", "data_loc__project"]

    class pagination_class(PageNumberPagination):
        page_size = 10
        page_size_query_param = "page_size"

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
        mapped_rec = fetch_mapped_rec(pk)
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
        responses={200: RecommendationResultUsingProfileSerializer},
        request=UserProfileInteractionSerializer,
    )
    @action(detail=True, methods=["post"])
    def recommend_using_profile_interaction(self, request, pk=None):
        mapped_rec = fetch_mapped_rec(pk)
        serializer = UserProfileInteractionSerializer(data=request.data)
        serializer.is_valid()
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
