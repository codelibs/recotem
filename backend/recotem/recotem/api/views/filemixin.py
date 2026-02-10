import re
from urllib.parse import quote

from django.http.response import StreamingHttpResponse
from rest_framework.decorators import action
from rest_framework.response import Response

from recotem.api.models.base_file_model import BaseFileModel

# Characters that could cause header injection or parsing issues.
_UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f\x7f"\\;]')


def _sanitize_filename(name: str) -> str:
    """Sanitize filename for use in Content-Disposition header (RFC 6266 / RFC 5987)."""
    return _UNSAFE_FILENAME_RE.sub("_", name)


class FileDownloadRemoveMixin:
    @action(detail=True, methods=["delete"])
    def unlink_file(self, request, pk: int):
        obj: BaseFileModel = self.get_object()
        obj.delete_file()
        return Response(self.get_serializer(obj, many=False).data)

    @action(detail=True, methods=["get"])
    def download_file(self, request, pk: int):
        obj: BaseFileModel = self.get_object()
        if not obj.file:
            return Response(status=404, data=dict(detail=["file deleted."]))
        filename = _sanitize_filename(obj.basename())
        # RFC 5987 encoded filename for non-ASCII characters.
        filename_utf8 = quote(filename, safe="")
        disposition = (
            f"attachment; filename=\"{filename}\"; filename*=UTF-8''{filename_utf8}"
        )
        response = StreamingHttpResponse(
            obj.file,
            status=200,
            content_type="application/octet-stream",
            headers={"Content-Disposition": disposition},
        )
        response["Cache-Control"] = "no-cache"
        return response
