from typing import Any, Callable, Dict, List


class Dispatcher(object):
    """This dispatcher is mostly used to decouple command execution from other 
    concerns like CLI output for file transfers, etc...
    """
    def __init__(self, listeners: Dict[str, List[Callable[..., bool]]] = {}):
        """
        Args:
            listeners (Dict[str, Callable[..., bool]]):
                List of callables taking undefined arguments and returning a
                bool associated to event names.
        """
        self.__listeners = listeners

    def on(self, event_name: str, fn: Callable[..., bool]):
        """Register a listener for a given event.

        Args:
            event_name (str):
                Name of the event the listeners should be attached to.
            fn (Callable[[Any, ...], bool]):
                The event listener that should be triggered for the given
                event.
        """

        if event_name not in self.__listeners:
            self.__listeners[event_name] = []

        self.__listeners[event_name].append(fn)

    def emit(self, event_name: str, **kwargs: Any):
        """Trigger all the event listeners registered for a given event name.

        This dispatcher doesn't support listener priority, so the event
        listeners are called in the order they've been registered.
        Listeners can either inform the Dispatcher to continue the event
        propagation, by returning ``True``, or stop it by returning anything
        else.

        Args:
            event_name (str): Name of the event.
            **kwargs: Any arguments associated with the event.
        """

        if event_name not in self.__listeners:
            return

        for fn in self.__listeners[event_name]:
            if not fn(**kwargs):
                return
