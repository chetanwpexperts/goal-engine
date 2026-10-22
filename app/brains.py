"""All of Goal Engine's brains in one place: what each can understand. Used by the learning loop (app/learning.py)."""


def labels(brain: str) -> set[str]:
    if brain == "office":
        from app.officeknow import brain as office

        return office.labels()
    return set()
