import pickle
import random
from functools import lru_cache
from typing import Optional

import pandas as pd
from drf_spectacular.utils import extend_schema
from irspack import IDMappedRecommender
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from recotem.api.models import ItemMetaData, Project, TrainedModel
from recotem.api.serializers import TrainedModelSerializer

from .filemixin import FileDownloadRemoveMixin


class IDAndScore(serializers.Serializer):
    item_id = serializers.CharField()
    score = serializers.FloatField()


class RawRecommendationSerializer(serializers.Serializer):
    user_id = serializers.CharField()
    user_profile = serializers.ListField(child=serializers.CharField())
    recommendations = serializers.ListField(child=IDAndScore())


class RecommendationWithMetaDataSerializer(serializers.Serializer):
    user_id = serializers.CharField()
    user_profile = serializers.CharField()
    recommendations = serializers.CharField()


@lru_cache(maxsize=1)
def fetch_mapped_rec(pk: int) -> Optional[IDMappedRecommender]:
    model_record = TrainedModel.objects.get(pk=pk)
    try:
        return pickle.load(model_record.file)["id_mapped_recommender"]
    except:
        return None


@lru_cache(maxsize=1)
def fetch_item_metadata(pk: int) -> Optional[pd.DataFrame]:
    model_record: ItemMetaData = ItemMetaData.objects.get(pk=pk)
    try:
        project: Project = model_record.project
        item_column: str = project.item_column
        df: pd.DataFrame = pickle.load(model_record.file)
        df[item_column] = [str(x) for x in df[item_column]]
        return df.drop_duplicates(item_column).set_index(
            model_record.project.item_column
        )
    except Exception as e:
        print(e.args)

        return None


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
        if mapped_rec is None:
            return Response(status=404, data=dict(detail=["file deleted."]))
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
        if mapped_rec is None:
            return Response(status=404, data=dict(detail=["file deleted."]))
        metadata = fetch_item_metadata(metadata_id)
        if metadata is None:
            return Response(status=404, data=dict(detail=["metadata file deleted."]))

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
