from datetime import datetime

from fastapi import Depends, APIRouter, HTTPException, status
from sqlalchemy.orm import Session
from . import models, schemas
from .database import get_db
from sqlalchemy import select

router = APIRouter(
    prefix="/todos",
    tags=["todos"]
)

def _stamp_completion(todo: models.Todo, completed: bool) -> None:
    """Record *when* work finished (Phase 2 prerequisite), clearing it on re-open.

    The timestamp is set by the server so history reflects real user behaviour
    rather than a client-supplied value (Rules.md section 7).
    """
    todo.completed_at = datetime.now() if completed else None

@router.get("/", response_model=list[schemas.TodoResponse])
def get_todos(db: Session = Depends(get_db)):
    todos = db.scalars(select(models.Todo)).all()
    return todos

@router.get("/{todo_id}", response_model=schemas.TodoResponse)
def get_todo(todo_id: int, db: Session = Depends(get_db)):
    todo = db.scalars(select(models.Todo).where(models.Todo.id == todo_id)).first()
    if not todo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    return todo

@router.post("/", status_code=status.HTTP_201_CREATED, response_model=schemas.TodoResponse)
def create_todo(todo: schemas.TodoCreate, db: Session = Depends(get_db)):
    new_todo = models.Todo(**todo.model_dump())
    if new_todo.completed:
        _stamp_completion(new_todo, True)
    db.add(new_todo)
    db.commit()
    db.refresh(new_todo)
    return new_todo

@router.put("/{todo_id}", response_model=schemas.TodoResponse)
def update_todo(todo_id: int, todo: schemas.TodoUpdate, db: Session = Depends(get_db)):
    todo_update = db.scalars(select(models.Todo).where(models.Todo.id == todo_id)).first()
    if not todo_update:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    # Partial update: apply only the fields explicitly present in the request body,
    # never overwrite unspecified fields with defaults.
    updates = todo.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update"
        )
    was_completed = todo_update.completed
    for key, value in updates.items():
        setattr(todo_update, key, value)
    # Stamp only on a real transition, so re-saving a completed task does not
    # overwrite the moment work actually finished.
    if "completed" in updates and todo_update.completed != was_completed:
        _stamp_completion(todo_update, todo_update.completed)
    db.commit()
    db.refresh(todo_update)
    return todo_update

@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    todo = db.scalars(select(models.Todo).where(models.Todo.id == todo_id)).first()
    if not todo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    db.delete(todo)
    db.commit()
    return None

