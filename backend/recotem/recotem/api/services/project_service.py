from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Count, Q

from recotem.api.exceptions import ResourceNotFoundError
from recotem.api.models import Project


def get_project_or_404(pk: int, user: AbstractBaseUser | None = None) -> Project:
    """Retrieve a project by primary key and optional user access check."""
    try:
        project = Project.objects.get(pk=pk)
    except Project.DoesNotExist:
        raise ResourceNotFoundError(detail=f"Project {pk} not found.") from None
    if (
        user is not None
        and not user.is_staff
        and project.owner_id not in (None, user.id)
    ):
        raise ResourceNotFoundError(detail=f"Project {pk} not found.")
    return project


def get_project_summary(project: Project) -> dict:
    """Compute summary statistics for a project in a single query."""
    result = Project.objects.filter(pk=project.pk).aggregate(
        n_data=Count(
            "trainingdata",
            filter=Q(trainingdata__filesize__isnull=False),
        ),
        n_complete_jobs=Count(
            "modelconfiguration",
            filter=Q(modelconfiguration__tuning_job__isnull=False),
        ),
        n_models=Count(
            "modelconfiguration__trainedmodel",
            filter=Q(modelconfiguration__trainedmodel__filesize__isnull=False),
        ),
    )
    return {
        "n_data": result["n_data"],
        "n_complete_jobs": result["n_complete_jobs"],
        "n_models": result["n_models"],
        "ins_datetime": project.ins_datetime,
    }
