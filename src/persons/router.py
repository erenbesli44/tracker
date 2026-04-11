from fastapi import APIRouter, status

from src.database import SessionDep
from src.persons import service
from src.persons.dependencies import ValidPersonDep
from src.persons.exceptions import PersonSlugConflict
from src.persons.schemas import PersonCreate, PersonResponse, PersonUpdate

router = APIRouter(prefix="/persons", tags=["persons"])


@router.post("/", response_model=PersonResponse, status_code=status.HTTP_201_CREATED)
def create_person(data: PersonCreate, session: SessionDep) -> PersonResponse:
    from slugify import slugify

    if service.get_by_slug(session, slugify(data.name)):
        raise PersonSlugConflict()
    return service.create(session, data)


@router.get("/", response_model=list[PersonResponse])
def list_persons(session: SessionDep) -> list[PersonResponse]:
    return service.list_all(session)


@router.get("/{person_id}", response_model=PersonResponse)
def get_person(person: ValidPersonDep) -> PersonResponse:
    return person


@router.patch("/{person_id}", response_model=PersonResponse)
def update_person(
    data: PersonUpdate,
    person: ValidPersonDep,
    session: SessionDep,
) -> PersonResponse:
    return service.update(session, person, data)
