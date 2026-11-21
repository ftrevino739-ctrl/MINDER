"""Safe, ordered dispatch for model-requested desktop actions."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar

CallT = TypeVar("CallT")
ResponseT = TypeVar("ResponseT")


async def dispatch_function_calls(
    function_calls: Iterable[CallT] | None,
    execute: Callable[[CallT], Awaitable[ResponseT]],
    *,
    is_interrupted: Callable[[], bool],
    response_factory: Callable[..., ResponseT],
) -> list[ResponseT]:
    """Run a tool batch in order, never starting actions after an interrupt.

    An action whose executor has already been entered is allowed to finish:
    cancelling arbitrary desktop side effects would be unsafe and unreliable.
    Every later call receives an explicit not-executed response so the caller
    can still complete the provider's tool-response protocol.
    """
    responses: list[ResponseT] = []
    for call in function_calls or ():
        call_id = str(getattr(call, "id", "") or "")
        name = str(getattr(call, "name", "") or "")

        if is_interrupted():
            responses.append(response_factory(
                id=call_id,
                name=name,
                response={
                    "error": (
                        "Not executed: the user interrupted the current turn "
                        "before this action started."
                    )
                },
            ))
            continue

        responses.append(await execute(call))

    return responses
