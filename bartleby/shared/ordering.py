"""Applying a permutation a drag reported to the list it was dragged from."""


def apply_order(items: list, order: list[int]) -> list | None:
    """*items* rearranged into *order*, or None if *order* does not describe *items*.

    The inverse of what a drag-reordered column emits, and it lives apart from any one of
    its listeners: BartleBot's card columns, its Settings tabs and the client manager all
    take the permutation, and a guard that only some of them apply is no guard.

    Guarded rather than trusted, because the permutation crosses a signal from a column
    that was built out of a list read some time earlier, and either surface — or a delete
    the same gesture raced — can have rewritten it in between. Refusing a stale
    permutation costs the user one drag; applying one silently reorders a list that is not
    the one they dragged, and can drop an account off the end of it.
    """
    if sorted(order) != list(range(len(items))):
        return None
    return [items[index] for index in order]
