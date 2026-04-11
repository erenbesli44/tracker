from typing import Annotated

from fastapi import Depends, Path

from src.database import SessionDep
from src.persons import service
from src.persons.exceptions import PersonNotFound
from src.persons.models import Person


def valid_person_id(
    person_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
) -> Person:
    person = service.get_by_id(session, person_id)
    if not person:
        raise PersonNotFound()
    return person


ValidPersonDep = Annotated[Person, Depends(valid_person_id)]
