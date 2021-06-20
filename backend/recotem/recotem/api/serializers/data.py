import json

from rest_framework import exceptions, serializers

from recotem.api.models import ItemMetaData, Project, TrainingData


class TrainingDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainingData
        fields = [
            "id",
            "project",
            "file",
            "ins_datetime",
            "basename",
            "filesize",
        ]
        read_only_fields = [
            "ins_datetime",
            "basename",
            "filesize",
        ]

    def create(self, validated_data):
        obj: TrainingData = TrainingData.objects.create(**validated_data)
        try:
            obj.validate_return_df()
        except exceptions.ValidationError as e:
            obj.delete()
            raise e
        return obj


class ItemMetaDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemMetaData
        fields = [
            "id",
            "project",
            "file",
            "valid_columns_list_json",
            "ins_datetime",
            "basename",
            "filesize",
        ]
        read_only_fields = [
            "valid_columns_list_json",
            "ins_datetime",
            "basename",
            "filesize",
        ]

    def create(self, validated_data):
        obj: ItemMetaData = ItemMetaData.objects.create(**validated_data)
        try:
            df = obj.validate_return_df()
        except exceptions.ValidationError as e:
            obj.delete()
            raise e
        project: Project = obj.project
        valid_column_names = []
        for c in df.columns:
            if c == project.item_column:
                continue
            try:
                df[[c]].to_json(orient="records")
                valid_column_names.append(c)
            except:
                continue
        obj.valid_columns_list_json = json.dumps(valid_column_names)
        obj.save()
        return obj
