from fastapi import HTTPException, Request


def repository(request: Request):
    repo = request.app.state.repo
    if repo is None:
        raise HTTPException(503, "Database is not configured or unavailable")
    return repo
