"""Album engine: event clustering, photo selection, layout, print validation.

Phase 2 of the build plan. The four stages run in order and each one's mistakes
are inherited by everything after it:

    clustering  -> which photos belong to "the Thailand trip"
    selection   -> which 40 of those 4000 go in the book
    layout      -> where they sit on a physical page, in millimetres
    validator   -> the hard gate before a PDF is sent to a printer

The last of those is the only one whose failure cannot be undone. A book is
printed once, and a validator that passes a bad page produces an object in the
post rather than a bug report -- which is why it fails closed on anything it
cannot measure, including its own inability to measure.

THIS FILE IMPORTS NOTHING, ON PURPOSE.

The first version eagerly imported all four submodules. That immediately broke
the clustering and validator test suites, because `selection` imports the
ranking engine and an eager re-export made every consumer of ANY module depend
on it -- including the validator, which has nothing to do with scoring. An
import in __init__ is a dependency edge whether or not the code has one.

That matters beyond tidiness here: the print validator is the piece most likely
to be lifted into another process (a render worker, a pre-flight check at a
vendor), and it should not drag a taste model along with it.

So import what you need:

    from memory_engine_album import validator
    from memory_engine_album.selection import select

Names are deliberately NOT flattened either. Placement, Frame and Candidate mean
different things at different stages, and one namespace is how a caller ends up
passing a layout Frame to a validator that wanted a placement.
"""

__all__: list[str] = []
