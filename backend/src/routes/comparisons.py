from fastapi import APIRouter, Depends, Query, Response

from src.constants.enums import Role
from src.controllers.views import comparison_view
from src.models.base import Session
from src.schemas.requests import CreateComparison
from src.services.auth import roles
from src.services.comparisons import create_comparison, get_comparison, list_comparisons

router = APIRouter(prefix='/api/comparisons')
editor = roles(Role.ANALYST, Role.SUPERVISOR)


@router.get('')
def index(candidate: int | None = Query(None, gt=0), user=Depends(editor)):
    with Session() as db:
        return [comparison_view(item) for item in list_comparisons(db, candidate)]


@router.post('')
def create(request: CreateComparison, response: Response, user=Depends(roles(Role.ANALYST))):
    with Session.begin() as db:
        item, created = create_comparison(db, request, user['sub'])
        view = comparison_view(item, include_result=True)
    response.status_code = 201 if created else 200
    return {'created': created, 'comparison': view}


@router.get('/{comparison_id}')
def detail(comparison_id: int, user=Depends(editor)):
    with Session() as db:
        return comparison_view(get_comparison(db, comparison_id), include_result=True)
