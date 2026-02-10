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

    def validate_project(self, project: Project) -> Project:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and project.owner_id not in (None, user.id):
            raise serializers.ValidationError("Project not found.")
        return project

    def validate(self, attrs):
        if not attrs.get("file"):
            raise exceptions.ValidationError({"file": ["file is required."]})
        return attrs

    def create(self, validated_data):
        obj: TrainingData = TrainingData.objects.create(**validated_data)
        try:
            obj.validate_return_df()
        except exceptions.ValidationError as e:
            obj.delete()
            raise exceptions.ValidationError({"file": e.detail}) from e
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

    def validate_project(self, project: Project) -> Project:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and project.owner_id not in (None, user.id):
            raise serializers.ValidationError("Project not found.")
        return project

    def create(self, validated_data):
        obj: ItemMetaData = ItemMetaData.objects.create(**validated_data)
        try:
            df = obj.validate_return_df()
        except exceptions.ValidationError as e:
            obj.delete()
            raise exceptions.ValidationError({"file": e.detail}) from e
        project: Project = obj.project
        valid_column_names = []
        for c in df.columns:
            if c == project.item_column:
                continue
            try:
                df[[c]].to_json(orient="records")
                valid_column_names.append(c)
            except (TypeError, ValueError):
                continue
        obj.valid_columns_list_json = valid_column_names
        obj.filesize = obj.file.size
        obj.save()
        return obj
