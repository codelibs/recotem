from django.http.response import StreamingHttpResponse
from rest_framework.decorators import action
from rest_framework.response import Response

from recotem.api.models.base_file_model import BaseFileModel


class FileDownloadRemoveMixin:
    @action(detail=True, methods=["delete"])
    def unlink_file(self, request, pk=None):
        obj: BaseFileModel = self.queryset.get(pk=pk)
        obj.delete_file()
        return Response(self.get_serializer(obj, many=False).data)

    @action(detail=True, methods=["get"])
    def download_file(self, request, pk=None):
        obj: BaseFileModel = self.queryset.get(pk=pk)
        if not obj.file:
            return Response(status=404, data=dict(detail=["file deleted."]))
        response = StreamingHttpResponse(
            obj.file,
            status=200,
            content_type="application/octed-stream",
            headers={"Content-Disposition": f'attachment; filename="{obj.basename()}"'},
        )
        response["Cache-Control"] = "no-cache"
        return response
